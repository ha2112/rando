"""
main.py
=======
Orchestrates download → parse → render → save for reMarkable documents.

Usage:
    python main.py <uuid1> [<uuid2> ...]

Optional flags:
    --profile  usb|home|hotspot   SSH profile to try (default: auto-order)
    --output   DIR                Base output directory (default: DONE_DIR)
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import List, Optional

import fitz

from client import FileDownloader, RMClient
from config import DONE_DIR, RM_ROOT, UUIDS
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
# Helpers
# ---------------------------------------------------------------------------

def resolve_folder_path(
    doc_uuid: str,
    work_dir: Path,
    client: RMClient,
) -> Path:
    """
    Walk the parent chain to build a human-readable folder path.

    Downloads missing `.metadata` files on demand.

    Returns:
        Path: Relative folder path (e.g., "Work/Notes") or "." if root.
    """
    meta_file = work_dir / f"{doc_uuid}.metadata"

    with meta_file.open(encoding="utf-8") as fh:
        parent: Optional[str] = json.load(fh).get("parent") or None

    parts: List[str] = []
    visited: set[str] = set()
    current = parent

    while current and current not in visited:
        visited.add(current)
        meta_path = work_dir / f"{current}.metadata"

        if not meta_path.exists():
            remote_path = f"{RM_ROOT}/{current}.metadata"
            if client.file_exists(remote_path):
                client.download(remote_path, str(meta_path))
            else:
                log.warning("Missing parent metadata on device: %s", current)
                break

        with meta_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)

        parts.append(raw.get("visibleName", current))
        current = raw.get("parent") or None

    parts.reverse()
    return Path(*parts) if parts else Path(".")


def make_stroke_provider(
    processor: StrokeProcessor,
    work_dir: Path,
    doc_uuid: str,
):
    """
    Create a stroke provider callback for PDFRenderer.

    Returns:
        Callable: (PageInfo) -> (strokes, highlights)
    """
    def provider(page_info: PageInfo):
        rm_path = work_dir / doc_uuid / f"{page_info.uuid}.rm"
        return processor.decode_rm_file(rm_path)

    return provider


# ---------------------------------------------------------------------------
# Document Processing
# ---------------------------------------------------------------------------

def _process_document(
    doc_uuid: str,
    work_dir: Path,
    client: RMClient,
    output_base: Path,
) -> None:
    """
    Parse, render, and save a single document.
    """
    log.info("Processing %s …", doc_uuid)

    # -- Parse ---------------------------------------------------------------
    parser = DocumentParser(work_dir, doc_uuid)
    meta = parser.parse_metadata()
    pages = parser.parse_pages()

    log.info(
        "  '%s' — %d page(s), has_pdf=%s",
        meta.name,
        len(pages),
        meta.has_pdf,
    )

    # -- Base PDF (optional) -------------------------------------------------
    base_doc: Optional[fitz.Document] = None
    if meta.has_pdf:
        base_doc = fitz.open(str(work_dir / f"{doc_uuid}.pdf"))

    # -- Output path ---------------------------------------------------------
    folder = resolve_folder_path(doc_uuid, work_dir, client)
    out_dir = output_base / folder
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = meta.name.replace("/", "_").replace("\\", "_")
    out_path = out_dir / f"{safe_name}.pdf"

    # -- Render --------------------------------------------------------------
    processor = StrokeProcessor()
    renderer = PDFRenderer()

    try:
        renderer.build_document(
            pages=pages,
            base_doc=base_doc,
            processor=processor,
            stroke_provider=make_stroke_provider(processor, work_dir, doc_uuid),
        )
        renderer.save(out_path)
        log.info("  Saved → %s", out_path)

    finally:
        renderer.close()
        if base_doc:
            base_doc.close()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    uuids = UUIDS
    output_base = DONE_DIR
    output_base.mkdir(parents=True, exist_ok=True)

    # -- Connect -------------------------------------------------------------
    rm_client = RMClient()
    ssh, _sftp, alias = rm_client.connect(profile=None)
    log.info("Connected via: %s", alias)

    try:
        with tempfile.TemporaryDirectory(prefix="rm-rebuild-") as tmp_dir:
            work_dir = Path(tmp_dir)

            # -- Download ----------------------------------------------------
            downloader = FileDownloader(rm_client, work_dir, uuids)
            downloader.download_all()

            # -- Process documents ------------------------------------------
            for doc_uuid in uuids:
                try:
                    _process_document(
                        doc_uuid,
                        work_dir,
                        rm_client,
                        output_base,
                    )
                except Exception as exc:
                    log.error(
                        "Failed to process %s: %s",
                        doc_uuid,
                        exc,
                        exc_info=True,
                    )

    finally:
        rm_client.disconnect()


if __name__ == "__main__":
    main()