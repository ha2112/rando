"""
parser.py
=========
Provides two classes for parsing reMarkable document files into Python objects:

- DocumentParser: Reads `.metadata` and `.content` JSON files, producing
  DocumentMeta and PageInfo instances.

- StrokeProcessor: Decodes `.rm` v6 binary files into lists of Stroke objects,
  and converts device coordinates to PDF coordinates with all necessary transforms.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pymupdf import r

from config import RM_HEIGHT, RM_HALF_WIDTH, RM_WIDTH
from models import DocumentMeta, PageInfo, Stroke, Highlight, Point, Rectangle

log = logging.getLogger("rm-rebuilder.parser")

# ======================================================================================
# Document Parser
# ======================================================================================

class DocumentParser:
    """Parses `.metadata` and `.content` JSON files for a document.

    Attributes:
        work_dir: Path to the local workspace containing downloaded files.
        doc_uuid: Document UUID used for file path construction.

    Methods:
        __init__(work_dir: Path, doc_uuid: str)
            Initializes the parser for a given directory and UUID.

        parse_metadata()
            Returns a DocumentMeta instance parsed from the corresponding `.metadata` file.

        parse_pages()
            Returns a list of PageInfo objects, parsed from the `.content` file in CRDT order.
    """
    def __init__(self, work_dir: Path, doc_uuid: str) -> None:
      self.work_dir = work_dir
      self.doc_uuid = doc_uuid
    

    def parse_metadata(self) -> DocumentMeta:
      """Parse the ``<uuid>.metadata`` file.

      Returns:
          A :class:`~models.DocumentMeta` populated from the JSON.

      Raises:
          FileNotFoundError: If the ``.metadata`` file is absent.
      """
      meta_file = self.work_dir / f"{self.doc_uuid}.metadata"
      if not meta_file.exists():
          raise FileNotFoundError(f"Metadata file not found: {meta_file}")

      with meta_file.open("r", encoding="utf-8") as fh:
          raw: Dict[str, Any] = json.load(fh)

      return DocumentMeta(
          uuid=self.doc_uuid,
          name=raw.get("visibleName", self.doc_uuid),
          doc_type=raw.get("type", "DocumentType"),
          parent=raw.get("parent"),
          version=int(raw.get("version", 0)),
          # has_pdf is determined by whether the .pdf file was downloaded
          has_pdf=(self.work_dir / f"{self.doc_uuid}.pdf").exists(),
      )

    def parse_pages(self) -> List[PageInfo]:
      """ Parse the ``<uuid>.content`` file and return pages in CRDT order.

      Returns:
        Ordered :class:`list` of :class:`~models.PageInfo` objects
      """

      content_file = self.work_dir / f"{self.doc_uuid}.content"
      if not content_file.exists:
        raise FileNotFoundError(f"Content file not found: {content_file}")

      with content_file.open("r", encoding="utf-8") as fh:
        data: Dict[str, Any] = json.load(fh)

      cpages = data.get("cPages", {})
      raw_pages = cpages.get("pages", [])
      
      pages: List[PageInfo] = []
      for i,p in enumerate[Any](raw_pages):
        # Skip deleted pages
        is_deleted = p.get("deleted", False) or p.get("deletedLength", 0) > 0
        if is_deleted:
          log.debug("Skipping deleted page at CRDT index %d (uuid: %s)", i, p.get("id"))
          continue
        
        orientation = data.get("orientation", "portrait")
        redir_raw = self._unwrap(p.get("redir"))
        pages.append(PageInfo(
          page_number=i,  # Keeps original CRDT array index for reference
          uuid=p.get("id", ""),
          redir=self._safe_int(redir_raw),
          template=self._unwrap(p.get("template")),
          modified=self._safe_int(p.get("modifed")),
          orientation=orientation, 
        ))
      
      log.debug("Parsed %d active pages in CRDT order.", len(pages))
      return pages
    
    # =========================================================
    # Helper
    # =========================================================
    @staticmethod
    def _unwrap(value: Any) -> Any:
        """Unwrap a ``{"value": …}`` wrapper dict to its inner value.
        Some fields in the ``.content`` JSON (e.g. ``redir``, ``template``)
        are stored as single-key dicts::
            {"value": 2}   →   2
            "Blank"        →   "Blank"   (pass-through, no wrapping)
        Args:
            value: Raw JSON value that may or may not be wrapped.
        Returns:
            The unwrapped scalar, or *value* unchanged if it is not a wrapper
            dict.
        """
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    @staticmethod
    def _safe_int(x: Any) -> Optional[int]:
        """Cast *x* to ``int``, returning ``None`` on failure.
        Args:
            x: Any value.
        Returns:
            Integer cast of *x*, or ``None`` if the cast raises
            :exc:`TypeError` or :exc:`ValueError`.
        """
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

# ---------------------------------------------------------------------------
# StrokeProcessor
# ---------------------------------------------------------------------------

class StrokeProcessor:
    """Decodes ``.rm`` v6 binary files and transforms stroke / highlight
    coordinates from reMarkable device space to PDF point space.

    Coordinate-system corrections applied
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    **Bug fix #1 — PDF x de-centering:**
        reMarkable v6 stores x coordinates for PDF-annotation pages with
        ``x = 0`` at the horizontal canvas midpoint (702 px from the left
        edge), so raw x values range over ``[-702, +702]``.  Adding
        ``RM_HALF_WIDTH = 702`` before scaling shifts this to ``[0, 1404]``.
        Applied only when ``is_pdf_page=True``.

    **Bug fix #2 — CropBox origin offset:**
        PDFs whose CropBox is inset within a larger MediaBox expose a
        non-zero ``(x_origin_pt, y_origin_pt)`` offset.  After scaling,
        every coordinate is translated by this offset so strokes land inside
        the visible content area rather than at the raw page origin.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decode_rm_file(
        self, rm_path: Path
    ) -> Tuple[List[Stroke], List[Highlight]]:
        """Parse a single ``.rm`` v6 file into strokes and text highlights.

        The top-level loop owns the **CRDT tombstone check** so that it gates
        both ``_parse_stroke`` and ``_parse_highlight`` uniformly — the
        ``temp2.py`` POC omitted this check which would have surfaced deleted
        ink as phantom strokes.

        Args:
            rm_path: Filesystem path to the ``.rm`` binary file.

        Returns:
            ``Tuple[ List[Stroke], List[Highlight] ]``

        Raises:
            ImportError: If the ``rmscene`` package is not installed.
        """

        # General shits
        if not rm_path.exists():
            log.warning("Missing .rm file — treating as blank page: %s", rm_path)
            return [], []

        try:
            import rmscene  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "The 'rmscene' package is required.  Install with:\n"
                "    pip install rmscene"
            )

        try:
            with rm_path.open("rb") as fh:
                raw_blocks = list(rmscene.read_blocks(fh))
        except Exception as exc:
            log.error("Failed to parse %s: %s", rm_path.name, exc)
            return [], []

        # Extract raw block

        strokes:    List[Stroke]    = []
        highlights: List[Highlight] = []

        for block_index, block in enumerate(raw_blocks):
            # ── gate 1: block must expose an item ────────────────────────────
            item = getattr(block, "item", None)
            if item is None:
                log.debug("Block %d has no item — skipping.", block_index)
                continue

            # ── gate 2: CRDT tombstone check (CRITICAL — from parser.py) ─────
            # Firmware v3+ marks deleted CRDT entries with a non-zero
            # ``deleted_length``.  Skipping here prevents deleted ink and
            # deleted highlight annotations from being rendered.
            if getattr(item, "deleted_length", 0) > 0:
                log.debug(
                    "Block %d is a CRDT tombstone (deleted_length > 0) — skipping.",
                    block_index,
                )
                continue

            # ── gate 3: item must carry a value ──────────────────────────────
            val = getattr(item, "value", None)
            if val is None:
                log.debug("Block %d item has no value — skipping.", block_index)
                continue

            # ── dispatch: stroke first, then highlight ────────────────────────
            stroke = self._parse_stroke(val, block_index)
            if stroke is not None:
                strokes.append(stroke)
                continue  # a block is either a stroke *or* a highlight, not both

            highlight = self._parse_highlight(val, block_index)
            if highlight is not None:
                highlights.append(highlight)

        
        log.debug(
            "%s → %d strokes, %d highlights decoded.",
            rm_path.name,
            len(strokes),
            len(highlights),
        )
        return strokes, highlights

    def scale_strokes(
        self,
        strokes: List[Stroke],
        target_width_pt: float,
        target_height_pt: float,
        *,
        is_pdf_page: bool = False,
        x_origin_pt: float = 0.0,
        y_origin_pt: float = 0.0,
    ) -> List[Stroke]:
        """
        Convert stroke coordinates from reMarkable device space to PDF point space.

        Args:
            strokes: List of strokes in device coordinates.
            target_width_pt: Target area width in PDF points.
            target_height_pt: Target area height in PDF points.
            is_pdf_page: If True, apply de-centering fix for PDF exports.
            x_origin_pt: X offset for CropBox translation.
            y_origin_pt: Y offset for CropBox translation.

        Returns:
            List of strokes with coordinates in PDF point space (input unchanged).
        """
   
        if is_pdf_page:
            scale = target_width_pt / RM_WIDTH
            sx = scale
            sy = scale
        else:
            # Notebook: independent per-axis scale fills the blank page.
            sx = target_width_pt  / RM_WIDTH
            sy = target_height_pt / RM_HEIGHT
        x_rm_offset: float = RM_HALF_WIDTH                      # scale to mid point origin

        scaled: List[Stroke] = []
        for s in strokes:
            new_points: List[Point] = []
            for pt in s.points:  # pt is now Point(x, y, pressure) — not a raw tuple
                # ── x pipeline ───────────────────────────────────────────────
                x_abs   = pt.x + x_rm_offset   # bug fix #1: de-center PDF coords
                x_scaled = x_abs * sx           # scale rm units → PDF points
                x_final  = x_scaled + x_origin_pt  # bug fix #2: CropBox offset

                # ── y pipeline ───────────────────────────────────────────────
                y_scaled = pt.y * sy            # scale rm units → PDF points
                y_final  = y_scaled + y_origin_pt  # bug fix #2: CropBox offset

                # Pressure is a sensor reading, not a coordinate — pass through.
                new_points.append(Point(x=x_final, y=y_final, pressure=pt.pressure))

            scaled.append(Stroke(
                tool=s.tool,
                color=s.color,
                width=s.width,        
                points=new_points,
                block_index=s.block_index,
            ))

        # -- DEBUG: Add border stroke --
        border_points = [
            Point(x=x_origin_pt, y=y_origin_pt, pressure=1.0),
            Point(x=x_origin_pt + target_width_pt, y=y_origin_pt, pressure=1.0),
            Point(x=x_origin_pt + target_width_pt, y=y_origin_pt + target_height_pt, pressure=1.0),
            Point(x=x_origin_pt, y=y_origin_pt + target_height_pt, pressure=1.0),
            Point(x=x_origin_pt, y=y_origin_pt, pressure=1.0),  # Close the rectangle
        ]
        border_stroke = Stroke(
            tool=18,  # or an arbitrary tool for debug
            color=1,   # distinguishable color for debug
            width=2.0,     # make it a thin but visible border
            points=border_points,
            block_index=-1
        )
        scaled.append(border_stroke)

        return scaled

    def scale_highlights(
        self,
        highlights: List[Highlight],
        target_width_pt: float,
        target_height_pt: float,
        *,
        is_pdf_page: bool = False,
        x_origin_pt: float = 0.0,
        y_origin_pt: float = 0.0,
    ) -> List[Highlight]:
        """Convert highlight rectangle coordinates from reMarkable device space to PDF point space.

        The transformation applies the same x/y pipeline as `scale_strokes` to the (x, y) origin of each rectangle.
        Rectangle extents (`w`, `h`) are scaled using `sx` and `sy` but do not apply translation offsets.

        Args:
            highlights: List of highlights in reMarkable coordinates.
            target_width_pt: Output width in PDF points.
            target_height_pt: Output height in PDF points.
            is_pdf_page: If True, applies PDF x de-centering.
            x_origin_pt: X translation offset for CropBox.
            y_origin_pt: Y translation offset for CropBox.

        Returns:
            List of Highlight objects with coordinates in PDF point space.
        """
   
        if is_pdf_page:
            scale = target_width_pt / RM_WIDTH
            sx = scale
            sy = scale
        else:
            # Notebook: independent per-axis scale fills the blank page.
            sx = target_width_pt  / RM_WIDTH
            sy = target_height_pt / RM_HEIGHT
        x_rm_offset: float = RM_HALF_WIDTH                      # scale to mid point origin

        scaled: List[Highlight] = []
        for h in highlights:
            new_rects: List[Rectangle] = []
            for r in h.rectangles:
                # Transform rectangle origin
                x_final = (r.x + x_rm_offset) * sx + x_origin_pt
                y_final = r.y * sy + y_origin_pt
         
                w_final = r.w * sx
                h_final = r.h * sy

                new_rects.append(Rectangle(x=x_final, y=y_final, w=w_final, h=h_final))

            scaled.append(Highlight(
                text=h.text,
                color=h.color,
                rectangles=new_rects,
                block_index=h.block_index,
            ))        

        return scaled

    # ------------------------------------------------------------------
    # Private block extraction helpers
    # ------------------------------------------------------------------

    def _parse_stroke(self, val: Any, block_index: int) -> Optional[Stroke]:
        """Parse a CRDT item value as a drawn stroke if possible.

        The value must have a non-empty 'points' attribute (from rmscene).
        'color' and 'tool' fields are cast to integer.

        Args:
            val: The CRDT block's value.
            block_index: Index of the parent block.

        Returns:
            Stroke object if detected, else None.
        """
   
        points_raw = getattr(val, "points", None)
        if not points_raw:
            return None

        parsed_points: List[Point] = []
        for p in points_raw:
            x = getattr(p, "x", None)
            y = getattr(p, "y", None)
            if x is None or y is None:
                continue  # skip malformed point rather than crashing
            parsed_points.append(
                Point(
                    x=float(x),
                    y=float(y),
                    pressure=getattr(p, "pressure", None),  # None if unsupported
                )
            )

        if not parsed_points:
            return None

        # Cast IntEnum → plain int for arithmetic safety (from original parser)
        try:
            color = int(val.color)
        except (TypeError, ValueError):
            color = 0

        try:
            tool = int(val.tool)
        except (TypeError, ValueError):
            tool = 0

        return Stroke(
            tool=tool,
            color=color,
            width=float(getattr(val, "width", 1.0) or 1.0),
            points=parsed_points,
            block_index=block_index,
        )

    def _parse_highlight(self, val: Any, block_index: int) -> Optional[Highlight]:
        """
        Try to parse a CRDT item value as a text highlight (GlyphRange).

        Detection: Checks if val is a GlyphRange based on its class name (avoids importing rmscene at module level).

        Args:
            val:           item.value from a CRDT block.
            block_index:   Index of the parent block.

        Returns:
            Highlight object if val is a GlyphRange with text, else None.
        """
   
        if val.__class__.__name__ != "GlyphRange":
            return None

        text = getattr(val, "text", None)
        if not text:
            return None  # GlyphRange exists but carries no text — skip

        rectangles_raw = getattr(val, "rectangles", [])
        rectangles: List[Rectangle] = []
        for r in rectangles_raw:
            rectangles.append(
                Rectangle(
                    x=float(getattr(r, "x", 0.0)),
                    y=float(getattr(r, "y", 0.0)),
                    w=float(getattr(r, "w", 0.0)),
                    h=float(getattr(r, "h", 0.0)),
                )
            )

        return Highlight(
            text=text,
            color=getattr(val, "color", None),
            rectangles=rectangles,
            block_index=block_index,
        )