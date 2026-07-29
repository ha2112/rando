"""
client.py
=========
rsync-based transport layer for the rm-rebuilder pipeline.

Replaces the former paramiko/scp implementation entirely.  All remote
communication is delegated to the system ``rsync`` binary, which resolves
SSH aliases from ``~/.ssh/config`` automatically.

Two classes
-----------
* :class:`RsyncClient`    — low-level rsync wrapper around ``subprocess``.
* :class:`FileDownloader` — high-level document-level sync (selective mode).

Sync modes
----------
Full sync
    A single ``rsync`` of the entire ``xochitl/`` directory into the cache.
    Fastest for first-time mirrors or "sync everything" workflows.  Invoked
    via :meth:`RsyncClient.full_sync`.

Selective sync
    Per-document rsync calls for only the UUIDs requested.  Faster for
    targeted refreshes when the full library is large.  Invoked via
    :meth:`FileDownloader.download_all`.

SSH alias convention
--------------------
Both modes expect SSH aliases in ``~/.ssh/config`` that match the strings
stored in ``config.PROFILES``.  The POC used ``remarkable-{mode}``; whatever
names are in PROFILES work as long as the corresponding ``Host`` entry exists::

    Host remarkable-usb
        HostName 10.11.99.1
        User root
        IdentityFile ~/.ssh/id_rsa_remarkable

Dependencies
------------
``rsync`` must be on ``$PATH`` (standard on macOS and most Linux distros).
No Python networking packages are required.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from config import (
    PROFILE_ORDER,
    PROFILES,
    RM_ROOT,
)

log = logging.getLogger("rm-rebuilder.client")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_alias(mode: Optional[str]) -> str:
    """Map a profile *mode* name to the SSH alias string from ``config.PROFILES``.

    If *mode* is ``None``, returns the first alias in ``config.PROFILE_ORDER``
    (auto-order fallback).

    Args:
        mode: Profile key (e.g. ``"usb"``, ``"home"``, ``"hotspot"``), or
              ``None`` to use the first configured profile.

    Returns:
        SSH alias string (e.g. ``"remarkable-usb"``).

    Raises:
        ValueError: When *mode* is given but not found in ``config.PROFILES``.
    """
    if mode is None:
        return PROFILES[PROFILE_ORDER[0]]

    if mode not in PROFILES:
        raise ValueError(
            f"Unknown profile {mode!r}. "
            f"Valid options: {sorted(PROFILES)}"
        )
    return PROFILES[mode]


# ---------------------------------------------------------------------------
# RsyncClient
# ---------------------------------------------------------------------------

class RsyncClient:
    """Low-level rsync wrapper that delegates all I/O to the system binary.

    Every transfer ultimately calls :meth:`_rsync`, which builds a
    ``subprocess`` command and streams rsync's output to the terminal.

    Attributes:
        cache_dir: Root of the persistent local cache (``.RM_FILES/``).
                   All downloaded files land here in a flat xochitl-mirror
                   layout.
        alias:     SSH alias to prefix remote paths (e.g. ``remarkable-usb``).

    Example::

        client = RsyncClient(cache_dir=Path(".RM_FILES"), alias="remarkable-usb")
        client.full_sync()                          # mirror all of xochitl/
        # — or —
        client.selective_sync(["uuid1", "uuid2"])   # only specific docs
    """

    def __init__(self, cache_dir: Path, alias: str) -> None:
        self.cache_dir = cache_dir
        self.alias = alias
        cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core primitive
    # ------------------------------------------------------------------

    def _rsync(
        self,
        src: str,
        dst: Path,
        *,
        extra_flags: Sequence[str] = (),
        optional: bool = False,
    ) -> bool:
        """Execute a single rsync transfer.

        Constructs::

            rsync -avz --progress [extra_flags] <src> <dst>

        Args:
            src:         Full rsync source (``alias:remote/path`` or local path).
            dst:         Local destination directory or file path.
            extra_flags: Any additional rsync flags (e.g. ``["--delete"]``).
            optional:    If ``True``, a non-zero exit code is silently swallowed
                         and ``False`` is returned.  Use for files that may be
                         absent on the tablet (e.g. ``.pdf``, ``.pagedata``).

        Returns:
            ``True`` on success, ``False`` when *optional* is ``True`` and the
            transfer fails.

        Raises:
            subprocess.CalledProcessError: When *optional* is ``False`` and
                rsync exits non-zero.
        """
        cmd: List[str] = [
            "rsync",
            "-avz",
            "--progress",
            *extra_flags,
            src,
            str(dst),
        ]
        log.info("  rsync %s → %s", src, dst)
        log.debug("  full cmd: %s", " ".join(cmd))

        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as exc:
            if optional:
                log.debug("Skipped (absent or transfer error): %s — exit %d", src, exc.returncode)
                return False
            raise

    def _remote(self, remote_path: str) -> str:
        """Format ``alias:remote_path`` for use as an rsync source.

        Args:
            remote_path: Absolute path on the reMarkable filesystem.

        Returns:
            rsync-compatible remote URI string.
        """
        return f"{self.alias}:{remote_path}"

    # ------------------------------------------------------------------
    # Sync modes
    # ------------------------------------------------------------------

    def full_sync(self) -> None:
        """Mirror the entire ``xochitl/`` directory into :attr:`cache_dir`.

        Uses a **trailing slash** on the source path so rsync copies the
        *contents* of ``xochitl/`` directly into ``cache_dir/``, preserving
        the flat UUID layout that :class:`~parser.DocumentParser` expects::

            .RM_FILES/
            ├── <uuid>.metadata
            ├── <uuid>.content
            ├── <uuid>.pdf
            └── <uuid>/
                ├── <page-uuid>.rm
                └── …

        The ``--delete`` flag removes locally cached files that no longer
        exist on the tablet (deleted documents), keeping the cache a true
        mirror.

        Note:
            This downloads the entire library.  For large libraries (100 +
            documents) the first run may take a while; subsequent runs are
            incremental.
        """
        # Trailing slash on source = copy *contents*, not the directory itself
        src = self._remote(RM_ROOT + "/")
        log.info("Full sync: %s → %s", src, self.cache_dir)
        self._rsync(src, self.cache_dir, extra_flags=["--delete", "--exclude=.rm_render_state.json*"])
        log.info("Full sync complete.")

    def selective_sync(self, uuids: List[str]) -> None:
        """Download only the assets required for the given document UUIDs.

        For each UUID, fetches:

        * ``<uuid>.metadata``  (required — raises on missing)
        * ``<uuid>.content``   (required — raises on missing)
        * ``<uuid>.pdf``       (optional)
        * ``<uuid>.pagedata``  (optional)
        * ``<uuid>.bookmarks`` (optional)
        * ``<uuid>/``          (required — stroke ``.rm`` files)

        The ``--ignore-existing`` flag skips files already in the local cache
        unless you want a forced refresh (call :meth:`full_sync` for that).

        Args:
            uuids: List of document UUID strings to download.
        """
        log.info("Selective sync: %d document(s).", len(uuids))
        for uuid in uuids:
            log.info("  Syncing document %s …", uuid)
            self._sync_document(uuid)

    # ------------------------------------------------------------------
    # Parent metadata (for resolve_folder_path)
    # ------------------------------------------------------------------

    def fetch_parent_metadata(self, parent_uuid: str) -> bool:
        """Fetch a single ``.metadata`` file for folder-path resolution.

        :func:`~main.resolve_folder_path` walks the parent chain stored in
        ``DocumentMeta.parent``.  In selective-sync mode, ancestor folder
        metadata may not have been downloaded.  This method retrieves it on
        demand.

        Args:
            parent_uuid: UUID of the parent folder whose metadata is needed.

        Returns:
            ``True`` if the file was successfully downloaded, ``False`` if it
            is absent on the tablet (e.g. orphaned document).
        """
        remote = self._remote(f"{RM_ROOT}/{parent_uuid}.metadata")
        dst    = self.cache_dir / f"{parent_uuid}.metadata"
        if dst.exists():
            log.debug("Parent metadata already cached: %s", parent_uuid)
            return True
        log.debug("Fetching parent metadata on demand: %s", parent_uuid)
        return self._rsync(remote, self.cache_dir, optional=True)

    # ------------------------------------------------------------------
    # Private document sync helper
    # ------------------------------------------------------------------

    def _sync_document(self, uuid: str) -> None:
        """Sync all assets for a single document UUID.

        Sidecar files are synced first (flat into :attr:`cache_dir`), then the
        stroke directory.

        Note on rsync directory layout:
            ``rsync alias:xochitl/uuid  .RM_FILES/``  (no trailing slash on
            source) copies the *directory* ``uuid/`` into ``.RM_FILES/``,
            creating ``.RM_FILES/uuid/``.  This is intentional and matches the
            flat-cache layout required by :class:`~parser.DocumentParser`.

        Args:
            uuid: Document UUID to sync.

        Raises:
            subprocess.CalledProcessError: If a required file (``.metadata``,
                ``.content``) is missing on the tablet.
        """
        base = f"{RM_ROOT}/{uuid}"

        # ── Sidecar files ────────────────────────────────────────────────────
        sidecar_specs: List[tuple[str, bool]] = [
            (f"{base}.metadata",  True),   # required
            (f"{base}.content",   True),   # required
            (f"{base}.pdf",       False),  # optional — absent for pure notebooks
            (f"{base}.pagedata",  False),  # optional
            (f"{base}.bookmarks", False),  # optional
        ]

        for remote_path, required in sidecar_specs:
            self._rsync(
                self._remote(remote_path),
                self.cache_dir,
                optional=not required,
            )

        # ── Stroke directory (uuid/ → .RM_FILES/uuid/) ───────────────────────
        # No trailing slash on source: rsync copies the directory *itself*,
        # so .RM_FILES/uuid/ is created automatically.
        self._rsync(
            self._remote(base),
            self.cache_dir,
        )


# ---------------------------------------------------------------------------
# FileDownloader — convenience alias kept for import compatibility
# ---------------------------------------------------------------------------

class FileDownloader:
    """High-level selective-sync wrapper.

    Thin façade over :class:`RsyncClient` that mirrors the original interface
    so existing call-sites in ``main.py`` can stay mostly unchanged.

    Args:
        client:   A configured :class:`RsyncClient` instance.
        uuids:    Document UUIDs to download.
    """

    def __init__(self, client: RsyncClient, uuids: List[str]) -> None:
        self.client = client
        self.uuids  = uuids

    def download_all(self) -> None:
        """Delegate to :meth:`RsyncClient.selective_sync`."""
        self.client.selective_sync(self.uuids)