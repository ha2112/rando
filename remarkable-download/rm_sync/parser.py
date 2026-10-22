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

from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import RM_HEIGHT, RM_WIDTH, SCALE
from models import DocumentMeta, PageInfo, Stroke, Highlight, Point, Rectangle

try:
    import rmscene  # type: ignore[import]
except ImportError:
    raise ImportError(
        "The 'rmscene' package is required.  Install with:\n"
        "    pip install rmscene"
    )


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
        content_file = self.work_dir / f"{self.doc_uuid}.content"
        if not content_file.exists():                        # fix the missing ()
            raise FileNotFoundError(f"Content file not found: {content_file}")

        with content_file.open("r", encoding="utf-8") as fh:
            data: Dict[str, Any] = json.load(fh)

        orientation = data.get("orientation", "portrait")

        # ── Format A: current firmware (cPages CRDT structure) ──────────────────
        if "cPages" in data:
            cpages    = data["cPages"]
            raw_pages = cpages.get("pages", [])
            # guard against {"value": [...]} wrapper on the pages array itself
            raw_pages = self._unwrap(raw_pages)
            return self._parse_cpages_format(raw_pages, orientation)

        # ── Format B: legacy firmware (flat pages array + redirectionPageMap) ───
        if "pages" in data:
            log.debug(
                "%s uses legacy flat-pages format — applying redirectionPageMap.",
                self.doc_uuid,
            )
            return self._parse_legacy_format(data, orientation)

        log.warning("%s: unrecognised .content structure — no pages found.", self.doc_uuid)
        return []


    def _parse_cpages_format(
        self,
        raw_pages: List[Any],
        orientation: str,
    ) -> List[PageInfo]:
        """Parse the current cPages CRDT format."""
        pages: List[PageInfo] = []
        for i, p in enumerate(raw_pages):
            is_deleted = p.get("deleted", False) or p.get("deletedLength", 0) > 0
            if is_deleted:
                log.debug("Skipping deleted page at CRDT index %d (uuid: %s)", i, p.get("id"))
                continue
            pages.append(PageInfo(
                page_number = i,
                uuid        = p.get("id", ""),
                redir       = self._safe_int(self._unwrap(p.get("redir"))),
                template    = self._unwrap(p.get("template")),
                modified    = self._safe_int(p.get("modified")),
                orientation = orientation,
            ))
        log.debug("Parsed %d active pages (cPages format).", len(pages))
        return pages


    def _parse_legacy_format(
        self,
        data: Dict[str, Any],
        orientation: str,
    ) -> List[PageInfo]:
        """Parse the legacy flat pages + redirectionPageMap format."""
        page_uuids   = data.get("pages", [])
        redir_map    = data.get("redirectionPageMap", [])

        pages: List[PageInfo] = []
        for i, uuid in enumerate(page_uuids):
            redir = redir_map[i] if i < len(redir_map) else None
            # legacy format has no per-page deletion markers
            pages.append(PageInfo(
                page_number = i,
                uuid        = uuid,
                redir       = self._safe_int(redir),
                template    = None,
                modified    = None,
                orientation = orientation,
            ))
        log.debug("Parsed %d pages (legacy format).", len(pages))
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
    """Decode resolved visible scene items and place their measurements.

    Notebook x is centered on the decoded SceneInfo.paper_size. PDF annotations
    use the existing fixed physical scale and center on the target page. Explicit
    origins are supported for callers working in media-box coordinates; the
    assembler uses crop-local coordinates. Unknown extension bytes remain exposed.
    """

    def __init__(self):
        self.paper_size = None
        self.extensions = []
        self.missing_source = False
        self.unsupported_blocks = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decode_rm_file(
        self, rm_path: Path
    ) -> Tuple[List[Stroke], List[Highlight]]:
        """Parse a single ``.rm`` v6 file into strokes and text highlights.

        Resolve CRDT item order, tombstones, and layer visibility before rendering.
        Unreadable blocks and fatal decoding failures raise instead of producing
        apparently successful empty ink.

        Args:
            rm_path: Filesystem path to the ``.rm`` binary file.

        Returns:
            ``Tuple[ List[Stroke], List[Highlight] ]``

        Raises:
            ValueError: For corrupt data or unsupported scene operations.
        """

        self.paper_size = None
        self.extensions = []
        self.missing_source = False
        self.unsupported_blocks = []
        if not rm_path.exists():
            # Untouched notebook pages need not have an .rm file.
            self.missing_source = True
            log.warning("Missing .rm file; no recorded ink: %s", rm_path)
            return [], []

        try:
            with rm_path.open("rb") as fh:
                raw_blocks = list(rmscene.read_blocks(fh))
            # Compatibility is limited to the exact observed pair, not all unknown blocks.
            allowed_extensions = {
                (14, 3, 3, bytes.fromhex("1c0100000000")),
                (15, 2, 2, bytes.fromhex("1f000b2f01143f00004f00005401000000")),
            }
            unreadable = [(index, b) for index, b in enumerate(raw_blocks)
                          if b.__class__.__name__ == "UnreadableBlock"]
            signatures = set()
            for _, block in unreadable:
                info = getattr(block, "info", None)
                signatures.add((getattr(info, "block_type", None),
                                getattr(info, "min_version", None),
                                getattr(info, "current_version", None),
                                getattr(block, "data", None)))
            if unreadable and (len(unreadable) != 2 or signatures != allowed_extensions):
                index, block = unreadable[0]
                raise ValueError(f"Unsupported scene block {index}: {block}")
            for index, block in enumerate(raw_blocks):
                if block.__class__.__name__ == "UnreadableBlock":
                    info = block.info
                    self.unsupported_blocks.append(dict(block_index=index,
                        block_type=info.block_type, min_version=info.min_version,
                        current_version=info.current_version, data=block.data,
                        error=block.error))
                    self.extensions.append(dict(block_index=index,
                        block_type=f"Unknown_0x{info.block_type:02x}", field="data", data=block.data))
                    log.warning("%s: partial fidelity: preserved unknown block 0x%02x (%d bytes)",
                                rm_path.name, info.block_type, len(block.data))
                for field in ("extra_data", "extra_value_data"):
                    data = getattr(block, field, b"")
                    if data:
                        self.extensions.append(dict(block_index=index,
                            block_type=type(block).__name__, field=field, data=data))
                if block.__class__.__name__ == "SceneInfo":
                    self.paper_size = getattr(block, "paper_size", None)
            tree = rmscene.SceneTree()
            rmscene.build_tree(tree, raw_blocks)
            if tree.root_text is not None and any(
                    not item.deleted_length and isinstance(item.value, str) and item.value
                    for item in tree.root_text.items.sequence_items()):
                raise ValueError("Typed scene text is not supported by this renderer")
        except Exception as exc:
            raise ValueError(f"Failed to decode {rm_path}: {exc}") from exc

        if self.extensions:
            log.warning("%s: partial fidelity: %d preserved extension regions (%d bytes)",
                        rm_path.name, len(self.extensions),
                        sum(len(r["data"]) for r in self.extensions))

        # Resolve scene order and visibility before splitting ink and glyphs.
        source = {b.item.item_id: i for i, b in enumerate(raw_blocks)
                  if getattr(b, "item", None) is not None}
        strokes, highlights = [], []

        def visit(group, offset=(0.0, 0.0)):
            if not group.visible.value:
                return
            items = {item.item_id: item for item in group.children.sequence_items()}
            if not any(not item.deleted_length and item.value is not None for item in items.values()):
                return
            if group.anchor_id is not None:
                dx, dy = self._empty_heading_anchor(group, tree.root_text)
                offset = (offset[0] + dx, offset[1] + dy)
            for item_id in group.children:
                item = items[item_id]
                if item.deleted_length or item.value is None:
                    continue
                value = item.value
                if isinstance(value, rmscene.scene_items.Group):
                    yield from visit(value, offset)
                else:
                    yield item_id, value, offset

        for order, (item_id, value, offset) in enumerate(visit(tree.root)):
            index = source[item_id]
            parsed = self._parse_stroke(value, index)
            if parsed is not None:
                if parsed.tool in (6, 8):
                    raise ValueError("Unresolved eraser operations require ink masks")
                strokes.append(replace(parsed, scene_order=order, item_id=str(item_id), scene_offset=offset))
            else:
                parsed = self._parse_highlight(value, index)
                if parsed is not None:
                    highlights.append(replace(parsed, scene_order=order, item_id=str(item_id), scene_offset=offset))

        return strokes, highlights

    @staticmethod
    def _empty_heading_anchor(group, text):
        """Resolve the empty-heading profile measured against Nhap copy's export."""
        from rmscene.tagged_block_common import CrdtId
        end = CrdtId(0, 0)
        special = {CrdtId(0, 281474976710654), CrdtId(0, 281474976710655)}
        if (text is None or set(text.styles) != {end}
                or text.styles[end].value != rmscene.scene_items.ParagraphStyle.HEADING
                or group.anchor_id.value not in special
                or group.anchor_origin_x is None
                or group.anchor_type is None or group.anchor_type.value != 2):
            raise ValueError(f"Unsupported anchor profile: {group.node_id}")
        # All 242 reference paths independently confirm this baseline to <0.001 scene units.
        return group.anchor_origin_x.value, text.pos_y + 63.1875

    def scale_strokes(
        self,
        strokes: List[Stroke],
        target_width_pt: float,
        target_height_pt: float,
        *,
        is_pdf_page: bool = False,
        x_origin_pt: float = 0.0,
        y_origin_pt: float = 0.0,
        source_size: Optional[Tuple[float, float]] = None,
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
            scale = SCALE
            sx = scale
            sy = scale
            x_rm_offset = target_width_pt / scale / 2 
        else:
            # Notebook: independent per-axis scale fills the blank page.
            source_w, source_h = source_size or self.paper_size or (RM_WIDTH, RM_HEIGHT)
            sx = target_width_pt / source_w
            sy = target_height_pt / source_h
            x_rm_offset = source_w / 2

        scaled: List[Stroke] = []
        for s in strokes:
            new_points: List[Point] = []
            dx, dy = s.scene_offset
            for pt in s.points:
                # ── x pipeline ───────────────────────────────────────────────
                x_abs   = pt.x + dx + x_rm_offset   # bug fix #1: de-center PDF coords
                x_scaled = x_abs * sx           # scale rm units → PDF points
                x_final  = x_scaled + x_origin_pt  # bug fix #2: CropBox offset

                # ── y pipeline ───────────────────────────────────────────────
                y_scaled = (pt.y + dy) * sy  # y offset removed
                y_final  = y_scaled + y_origin_pt  # bug fix #2: CropBox offset

                # Pressure is a sensor reading, not a coordinate — pass through.
                new_points.append(replace(pt, x=x_final, y=y_final))

            scaled.append(replace(s, points=new_points, scene_offset=(0.0, 0.0),
                geometry_scale=(s.geometry_scale[0] * sx, s.geometry_scale[1] * sy)))

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
        source_size: Optional[Tuple[float, float]] = None,
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
            scale = SCALE
            sx = scale
            sy = scale
            x_rm_offset = target_width_pt / scale / 2.0 
        else:
            # Notebook: independent per-axis scale fills the blank page.
            source_w, source_h = source_size or self.paper_size or (RM_WIDTH, RM_HEIGHT)
            sx = target_width_pt / source_w
            sy = target_height_pt / source_h
            x_rm_offset = source_w / 2

        scaled: List[Highlight] = []
        for h in highlights:
            new_rects: List[Rectangle] = []
            dx, dy = h.scene_offset
            for r in h.rectangles:
                # Transform rectangle origin and consume the scene-group translation.
                x_final = (r.x + dx + x_rm_offset) * sx + x_origin_pt
                y_final = (r.y + dy) * sy + y_origin_pt  # y offset removed
         
                w_final = r.w * sx
                h_final = r.h * sy

                new_rects.append(Rectangle(x=x_final, y=y_final, w=w_final, h=h_final))

            scaled.append(replace(h, rectangles=new_rects, scene_offset=(0.0, 0.0)))

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
                    pressure=getattr(p, "pressure", None),
                    width=getattr(p, "width", None),
                    speed=getattr(p, "speed", None),
                    direction=getattr(p, "direction", None),
                )
            )

        if not parsed_points:
            return None

        # Cast IntEnum → plain int for arithmetic safety (from original parser)
        try:
            color = int(val.color)
        except (AttributeError, TypeError, ValueError):
            color = None

        try:
            tool = int(val.tool)
        except (AttributeError, TypeError, ValueError):
            tool = None

        return Stroke(
            tool=tool,
            color=color,
            thickness_scale=getattr(val, "thickness_scale", None),
            color_rgba=getattr(val, "color_rgba", None),
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
            color_rgba=getattr(val, "color_rgba", None),
            color=getattr(val, "color", None),
            rectangles=rectangles,
            block_index=block_index,
        )