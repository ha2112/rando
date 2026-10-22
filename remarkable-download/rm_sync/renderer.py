"""
renderer.py
-----------
PDF assembler using PyMuPDF to overlay stroke and highlight data onto PDF pages.

PDFRenderer does the following:
1. Sequences pages in CRDT order, inserting blanks for interleaved notes.
2. Delegates coordinate transformation to StrokeProcessor using correct PDF/page settings.
3. Renders measured vector ink, preserving explicit RGBA. Non-target brushes
   use an explicitly reported approximation; blend/texture calibration is deferred.
"""
from __future__ import annotations

import hashlib
import json
import logging
from importlib.metadata import version
import tempfile
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF

from config import (BLANK_PAGE_HEIGHT_PT, BLANK_PAGE_WIDTH_PT, SCALE,
                    HIGHLIGHT_OPACITY_PLAIN, PEN_COLOR_MAP)
from brushes import draw_ink, glyph_highlight_color, painted_bounds

from models import Highlight, PageInfo, Stroke
from parser import StrokeProcessor

log = logging.getLogger("rm-rebuilder.renderer")

# Bump when geometry, palette, or brush behavior changes.
RENDERER_REVISION = "pen-measurements-v3-snapped-highlight-blending"


def render_settings_signature():
    settings = (BLANK_PAGE_WIDTH_PT, BLANK_PAGE_HEIGHT_PT, SCALE,
                HIGHLIGHT_OPACITY_PLAIN, PEN_COLOR_MAP,
                fitz.VersionBind, version("rmscene"))
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


