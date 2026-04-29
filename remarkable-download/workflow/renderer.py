"""
renderer.py
-----------
PDF assembler using PyMuPDF to overlay stroke and highlight data onto PDF pages.

PDFRenderer does the following:
1. Sequences pages in CRDT order, inserting blanks for interleaved notes.
2. Delegates coordinate transformation to StrokeProcessor using correct PDF/page settings.
3. Renders highlighter strokes as semi-transparent wide polylines with blending (Multiply) when supported, tinting background without obscuring text.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

from config import (
    BASE_LINE_WIDTH,
    BLANK_PAGE_HEIGHT_PT,
    BLANK_PAGE_WIDTH_PT,
    HIGHLIGHT_OPACITY_BLEND,
    HIGHLIGHT_OPACITY_PLAIN,
    HIGHLIGHT_WIDTH_MIN,
    HIGHLIGHT_WIDTH_SCALE,
    MIN_LINE_WIDTH,
    pen_color_to_rgb,
)
from models import Highlight, PageInfo, Stroke
from parser import StrokeProcessor

log = logging.getLogger("rm-rebuilder.renderer")


class PDFRenderer:
    def __init__(self) -> None:
        self._doc: Optional[fitz.Document] = None

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
            crop_pos = src_page.cropbox_position

            target_w = float(crop.width)
            target_h = float(crop.height)

            x_origin = float(crop_pos.x)
            y_origin = float(crop_pos.y)
        else:
            target_w = BLANK_PAGE_WIDTH_PT
            target_h = BLANK_PAGE_HEIGHT_PT
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
            base_rect=(
                src_page.cropbox if is_pdf_page else
                fitz.Rect(0, 0, target_w, target_h)
            )
        )

        # margin (tunable)
        MARGIN = 40

        final_bbox = fitz.Rect(
            content_bbox.x0 - MARGIN,
            content_bbox.y0 - MARGIN,
            content_bbox.x1 + MARGIN,
            content_bbox.y1 + MARGIN,
        )

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
            src_page = base_doc[page_info.redir]
            try:
                content_bbox = src_page.get_contents_bbox()
            except AttributeError:
                content_bbox = src_page.cropbox

            target_rect = fitz.Rect(
                content_bbox.x0 + dx,
                content_bbox.y0 + dy,
                content_bbox.x1 + dx,
                content_bbox.y1 + dy,
            )
   
            # Draw only the CropBox area of the source page onto the new page.
            new_page.show_pdf_page(
                target_rect,
                base_doc,
                page_info.redir,
                clip=crop,
            )

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

        for stroke in scaled_strokes:
            if not stroke.points:
                continue

            if len(stroke.points) == 1:
                self._draw_dot(new_page, stroke)
            elif stroke.is_highlight:
                self._draw_highlight(new_page, stroke)
            else:
                self._draw_stroke(new_page, stroke)

        for highlight in scaled_highlights:
            self._draw_glyph_highlight(new_page, highlight)

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
            for p in s.points:
                xs.append(p.x)
                ys.append(p.y)

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

        # ── Render ink strokes ────────────────────────────────────────────
        for stroke in scaled_strokes:
            if not stroke.points:
                continue

            if len(stroke.points) == 1:
                # Single-point tap: render as a small filled circle
                self._draw_dot(page, stroke)
                continue

            if stroke.is_highlight:
                # Highlighter-tool stroke: wide + transparent + Multiply blend
                self._draw_highlight(page, stroke)
            else:
                # Normal stroke: thin opaque polyline
                self._draw_stroke(page, stroke)

        # ── Render GlyphRange text-selection highlights ───────────────────
        for highlight in scaled_highlights:
            self._draw_glyph_highlight(page, highlight)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def save(self, output_path: Path) -> None:
        """Save the assembled PDF document to disk, removing unused objects and compressing content.

        Args:
            output_path: Path to save the output PDF file.
        """
   
        log.info("Saving → %s", output_path)
        self._doc.save(
            str(output_path),
            garbage=4,     # purge unused objects left by insert_pdf copies
            deflate=True,  # compress all content streams
            clean=True,    # sanitise content stream syntax
        )

    def close(self) -> None:
        """Release the :class:`fitz.Document` handle."""
        if self._doc:
            self._doc.close()
            self._doc = None

    # ------------------------------------------------------------------
    # Private drawing helpers
    # ------------------------------------------------------------------

    def _draw_stroke(self, page: fitz.Page, stroke: Stroke) -> None:
        """Render a normal (non-highlight) stroke as an opaque polyline with rounded caps.

        The line width is determined by:
            max(MIN_LINE_WIDTH, BASE_LINE_WIDTH * stroke.width)

        Args:
            page:   The target fitz.Page instance.
            stroke: A Stroke object with already scaled coordinates.
        """
   
        r, g, b  = pen_color_to_rgb(stroke.color)
        color    = (r, g, b)
        width    = max(MIN_LINE_WIDTH, BASE_LINE_WIDTH * stroke.width)
        pts      = [fitz.Point(pt.x, pt.y) for pt in stroke.points]

        try:
            page.draw_polyline(pts, color=color, width=width, lineCap=1)
        except Exception as exc:
            log.warning("draw_polyline failed on page (normal stroke): %s", exc)

    def _draw_highlight(self, page: fitz.Page, stroke: Stroke) -> None:
        """Render a highlighter stroke as a wide, semi-transparent polyline.

        Attempts to use the PDF "Multiply" blend mode for realistic highlighter
        appearance (background tinted, text remains sharp and legible). If the
        PyMuPDF version does not support blend_mode, falls back to lower opacity.

        Args:
            page:   fitz.Page to draw on.
            stroke: Stroke object (should be a highlight), with pre-scaled coordinates.
        """
   
        r, g, b = pen_color_to_rgb(stroke.color)
        color   = (r, g, b)
        # Highlighter strokes are rendered wider to cover text lines and scaled according to the user's brush size.
 
        width = max(HIGHLIGHT_WIDTH_MIN, HIGHLIGHT_WIDTH_SCALE * stroke.width)
        pts   = [fitz.Point(pt.x, pt.y) for pt in stroke.points]
        try:
            shape = page.new_shape()
            shape.draw_polyline(pts)
            shape.finish(
                color=color,
                width=width,
                stroke_opacity=HIGHLIGHT_OPACITY_BLEND,
                blend_mode="Multiply",
                lineCap=1,
            )
            shape.commit()
            return
        except TypeError:
            log.debug("Multiply blend mode unavailable; using plain opacity fallback.")
        except Exception as exc:
            log.warning("Highlight (blend mode path) failed: %s", exc)
   
        try:
            shape = page.new_shape()
            shape.draw_polyline(pts)
            shape.finish(
                color=color,
                width=width,
                stroke_opacity=HIGHLIGHT_OPACITY_PLAIN, 
                lineCap=1,
            )
            shape.commit()
        except Exception as exc:
            log.warning("Highlight (fallback path) also failed: %s", exc)

    def _draw_dot(self, page: fitz.Page, stroke: Stroke) -> None:
        """Render a single-point stroke as a small filled circle.

        Args:
            page:   Target :class:`fitz.Page`.
            stroke: :class:`~models.Stroke` with exactly one point in
                pre-scaled coordinates.
        """
        r, g, b = pen_color_to_rgb(stroke.color)
        pt      = stroke.points[0]
        # Radius is at minimum 0.5 pt; scales gently with width
        radius  = max(0.5, 0.75 * stroke.width)
        page.draw_circle(
            fitz.Point(pt.x, pt.y),
            radius,
            color=(r, g, b),
            fill=(r, g, b),
        )

    def _draw_glyph_highlight(self, page: fitz.Page, highlight: Highlight) -> None:
        """Render a text highlight as filled rectangles with blend/opacity fallback.

        Draws each rectangle in the highlight with semi-transparent fill, using
        Multiply blend mode if available for best text readability, else plain opacity.

        Args:
            page:      fitz.Page to draw on.
            highlight: Highlight object with pre-scaled rectangles.
        """
   
        if not highlight.rectangles:
            return

        r, g, b = pen_color_to_rgb(highlight.color)
        color   = (r, g, b)

        # ── Attempt 1: Multiply blend mode (PyMuPDF >= 1.24) ─────────────
        try:
            shape = page.new_shape()
            for rect in highlight.rectangles:
                # fitz.Rect takes (x0, y0, x1, y1)
                shape.draw_rect(
                    fitz.Rect(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)
                )
            shape.finish(
                fill=color,
                color=None,           # no border stroke, fill only
                fill_opacity=HIGHLIGHT_OPACITY_BLEND,
                blend_mode="Multiply",
            )
            shape.commit()
            return
        except TypeError:
            log.debug(
                "Multiply blend mode unavailable (PyMuPDF < 1.24); "
                "using opacity fallback for glyph highlight."
            )
        except Exception as exc:
            log.warning("GlyphRange highlight (blend mode path) failed: %s", exc)

        # ── Attempt 2: Plain opacity fallback ────────────────────────────
        try:
            shape = page.new_shape()
            for rect in highlight.rectangles:
                shape.draw_rect(
                    fitz.Rect(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)
                )
            shape.finish(
                fill=color,
                color=None,
                fill_opacity=HIGHLIGHT_OPACITY_PLAIN,
            )
            shape.commit()
        except Exception as exc:
            log.warning("GlyphRange highlight (fallback path) also failed: %s", exc)