#!/opt/anaconda3/bin/python -u "/Users/academicweapon/Documents/CodingTypeShii/Repos/rando/remarkable-download/workflow/main.py" --sync full --render full --force &> "/Users/academicweapon/Documents/CodingTypeShii/Repos/rando/remarkable-download/workflow/output.log"

"""
main.py
=======
Orchestrates sync → parse → render → save for reMarkable documents.

Usage
-----
Full sync (default) — mirror the entire xochitl/ library, then render requested UUIDs::

    python main.py <uuid1> [<uuid2> …] --mode usb
    python main.py <uuid1> [<uuid2> …] --mode home --output ~/Desktop/rm-out

Selective sync — download and render specific documents::

    python main.py <uuid1> [<uuid2> …] --mode usb --sync selective
    python main.py --sync selective --mode hotspot          # sync only, no render

Flags
-----
``--mode``      usb | home | hotspot  (SSH alias profile; auto-detects via PROFILE_ORDER when omitted)
``--sync``      selective | full      (sync strategy; default: full)
``--render``    selective | full      (render scope; default: selective)
``--output``    DIR                   (output base directory; default: DONE_DIR from config)
``--cache``     DIR                   (persistent cache root; default: .RM_FILES)
``--no-sync``   skip the rsync step entirely (render from cache as-is)
``--force``     re-render even if source files have not changed

Persistent cache
----------------
Downloaded files are stored in a persistent ``.RM_FILES/`` directory that
mirrors the flat xochitl layout expected by DocumentParser::

    .RM_FILES/
    ├── <uuid>.metadata
    ├── <uuid>.content
    ├── <uuid>.pdf           ← absent for pure notebooks
    └── <uuid>/
        ├── <page-uuid>.rm
        └── …

This replaces the old ``tempfile.TemporaryDirectory`` approach.  Subsequent
runs reuse cached files; rsync's incremental transfer handles updates.

Render state
------------
A JSON manifest ``.RM_FILES/.rm_render_state.json`` maps each UUID to its last
known output path and the modification times of every source file that feeds the
render.  The pipeline uses this to:

* **Skip unchanged documents** — only re-render when at least one source file
  has been modified since the last run.
* **Clean up renamed documents** — delete the stale PDF when a document is
  renamed on the tablet.
* **Move trashed documents** — when ``parent == "trash"`` is detected in a
  document's ``.metadata``, the previously rendered PDF is moved into
  ``<output>/.TRASH/`` instead of being silently abandoned.

Pass ``--force`` to bypass change detection and re-render every document
unconditionally.  Explicitly supplying UUIDs on the command line also forces
those specific documents to re-render regardless of state.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import fitz

from client import FileDownloader, RsyncClient, resolve_alias
from config import _BASE_DIR, DONE_DIR, PROFILE_ORDER, PROFILES, RM_ROOT, UUIDS
from models import PageInfo
from parser import DocumentParser, StrokeProcessor
from renderer import PDFRenderer


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rm-rebuilder.main")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR = _BASE_DIR / ".RM_FILES"


# ---------------------------------------------------------------------------
# SSH profile auto-detection helpers
# ---------------------------------------------------------------------------

def _probe_ssh_alias(alias: str, timeout: int = 5) -> bool:
    """Return ``True`` when the SSH alias is reachable.

    Runs a lightweight ``ssh … echo ok`` with ``BatchMode=yes`` so no
    interactive prompt blocks the process.  Any failure — network timeout,
    unknown host, missing key, ``ssh`` binary absent — is caught and treated
    as *unreachable* rather than propagated.

    Args:
        alias:   SSH config alias (e.g. ``"remarkable-hotspot"``).
        timeout: Seconds to wait for the connection before giving up.
                 Passed both as the SSH ``ConnectTimeout`` option and as the
                 ``subprocess.run`` watchdog (with a 2-second margin).

    Returns:
        ``True`` if the remote shell echoed back successfully, ``False``
        on any failure.
    """
    cmd = [
        "ssh",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        alias,
        "echo ok",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _resolve_profile_auto(
    mode: Optional[str],
    profiles: dict,
    profile_order: list,
) -> str:
    """Return an SSH alias, auto-detecting the best profile when *mode* is ``None``.

    When *mode* is explicitly provided (i.e. the user passed ``--mode``),
    :func:`~client.resolve_alias` is called directly — identical to the
    previous behaviour.

    When *mode* is ``None``, each entry in *profile_order* is probed via
    :func:`_probe_ssh_alias` in sequence.  The alias for the **first
    reachable** profile is returned immediately.  If no profile responds,
    a :exc:`ConnectionError` is raised listing all attempted profiles.

    Args:
        mode:          CLI ``--mode`` value, or ``None`` for auto-detect.
        profiles:      ``PROFILES`` dict mapping mode keys → SSH alias strings.
        profile_order: ``PROFILE_ORDER`` list defining probe sequence.

    Returns:
        SSH alias string (e.g. ``"remarkable-hotspot"``).

    Raises:
        ConnectionError: When *mode* is ``None`` and no profile is reachable.
    """
    if mode is not None:
        # Explicit mode — delegate directly, no probing
        return resolve_alias(mode)

    log.info(
        "No --mode given; probing %d profile(s) in order: %s",
        len(profile_order),
        ", ".join(profile_order),
    )

    for profile_name in profile_order:
        alias = profiles.get(profile_name, profile_name)
        log.info("  ⟳  Trying profile '%s' → %s …", profile_name, alias)
        if _probe_ssh_alias(alias):
            log.info("  ✔  Reachable: '%s' (%s)", profile_name, alias)
            return alias
        log.info("  ✘  Unreachable: '%s'", profile_name)

    tried = ", ".join(f"'{p}'" for p in profile_order)
    raise ConnectionError(
        f"No reachable SSH profile found after trying {tried}. "
        "Check that the tablet is powered on and connected, or pass --mode explicitly."
    )


# ---------------------------------------------------------------------------
# Render state tracker
# ---------------------------------------------------------------------------

class RenderStateTracker:
    """Tracks rendered document state for change detection and trash handling.

    Persists a JSON manifest at ``cache_dir/.rm_render_state.json`` that maps
    each UUID to its last-known output path and source-file modification times.
    The manifest is written atomically (tmp → replace) so a crash mid-save
    never leaves a corrupt file.

    Change detection
    ~~~~~~~~~~~~~~~~
    Before rendering, :meth:`needs_render` compares the stored ``file_mtimes``
    snapshot against the current mtimes of::

        <uuid>.metadata   ← name / parent / type
        <uuid>.content    ← page structure
        <uuid>.pdf        ← base PDF (may be absent)
        <uuid>.pagedata   ← template info (may be absent)
        <uuid>/           ← directory mtime, changes whenever any .rm file changes

    Any difference (new file, deleted file, changed mtime) triggers a re-render.

    Trash handling
    ~~~~~~~~~~~~~~
    When ``parent == "trash"`` is detected in a document's ``.metadata``, the
    previously rendered PDF (looked up from the manifest) is moved to
    ``output_base/.TRASH/``.  A short UUID prefix is prepended to the filename
    if a name collision already exists in ``.TRASH/``.

    Rename handling
    ~~~~~~~~~~~~~~~
    When :meth:`handle_rename_if_needed` is called before writing a new render,
    if the stored output path differs from the new target path and the old file
    still exists, it is deleted so the output directory does not accumulate
    stale copies.

    Attributes:
        cache_dir:   Root of the local file cache (``.RM_FILES/``).
        output_base: Base directory where rendered PDFs are written.
    """

    STATE_FILENAME = ".rm_render_state.json"

    def __init__(self, cache_dir: Path, output_base: Path) -> None:
        self.cache_dir   = cache_dir
        self.output_base = output_base
        self.state_file  = cache_dir / self.STATE_FILENAME
        self._state: Dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, dict]:
        """Load the manifest from disk, returning an empty dict on any failure."""
        if self.state_file.exists():
            try:
                with self.state_file.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
                log.warning(
                    "Render state file has unexpected format — starting fresh."
                )
            except Exception as exc:
                log.warning(
                    "Could not load render state from %s (%s) — starting fresh.",
                    self.state_file, exc,
                )
        return {}

    def save(self) -> None:
        """Persist the manifest to disk atomically (write to tmp, then replace).

        Safe to call even on partial failure — an incomplete write is discarded
        and the previous manifest is preserved.
        """
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
            tmp.replace(self.state_file)
        except Exception as exc:
            log.error("Failed to save render state: %s", exc)
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Fingerprinting
    # ------------------------------------------------------------------

    def _fingerprint(self, uuid: str) -> Dict[str, float]:
        """Return ``{filename: mtime}`` for every relevant source file.

        Files that do not exist are omitted, so their appearance or disappearance
        is detected as a change on the next comparison.

        Args:
            uuid: Document UUID.

        Returns:
            Dict mapping filename stem+ext (or ``uuid/``) to ``st_mtime``.
        """
        mtimes: Dict[str, float] = {}

        for ext in (".metadata", ".content", ".pdf", ".pagedata"):
            p = self.cache_dir / f"{uuid}{ext}"
            if p.exists():
                mtimes[f"{uuid}{ext}"] = p.stat().st_mtime

        # Directory mtime changes whenever any .rm annotation file inside
        # is added, removed, or modified — one stat covers the whole page set.
        d = self.cache_dir / uuid
        if d.exists():
            mtimes[f"{uuid}/"] = d.stat().st_mtime

        return mtimes

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def needs_render(self, uuid: str) -> bool:
        """Return ``True`` when source files have changed since the last render.

        A UUID that has never been rendered (not in the manifest) always
        returns ``True``.

        Args:
            uuid: Document UUID to check.

        Returns:
            ``True``  — render needed (new, modified, or never rendered).
            ``False`` — all source files match the last recorded render.
        """
        entry = self._state.get(uuid)
        if entry is None:
            return True  # never rendered
        stored  = entry.get("file_mtimes", {})
        current = self._fingerprint(uuid)
        changed = stored != current
        if changed:
            log.debug(
                "Change detected for %s.\n  stored:  %s\n  current: %s",
                uuid, stored, current,
            )
        return changed

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def record_rendered(self, uuid: str, output_path: Path) -> None:
        """Store a successful render record for *uuid*.

        Captures the current file fingerprint so subsequent calls to
        :meth:`needs_render` can detect future changes.

        Args:
            uuid:        Document UUID.
            output_path: Absolute path to the written PDF.
        """
        self._state[uuid] = {
            "output_path": str(output_path),
            "file_mtimes": self._fingerprint(uuid),
        }

    def get_output_path(self, uuid: str) -> Optional[Path]:
        """Return the last known output path for *uuid*, or ``None``.

        Args:
            uuid: Document UUID.

        Returns:
            :class:`~pathlib.Path` if recorded, otherwise ``None``.
        """
        entry = self._state.get(uuid)
        if entry and "output_path" in entry:
            return Path(entry["output_path"])
        return None

    # ------------------------------------------------------------------
    # Rename handling
    # ------------------------------------------------------------------

    def handle_rename_if_needed(self, uuid: str, new_output_path: Path) -> None:
        """Delete the stale rendered PDF when a document has been renamed.

        Called *before* rendering so the old file is gone before the new one
        is written at the updated path.  No-ops when the path has not changed
        or when the old file no longer exists on disk.

        Args:
            uuid:            Document UUID.
            new_output_path: Absolute path where the new render will be saved.
        """
        old_path = self.get_output_path(uuid)
        if old_path and old_path != new_output_path and old_path.exists():
            old_path.unlink()
            log.info(
                "  📝  Removed stale output after rename: '%s' → '%s'",
                old_path.name, new_output_path.name,
            )

    # ------------------------------------------------------------------
    # Trash handling
    # ------------------------------------------------------------------

    def handle_trash(self, uuid: str, visible_name: str) -> bool:
        """Move a previously rendered PDF to ``<output>/.TRASH/``.

        If the UUID has a recorded output path and that file still exists on
        disk, it is moved into ``output_base/.TRASH/``.  When a filename
        collision exists in ``.TRASH/``, a short UUID prefix is prepended to
        the destination name to avoid silent overwrites.

        The UUID is removed from the manifest regardless of whether a file was
        physically moved, preventing re-rendering of trashed documents in
        subsequent runs.

        Args:
            uuid:         Document UUID.
            visible_name: Human-readable document name (for log messages).

        Returns:
            ``True`` if a PDF was physically moved, ``False`` otherwise.
        """
        old_path = self.get_output_path(uuid)
        moved = False

        # Fallback recovery if manifest entry is missing/stale
        if old_path is None or not old_path.exists():
            safe_name = visible_name.replace("/", "_").replace("\\", "_")
            candidates = list(self.output_base.rglob(f"{safe_name}.pdf"))

            if candidates:
                old_path = candidates[0]

        if old_path and old_path.exists():
            trash_dir = self.output_base / ".TRASH"
            trash_dir.mkdir(parents=True, exist_ok=True)

            dest = trash_dir / old_path.name
            if dest.exists():
                # Disambiguate with short UUID prefix to avoid silent overwrites
                dest = trash_dir / f"{uuid[:8]}_{old_path.name}"

            old_path.rename(dest)
            log.info("  🗑️   Trashed '%s' → %s", visible_name, dest)
            moved = True

        # Always evict from manifest — do not re-render trashed documents
        self._state.pop(uuid, None)
        return moved

    # ------------------------------------------------------------------
    # Full trash scan
    # ------------------------------------------------------------------

    def scan_and_move_trash(self) -> int:
        """Scan every cached ``.metadata`` file for trashed documents.

        For each document whose ``parent`` field equals ``"trash"``:

        * If a rendered PDF was previously recorded in the manifest, it is
          moved to ``output_base/.TRASH/`` via :meth:`handle_trash`.
        * The UUID is removed from the manifest so it will not be rendered
          in future runs.

        This should be called **after** the rsync step so the cache reflects
        the current tablet state (trashed metadata files are synced with
        ``"parent": "trash"`` set by the firmware).

        Returns:
            Number of rendered PDFs physically moved to ``.TRASH/``.
        """
        moved_count = 0

        for meta_path in sorted(self.cache_dir.glob("*.metadata")):
            uuid = meta_path.stem
            try:
                with meta_path.open(encoding="utf-8") as fh:
                    raw = json.load(fh)
            except Exception as exc:
                log.debug(
                    "Could not read %s during trash scan: %s",
                    meta_path.name, exc,
                )
                continue

            if raw.get("parent") != "trash":
                continue

            visible_name = raw.get("visibleName", uuid)
            if self.handle_trash(uuid, visible_name):
                moved_count += 1
            else:
                # In trash but never rendered — evict from manifest if present
                self._state.pop(uuid, None)

        if moved_count:
            log.info("Trash scan: moved %d document(s) to .TRASH/", moved_count)
        else:
            log.debug("Trash scan: no trashed documents with rendered outputs found.")

        return moved_count

    # ------------------------------------------------------------------
    # Trash purge (--force)
    # ------------------------------------------------------------------

    def purge_trash(self) -> int:
        """Permanently delete every file inside ``<output>/.TRASH/``.

        Called at the start of a ``--force`` run so that documents which were
        previously trashed on the tablet do not accumulate indefinitely.  The
        ``.TRASH/`` directory itself is left in place (it is recreated on demand
        by :meth:`handle_trash` anyway, but keeping it avoids a redundant mkdir
        on the same run).

        Only regular files are removed; sub-directories inside ``.TRASH/`` are
        left untouched to avoid accidentally deleting nested structure that a
        user may have created manually.  Each deletion is attempted
        individually so a single permission error does not abort the whole
        purge.

        Returns:
            Number of files successfully deleted.
        """
        trash_dir = self.output_base / ".TRASH"

        if not trash_dir.exists():
            log.debug("Trash purge: .TRASH/ does not exist — nothing to do.")
            return 0

        deleted = 0
        for item in sorted(trash_dir.iterdir()):
            if not item.is_file():
                log.debug("Trash purge: skipping non-file entry %s", item.name)
                continue
            try:
                item.unlink()
                log.info("  🗑\ufe0f   Purged from .TRASH/: %s", item.name)
                deleted += 1
            except OSError as exc:
                log.warning("Trash purge: could not delete %s — %s", item.name, exc)

        if deleted:
            log.info("Trash purge: permanently deleted %d file(s) from .TRASH/", deleted)
        else:
            log.debug("Trash purge: .TRASH/ was already empty.")

        return deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_folder_path(
    doc_uuid: str,
    cache_dir: Path,
    client: Optional[RsyncClient] = None,
) -> Path:
    """Walk the parent-UUID chain to build a human-readable output folder path.

    Reads ``<uuid>.metadata`` files from the local cache.  When a parent's
    metadata is absent and a connected :class:`~client.RsyncClient` is
    provided, it is fetched on demand via
    :meth:`~client.RsyncClient.fetch_parent_metadata`.

    This function works in both sync modes:

    * **Full sync** — all metadata is already in the cache; no on-demand
      fetching needed.
    * **Selective sync** — only the requested documents' files were downloaded.
      Ancestor folder metadata (needed for the output path) is fetched lazily.

    Args:
        doc_uuid:  UUID of the document being rendered.
        cache_dir: Root of the local cache (mirrors xochitl/ layout).
        client:    Optional :class:`~client.RsyncClient` for on-demand
                   metadata fetching.  Pass ``None`` when running with
                   ``--no-sync`` to skip fetching.

    Returns:
        Relative :class:`~pathlib.Path` of the output folder
        (e.g. ``Path("Work/Notes")``), or ``Path(".")`` for root-level docs.
    """
    meta_file = cache_dir / f"{doc_uuid}.metadata"
    if not meta_file.exists():
        log.warning("Metadata not found in cache for %s — outputting to root.", doc_uuid)
        return Path(".")

    with meta_file.open(encoding="utf-8") as fh:
        parent: Optional[str] = json.load(fh).get("parent") or None

    parts: List[str] = []
    visited: set = set()
    current = parent

    while current and current not in visited:
        if current == "trash":
            # Special case: parent is "trash". Place in .TRASH folder and stop climbing further.
            parts.append(".TRASH")
            break

        visited.add(current)
        parent_meta = cache_dir / f"{current}.metadata"

        # On-demand fetch for selective-sync mode
        if not parent_meta.exists():
            if client is not None:
                fetched = client.fetch_parent_metadata(current)
                if not fetched:
                    log.warning("Parent metadata unavailable on tablet: %s", current)
                    break
            else:
                log.warning(
                    "Parent metadata %s not in cache and no client available — "
                    "truncating folder path.",
                    current,
                )
                break

        with parent_meta.open(encoding="utf-8") as fh:
            raw = json.load(fh)

        folder_name = raw.get("visibleName", current)
        parts.append(folder_name)
        current = raw.get("parent") or None

    parts.reverse()
    return Path(*parts) if parts else Path(".")


def make_stroke_provider(
    processor: StrokeProcessor,
    cache_dir: Path,
    doc_uuid: str,
):
    """Create a stroke-provider callback for :class:`~renderer.PDFRenderer`.

    Args:
        processor: Shared :class:`~parser.StrokeProcessor` instance.
        cache_dir: Root of the local cache.
        doc_uuid:  Document UUID (used to locate ``uuid/<page-uuid>.rm``).

    Returns:
        Callable ``(PageInfo) → (strokes, highlights)``.
    """
    def provider(page_info: PageInfo):
        rm_path = cache_dir / doc_uuid / f"{page_info.uuid}.rm"
        return processor.decode_rm_file(rm_path)

    return provider


# ---------------------------------------------------------------------------
# Cache discovery
# ---------------------------------------------------------------------------

def discover_cached_uuids(cache_dir: Path) -> List[str]:
    """Return the UUIDs of every renderable document found in *cache_dir*.

    A UUID is considered renderable when **both** ``<uuid>.metadata`` and
    ``<uuid>.content`` exist in the cache root.  Folder entries
    (``type == "CollectionType"``) and trashed documents
    (``parent == "trash"``) are filtered out.

    Args:
        cache_dir: Root of the local cache (flat xochitl mirror).

    Returns:
        Sorted list of UUID strings, one per renderable document.
    """
    uuids: List[str] = []

    for meta_path in sorted(cache_dir.glob("*.metadata")):
        uuid = meta_path.stem

        # Both sidecar files must be present
        if not (cache_dir / f"{uuid}.content").exists():
            log.debug("Skipping %s — missing .content file.", uuid)
            continue

        # Filter on metadata fields (single read covers all checks)
        try:
            with meta_path.open(encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception as exc:
            log.warning("Could not read %s: %s — skipping.", meta_path.name, exc)
            continue

        # Skip folder entries (CollectionType)
        if meta.get("type", "DocumentType") != "DocumentType":
            log.debug("Skipping %s — type is %r.", uuid, meta.get("type"))
            continue

        # Skip documents in the reMarkable trash
        if meta.get("parent") == "trash":
            log.debug("Skipping %s (%r) — in trash.", uuid, meta.get("visibleName", uuid))
            continue

        uuids.append(uuid)

    log.info("Cache discovery: %d renderable document(s) found.", len(uuids))
    return uuids


# ---------------------------------------------------------------------------
# Document processing
# ---------------------------------------------------------------------------

def _process_document(
    doc_uuid: str,
    cache_dir: Path,
    client: Optional[RsyncClient],
    output_base: Path,
    tracker: Optional[RenderStateTracker] = None,
    force: bool = False,
) -> bool:
    """Parse, render, and save a single document.

    When a :class:`RenderStateTracker` is provided, change detection and
    rename-cleanup are applied automatically.  Pass ``force=True`` to bypass
    change detection and always re-render (equivalent to ``--force`` on the
    CLI for a specific document).

    Args:
        doc_uuid:     Document UUID to render.
        cache_dir:    Root of the local cache (flat xochitl mirror).
        client:       Connected :class:`~client.RsyncClient`, used by
                      :func:`resolve_folder_path` for on-demand metadata
                      fetching.  May be ``None`` in ``--no-sync`` mode.
        output_base:  Base directory for rendered PDFs.
        tracker:      Optional :class:`RenderStateTracker`.  When provided,
                      change detection and manifest updates are applied.
        force:        When ``True``, bypass change detection for this document.

    Returns:
        ``True`` if the document was rendered and saved.
        ``False`` if rendering was skipped because no changes were detected.
    """
    log.info("Processing %s …", doc_uuid)

    # ── Parse ────────────────────────────────────────────────────────────────
    parser = DocumentParser(cache_dir, doc_uuid)
    meta   = parser.parse_metadata()
    pages  = parser.parse_pages()

    log.info(
        "  '%s' — %d page(s), has_pdf=%s",
        meta.name,
        len(pages),
        meta.has_pdf,
    )

    # ── Compute output path before change check (needed for rename detection) ─
    folder    = resolve_folder_path(doc_uuid, cache_dir, client)
    out_dir   = output_base / folder
    safe_name = meta.name.replace("/", "_").replace("\\", "_")
    out_path  = out_dir / f"{safe_name}.pdf"

    # ── Change detection ──────────────────────────────────────────────────────
    if tracker is not None and not force:
        if not tracker.needs_render(doc_uuid):
            log.info("  ⏭️   Skipping '%s' — no changes detected.", meta.name)
            return False

    # ── Rename cleanup ────────────────────────────────────────────────────────
    if tracker is not None:
        tracker.handle_rename_if_needed(doc_uuid, out_path)

    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Base PDF (optional) ──────────────────────────────────────────────────
    base_doc: Optional[fitz.Document] = None
    if meta.has_pdf:
        base_doc = fitz.open(str(cache_dir / f"{doc_uuid}.pdf"))

    # ── Render ───────────────────────────────────────────────────────────────
    processor = StrokeProcessor()
    renderer  = PDFRenderer()

    try:
        renderer.build_document(
            pages=pages,
            base_doc=base_doc,
            processor=processor,
            stroke_provider=make_stroke_provider(processor, cache_dir, doc_uuid),
        )
        renderer.save(out_path)
        log.info("  ✅  Saved → %s", out_path)

        # Record the successful render in the manifest
        if tracker is not None:
            tracker.record_rendered(doc_uuid, out_path)

    finally:
        renderer.close()
        if base_doc:
            base_doc.close()

    return True


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download and render reMarkable documents to annotated PDFs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  # Full sync + render specific docs over USB (default mode)
  python main.py uuid1 uuid2 --mode usb

  # Full sync (entire library), then render specific docs
  python main.py uuid1 uuid2 --mode home --sync full

  # Full sync + render every document in the library
  python main.py --mode usb --sync full --render full

  # Full sync only (no rendering)
  python main.py --mode hotspot --sync full

  # Render every cached document without re-syncing
  python main.py --no-sync --render full

  # Re-render specific docs from existing cache (no network)
  python main.py uuid1 uuid2 --no-sync

  # Force re-render all documents, bypassing change detection
  python main.py --no-sync --render full --force
""",
    )

    p.add_argument(
        "uuids",
        nargs="*",
        metavar="UUID",
        help=(
            "Document UUIDs to render. "
            "Falls back to UUIDS list from config.py when omitted. "
            "Explicitly-provided UUIDs always re-render (bypass change detection)."
        ),
    )
    p.add_argument(
        "--mode",
        choices=list(PROFILES.keys()),
        default=None,
        metavar="MODE",
        help=(
            f"SSH profile to use. One of: {', '.join(PROFILES)}. "
            f"When omitted, profiles in PROFILE_ORDER are probed in sequence "
            f"({', '.join(PROFILE_ORDER)}) and the first reachable one is used."
        ),
    )
    p.add_argument(
        "--sync",
        choices=["selective", "full"],
        default="full",
        dest="sync_mode",
        help=(
            "selective: download only requested UUIDs. "
            "full: mirror the entire xochitl/ library first (default)."
        ),
    )
    p.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip rsync entirely — render from the local cache as-is.",
    )
    p.add_argument(
        "--render",
        choices=["selective", "full"],
        default="selective",
        dest="render_mode",
        help=(
            "selective: render only the UUIDs given on the command line / config (default). "
            "full: render every renderable document discovered in the cache."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass change detection and re-render every document unconditionally. "
            "Useful after a config or renderer change that affects all output."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DONE_DIR,
        metavar="DIR",
        help=f"Base output directory for rendered PDFs. Default: {DONE_DIR}",
    )
    p.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        metavar="DIR",
        help=f"Persistent local cache directory. Default: _BASE_DIR/.RM_FILES  ({DEFAULT_CACHE_DIR})",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cache_dir   = args.cache
    output_base = args.output

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_base.mkdir(parents=True, exist_ok=True)

    # ── Seed UUID list from CLI / config (used by selective paths) ───────────
    requested_uuids: List[str] = args.uuids or UUIDS

    # ── Validate: need UUIDs unless we are doing a full sync or full render ──
    needs_uuids = args.sync_mode != "full" and args.render_mode != "full"
    if needs_uuids and not requested_uuids:
        log.error(
            "No UUIDs specified and UUIDS in config.py is empty. "
            "Provide UUIDs, or use --sync full / --render full."
        )
        sys.exit(1)

    # ── Resolve SSH alias (auto-detect when --mode is omitted) ───────────────
    alias: Optional[str] = None
    client: Optional[RsyncClient] = None

    if not args.no_sync:
        try:
            alias = _resolve_profile_auto(args.mode, PROFILES, PROFILE_ORDER)
        except (ValueError, ConnectionError) as exc:
            log.error("%s", exc)
            sys.exit(1)

        client = RsyncClient(cache_dir=cache_dir, alias=alias)
        log.info("Using SSH alias: %s  |  cache: %s", alias, cache_dir)

    # ── Initialise render state tracker ──────────────────────────────────────
    tracker = RenderStateTracker(cache_dir=cache_dir, output_base=output_base)
    log.debug("Render state loaded: %d known document(s).", len(tracker._state))

    # ── Sync ─────────────────────────────────────────────────────────────────
    if not args.no_sync:
        assert client is not None  # for type checker

        if args.sync_mode == "full":
            log.info("=== Full sync ===")
            client.full_sync()
        else:
            log.info("=== Selective sync (%d UUID(s)) ===", len(requested_uuids))
            FileDownloader(client=client, uuids=requested_uuids).download_all()

        # ── Trash scan (after sync so cache reflects current tablet state) ───
        log.info("=== Trash scan ===")
        tracker.scan_and_move_trash()
    else:
        log.info("Skipping sync — rendering from cache: %s", cache_dir)

    # ── Purge .TRASH/ on --force ──────────────────────────────────────────────
    # Runs before render-list resolution so the purge always executes when
    # --force is requested, even if the cache turns out to be empty.
    if args.force:
        log.info("=== Purging .TRASH/ (--force) ===")
        tracker.purge_trash()

    # ── Resolve final render list ─────────────────────────────────────────────
    if args.render_mode == "full":
        log.info("=== Render mode: full (discovering all cached documents) ===")
        render_uuids = discover_cached_uuids(cache_dir)
        if not render_uuids:
            log.warning("No renderable documents found in cache. Run a sync first.")
            tracker.save()
            return
    else:
        render_uuids = requested_uuids

    if not render_uuids:
        log.info("No UUIDs to render (sync-only run). Done.")
        tracker.save()
        return

    # ── Force flag logic ─────────────────────────────────────────────────────
    # Global --force flag overrides all change detection.
    # Individually named UUIDs on the CLI also force-render that specific doc
    # so that `python main.py <uuid>` always refreshes without needing --force.
    explicit_uuids: set = set(args.uuids) if args.uuids else set()

    def _should_force(uuid: str) -> bool:
        return args.force or (uuid in explicit_uuids)

    # ── Render ───────────────────────────────────────────────────────────────
    log.info("=== Rendering %d document(s) ===", len(render_uuids))

    rendered_count = 0
    skipped_count  = 0
    errors: List[tuple] = []   # (visible_name, uuid, exception)

    for doc_uuid in render_uuids:
        # Best-effort name lookup from cached metadata for readable error output
        try:
            meta_path = cache_dir / f"{doc_uuid}.metadata"
            with meta_path.open(encoding="utf-8") as fh:
                doc_name = json.load(fh).get("visibleName", doc_uuid)
        except Exception:
            doc_name = doc_uuid

        try:
            did_render = _process_document(
                doc_uuid    = doc_uuid,
                cache_dir   = cache_dir,
                client      = client,       # None when --no-sync
                output_base = output_base,
                tracker     = tracker,
                force       = _should_force(doc_uuid),
            )
            if did_render:
                rendered_count += 1
            else:
                skipped_count += 1
        except Exception as exc:
            log.error(
                "Failed to process '%s' (%s): %s",
                doc_name, doc_uuid, exc, exc_info=True,
            )
            errors.append((doc_name, doc_uuid, exc))

    # ── Persist manifest (save even on partial failure to keep progress) ─────
    tracker.save()
    log.debug("Render state saved → %s", tracker.state_file)

    # ── Summary ──────────────────────────────────────────────────────────────
    log.info(
        "Render summary: %d rendered, %d skipped (unchanged), %d failed.",
        rendered_count, skipped_count, len(errors),
    )

    if errors:
        log.warning("%d document(s) failed:", len(errors))
        for name, uuid, exc in errors:
            log.warning(
                "  ✗  %s  [%s]\n      %s: %s",
                name, uuid, type(exc).__name__, exc,
            )
        sys.exit(2)

    log.info("All done. Output → %s", output_base)


if __name__ == "__main__":
    main()