class PDFRenderer:
    def __init__(self) -> None:
        self._doc: Optional[fitz.Document] = None
        self.fidelity_warnings = []

    # ==========================================================
    # PUBLIC ENTRYPOINT
    # ==========================================================
    def build_document(
        self,
        pages: List[PageInfo],
        base_doc: Optional[fitz.Document],
        processor: StrokeProcessor,
        stroke_provider,  # function(page_info) -> (strokes, highlights)
    ) -> None:
        self._doc = fitz.open()

        for page_index, page_info in enumerate(pages):
            strokes, highlights = stroke_provider(page_info)
            fallback_tools = sorted({s.tool for s in strokes if s.tool not in (2, 4, 15, 17)},
                                    key=lambda tool: -1 if tool is None else tool)
            if fallback_tools:
                log.warning("%s: approximate brush appearance for tools %s",
                            page_info.uuid, fallback_tools)
            if processor.extensions or fallback_tools or processor.missing_source:
                self.fidelity_warnings.append(dict(page=page_info.uuid,
                    extension_regions=len(processor.extensions),
                    extension_bytes=sum(len(r["data"]) for r in processor.extensions),
                    approximate_tools=fallback_tools, missing_source=processor.missing_source,
                    unsupported_blocks=[dict(b, data=b["data"].hex())
                                        for b in processor.unsupported_blocks]))

            is_pdf = page_info.redir is not None and base_doc is not None

            self._build_single_page(
                page_info,
                base_doc,
                strokes,
                highlights,
                processor,
                is_pdf,
            )

    # ==========================================================
    # CORE PIPELINE
    # ==========================================================
    def _build_single_page(
        self,
        page_info,
        base_doc,
        strokes,
        highlights,
        processor,
        is_pdf_page: bool,
    ):
        # ------------------------------------------------------
        # 1. GET BASE PDF GEOMETRY
        # ------------------------------------------------------
        if is_pdf_page:
            src_page = base_doc[page_info.redir]
            crop = src_page.cropbox

            target_w = float(crop.width)
            target_h = float(crop.height)

            x_origin = 0.0
            y_origin = 0.0
        else:
            if processor.paper_size:
                target_w = processor.paper_size[0] * SCALE
                target_h = processor.paper_size[1] * SCALE
            else:
                target_w = BLANK_PAGE_WIDTH_PT * SCALE
                target_h = BLANK_PAGE_HEIGHT_PT * SCALE
            x_origin = 0.0
            y_origin = 0.0
        

        # ------------------------------------------------------
        # 2. SCALE STROKES INTO PDF SPACE
        # ------------------------------------------------------
        scaled_strokes = processor.scale_strokes(
            strokes,
            target_w,
            target_h,
            is_pdf_page=is_pdf_page,
            x_origin_pt=x_origin,
            y_origin_pt=y_origin,
        )

        scaled_highlights = processor.scale_highlights(
            highlights,
            target_w,
            target_h,
            is_pdf_page=is_pdf_page,
            x_origin_pt=x_origin,
            y_origin_pt=y_origin,
        )

        # ------------------------------------------------------
        # 3. COMPUTE BOUNDING BOX
        # ------------------------------------------------------
        content_bbox = self._compute_content_bbox(
            scaled_strokes,
            scaled_highlights,
            base_rect=fitz.Rect(0, 0, target_w, target_h)
        )

        if not is_pdf_page:
            # Keep the notebook origin; never fit low-positioned ink to the top.
            # Native extended-page padding is empirical, verified on Pen page 2.
            if content_bbox.y1 > target_h:
                content_bbox.y1 = max(content_bbox.y1,
                    max((p.y for s in scaled_strokes for p in s.points), default=0) + 50 * SCALE)
            painted = self._compute_content_bbox(scaled_strokes, scaled_highlights,
                                                 fitz.Rect(0, 0, 0, 0))
            # Round nominal dimensions, while retaining all painted extents.
            content_bbox.x1 = max(round(target_w), painted.x1)
            content_bbox.y1 = max(round(target_h), round(content_bbox.y1), painted.y1)
        final_bbox = content_bbox

        # ------------------------------------------------------
        # 4. CREATE FITTED PAGE
        # ------------------------------------------------------
        page_w = final_bbox.width
        page_h = final_bbox.height
        new_page = self._doc.new_page(width=page_w, height=page_h)
        dx = -final_bbox.x0
        dy = -final_bbox.y0
   

        # ------------------------------------------------------
        # 5. DRAW ORIGINAL PDF CONTENT
        # ------------------------------------------------------
        if is_pdf_page:
            target_rect = fitz.Rect(dx, dy, target_w + dx, target_h + dy)
            new_page.show_pdf_page(target_rect, base_doc, page_info.redir, clip=src_page.rect)

        # ------------------------------------------------------
        # 6. SHIFT + DRAW STROKES
        # ------------------------------------------------------
        for stroke in scaled_strokes:
            for pt in stroke.points:
                pt.x += dx
                pt.y += dy

        for h in scaled_highlights:
            for r in h.rectangles:
                r.x += dx
                r.y += dy

        self._draw_items(new_page, scaled_strokes, scaled_highlights)

    # ==========================================================
    # BOUNDING BOX LOGIC
    # ==========================================================
    def _compute_content_bbox(
        self,
        strokes,
        highlights,
        base_rect: fitz.Rect
    ) -> fitz.Rect:
        xs = []
        ys = []

        # strokes
        for s in strokes:
            if s.points:
                x0, y0, x1, y1 = painted_bounds(s)
                xs.extend([x0, x1])
                ys.extend([y0, y1])

        # highlights
        for h in highlights:
            for r in h.rectangles:
                xs.extend([r.x, r.x + r.w])
                ys.extend([r.y, r.y + r.h])

        if not xs:
            return base_rect

        stroke_bbox = fitz.Rect(
            min(xs),
            min(ys),
            max(xs),
            max(ys),
        )

        # union with base content
        return base_rect | stroke_bbox 

    # ======================================================================================================================
    # Drawing functions for rendering strokes and highlights follow.
    # ======================================================================================================================
    def render_strokes_on_page(
        self,
        output_page_index: int,
        strokes: List[Stroke],
        highlights: List[Highlight],
        processor: StrokeProcessor,
        *,
        is_pdf_page: bool = False,
    ) -> None:
        """
        Scale and draw all strokes and highlight rectangles onto one output page.

        For PDF-backed pages (is_pdf_page=True), scales and shifts coordinates using the page's CropBox,
        applying both offset and scaling so strokes fill the visible content area.

        For notebook pages (is_pdf_page=False), fills an RM_WIDTH × RM_HEIGHT page with a 1:1 mapping.

        Strokes and GlyphRange highlights are both scaled using the processor. Scaled highlight strokes and rectangles
        are drawn with the appropriate methods.

        Args:
            output_page_index (int): Index of the output page.
            strokes (List[Stroke]): Raw ink strokes (device coordinates).
            highlights (List[Highlight]): Raw GlyphRange highlights (device coordinates).
            processor (StrokeProcessor): Used for coordinate scaling.
            is_pdf_page (bool): If True, enables PDF-specific geometry corrections.
        """
   
        page = self._doc[output_page_index]

        if is_pdf_page:
            # Get CropBox geometry and position for PDF pages
            # page.cropbox: visible rectangle in PDF coordinates
            # page.cropbox_position: (x0, y0) offset within the document
 
            crop     = page.cropbox
            crop_pos = page.cropbox_position   # fitz.Point(x0, y0)
            target_w = float(crop.width)
            target_h = float(crop.height)
            x_origin = float(crop_pos.x)
            y_origin = float(crop_pos.y)
        else:
            # Blank notebook pages are exactly RM canvas size — no offset.
            target_w = float(page.rect.width)
            target_h = float(page.rect.height)
            x_origin = 0.0
            y_origin = 0.0

        # Transform ink strokes to PDF point coordinates
 
        scaled_strokes = processor.scale_strokes(
            strokes,
            target_w,
            target_h,
            is_pdf_page=is_pdf_page,
            x_origin_pt=x_origin,
            y_origin_pt=y_origin,
        )

        # Transform GlyphRange highlight rectangles to PDF point coordinates.
 
        scaled_highlights = processor.scale_highlights(
            highlights,
            target_w,
            target_h,
            is_pdf_page=is_pdf_page,
            x_origin_pt=x_origin,
            y_origin_pt=y_origin,
        )

        self._draw_items(page, scaled_strokes, scaled_highlights)

    def _draw_items(self, page, strokes, highlights):
        for item in sorted([*strokes, *highlights], key=lambda item: item.scene_order):
            if isinstance(item, Highlight):
                self._draw_glyph_highlight(page, item)
            else:
                self._draw_stroke(page, item)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def save(self, output_path: Path) -> None:
        """Save the assembled PDF document to disk, removing unused objects and compressing content.

        Args:
            output_path: Path to save the output PDF file.
        """
   
        log.info("Saving → %s", output_path)
        # Replace only after the complete PDF has been written successfully.
        with tempfile.NamedTemporaryFile(dir=output_path.parent, suffix=".pdf", delete=False) as fh:
            temporary = Path(fh.name)
        try:
            self._doc.save(str(temporary), garbage=4, deflate=True, clean=True)
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        """Release the :class:`fitz.Document` handle."""
        if self._doc:
            self._doc.close()
            self._doc = None

    # ------------------------------------------------------------------
    # Private drawing helpers
    # ------------------------------------------------------------------

    def _draw_stroke(self, page: fitz.Page, stroke: Stroke) -> None:
        draw_ink(page, stroke)

    def _draw_highlight(self, page: fitz.Page, stroke: Stroke) -> None:
        # Explicit RGBA is authoritative; Multiply calibration is a later gate.
        draw_ink(page, stroke)

    def _draw_dot(self, page: fitz.Page, stroke: Stroke) -> None:
        draw_ink(page, stroke)

    def _draw_glyph_highlight(self, page: fitz.Page, highlight: Highlight) -> None:
        if not highlight.rectangles:
            return
        color, opacity = glyph_highlight_color(highlight)
        shape = page.new_shape()
        for rect in highlight.rectangles:
            shape.draw_rect(fitz.Rect(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h))
        shape.finish(color=None, fill=color, fill_opacity=opacity, closePath=False)
        shape.commit()
        self._multiply_highlight_stream(page, page.get_contents()[-1])

    @staticmethod
    def _multiply_highlight_stream(page: fitz.Page, stream_xref: int) -> None:
        # Shape.finish has no blend-mode argument. Use a real PDF graphics state.
        doc = page.parent
        resource_type, resources = doc.xref_get_key(page.xref, "Resources")
        if resource_type == "xref":
            resource_xref = int(resources.split()[0])
        else:
            resource_xref = doc.get_new_xref()
            doc.update_object(resource_xref, resources if resource_type == "dict" else "<<>>")
            doc.xref_set_key(page.xref, "Resources", f"{resource_xref} 0 R")
        state_xref = doc.get_new_xref()
        doc.update_object(state_xref, "<< /Type /ExtGState /BM /Multiply >>")
        owner = resource_xref
        prefix = "ExtGState/"
        kind, value = doc.xref_get_key(resource_xref, "ExtGState")
        if kind == "xref":
            owner, prefix = int(value.split()[0]), ""
        name = f"RMSnappedHighlight{state_xref}"
        while doc.xref_get_key(owner, prefix + name)[0] != "null":
            name += "X"
        doc.xref_set_key(owner, prefix + name, f"{state_xref} 0 R")
        data = doc.xref_stream(stream_xref)
        # Limit Multiply to this mark, preserving the graphics state for later ink.
        doc.update_stream(stream_xref, b"q\n/" + name.encode("ascii") + b" gs\n" + data + b"\nQ\n")
