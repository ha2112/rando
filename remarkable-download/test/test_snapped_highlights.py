"""Snapped highlight color, compositing, and text-readability regressions."""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import fitz

from rm_sync.parser import DocumentParser, StrokeProcessor
from rm_sync.renderer import PDFRenderer, Highlight
from rm_sync.models import Rectangle, Stroke, Point

FIXTURE = Path(__file__).resolve().parents[1] / 'test_data/snapped_highlights'


def highlight(color=5, rgba=(0, 0, 0, 255), rectangles=None):
    return Highlight('Readable text', color,
                     rectangles or [Rectangle(10, 15, 155, 45)], 0, color_rgba=rgba)


class SnappedHighlightTests(unittest.TestCase):
    def document(self):
        doc = fitz.open(); page = doc.new_page(width=180, height=90)
        page.insert_text((20, 45), 'Readable text', fontsize=20)
        return doc

    def test_indexed_pink_with_black_rgba_is_translucent_pink(self):
        with self.document() as doc:
            value = highlight()
            PDFRenderer()._draw_glyph_highlight(doc[0], value)
            r, g, b = doc[0].get_pixmap().pixel(12, 20)
            self.assertGreater(r, 240)
            self.assertGreater(b, 210)
            self.assertLess(g, b - 15)
            self.assertGreater(g, 150)
            # Appearance fallback must not rewrite the raw decoder measurement.
            self.assertEqual(value.color_rgba, (0, 0, 0, 255))

    def test_text_pixels_stay_black_under_multiply_and_are_extractable(self):
        with self.document() as doc:
            before = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
            PDFRenderer()._draw_glyph_highlight(doc[0], highlight(rgba=(0, 200, 255, 255), color=9))
            after = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
            black = [(x, y) for y in range(30, 120) for x in range(20, 330)
                     if max(before.pixel(x, y)) == 0]
            self.assertGreater(len(black), 100)
            self.assertTrue(all(max(after.pixel(x, y)) <= 2 for x, y in black))
            self.assertEqual(doc[0].get_text().strip(), 'Readable text')
            self.assertGreater(min(after.pixel(24, 40)), 100)

    def test_custom_color_and_partial_alpha_are_preserved(self):
        for alpha, expected in ((255, .35), (64, 64 / 255), (0, 0)):
            with self.subTest(alpha=alpha), self.document() as doc:
                PDFRenderer()._draw_glyph_highlight(doc[0], highlight(9, (20, 180, 240, alpha)))
                draw = doc[0].get_drawings()[-1]
                for a, b in zip(draw['fill'], (20 / 255, 180 / 255, 240 / 255)):
                    self.assertAlmostEqual(a, b, places=5)
                self.assertAlmostEqual(draw['fill_opacity'], expected, places=5)

    def test_explicit_black_custom_color_stays_gray_and_readable(self):
        with self.document() as doc:
            PDFRenderer()._draw_glyph_highlight(doc[0], highlight(9, (0, 0, 0, 255)))
            pixel = doc[0].get_pixmap().pixel(12, 20)
            self.assertEqual(pixel[0], pixel[1])
            self.assertEqual(pixel[1], pixel[2])
            self.assertGreater(pixel[0], 150)
            self.assertLess(pixel[0], 200)

    def test_transparent_color_is_invisible(self):
        with self.document() as doc:
            before = doc[0].get_pixmap().samples
            PDFRenderer()._draw_glyph_highlight(doc[0], highlight(9, (30, 100, 200, 0)))
            self.assertEqual(doc[0].get_pixmap().samples, before)

    def test_one_highlight_has_no_dark_seams_between_overlapping_rectangles(self):
        with self.document() as doc:
            rects = [Rectangle(10, 60, 70, 20), Rectangle(50, 60, 100, 20)]
            PDFRenderer()._draw_glyph_highlight(doc[0], highlight(rectangles=rects))
            pix = doc[0].get_pixmap()
            self.assertEqual(pix.pixel(20, 70), pix.pixel(60, 70))
            self.assertEqual(pix.pixel(60, 70), pix.pixel(140, 70))

    def test_multiply_preserves_colored_content_and_does_not_leak_to_later_ink(self):
        with self.document() as doc:
            page = doc[0]
            page.draw_rect((10, 15, 165, 60), fill=(1, 0, 0), color=None)
            PDFRenderer()._draw_glyph_highlight(page, highlight(9, (0, 255, 255, 255)))
            # Cyan over red under Multiply darkens red; Normal would add green/blue.
            pixel = page.get_pixmap().pixel(12, 20)
            self.assertLess(pixel[1], 3)
            self.assertLess(pixel[2], 3)
            self.assertGreater(pixel[0], 150)
            page.draw_rect((20, 20, 40, 40), fill=(0, 0, 1), color=None)
            self.assertEqual(page.get_pixmap().pixel(30, 30), (0, 0, 255))

    def test_blending_survives_compressed_save_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp, self.document() as doc:
            renderer = PDFRenderer(); renderer._doc = doc
            renderer._draw_glyph_highlight(doc[0], highlight())
            before = doc[0].get_pixmap().samples
            path = Path(tmp) / 'highlight.pdf'; renderer.save(path)
            with fitz.open(path) as reopened:
                after = reopened[0].get_pixmap().samples
                # Content cleaning rounds alpha and text coordinates slightly.
                errors = [abs(a - b) for a, b in zip(after, before)]
                self.assertLessEqual(max(errors), 8)
                self.assertLess(sum(errors) / len(errors), .5)
                self.assertGreater(reopened[0].get_pixmap().pixel(12, 20)[1], 150)
                self.assertAlmostEqual(reopened[0].get_drawings()[-1]['fill_opacity'], .35, places=5)
                self.assertEqual(reopened[0].get_text().strip(), 'Readable text')

    def test_ordinary_opaque_ink_rgba_is_unchanged(self):
        with self.document() as doc:
            stroke = Stroke(17, 0, 1, [Point(20, 70, width=16), Point(100, 70, width=16)],
                            0, color_rgba=(0, 0, 0, 255))
            PDFRenderer()._draw_stroke(doc[0], stroke)
            self.assertEqual(doc[0].get_pixmap().pixel(50, 70), (0, 0, 0))


@unittest.skipUnless((FIXTURE / 'manifest.json').exists(), 'local snapped highlight fixture unavailable')
class SnappedHighlightFixtureTests(unittest.TestCase):
    def test_all_13_real_highlights_leave_black_text_readable_after_pdf_assembly(self):
        manifest = json.loads((FIXTURE / 'manifest.json').read_text())
        for item in manifest['files']:
            data = (FIXTURE / item['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        count = pixels = 0
        for source in manifest['pages']:
            processor = StrokeProcessor(); renderer = PDFRenderer(); plain = PDFRenderer()
            uuid = source['document_uuid']
            strokes, highlights = processor.decode_rm_file(FIXTURE / uuid / (source['page_uuid'] + '.rm'))
            self.assertEqual(len(highlights), source['glyph_count'])
            self.assertTrue(all(h.color == 5 and h.color_rgba == (0, 0, 0, 255) for h in highlights))
            page = next(p for p in DocumentParser(FIXTURE, uuid).parse_pages() if p.uuid == source['page_uuid'])
            with fitz.open(FIXTURE / (uuid + '.pdf')) as base:
                renderer.build_document([page], base, processor, lambda p: ([], highlights))
                # Retain the same bounds without painting the highlight for an aligned baseline.
                transparent = [replace(h, color_rgba=(255, 255, 255, 0)) for h in highlights]
                plain.build_document([page], base, processor, lambda p: ([], transparent))
            try:
                a = plain._doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                b = renderer._doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                self.assertEqual((a.width, a.height), (b.width, b.height))
                painted = renderer._doc[0].get_drawings()[-len(highlights):]
                for draw in painted:
                    rect = draw['rect']
                    dark = [(x, y) for y in range(max(0, int(rect.y0 * 2)), min(a.height, int(rect.y1 * 2)))
                            for x in range(max(0, int(rect.x0 * 2)), min(a.width, int(rect.x1 * 2)))
                            if max(a.pixel(x, y)) <= 5]
                    self.assertGreater(len(dark), 0)
                    self.assertTrue(all(max(b.pixel(x, y)) <= 7 for x, y in dark))
                    # The highlight background must contain a visible pink tint.
                    tinted = sum(1 for y in range(max(0, int(rect.y0 * 2)), min(b.height, int(rect.y1 * 2)))
                                 for x in range(max(0, int(rect.x0 * 2)), min(b.width, int(rect.x1 * 2)))
                                 if b.pixel(x, y)[0] > 200 and b.pixel(x, y)[0] - b.pixel(x, y)[1] > 20)
                    self.assertGreater(tinted, 0)
                    pixels += len(dark)
                self.assertEqual(renderer._doc[0].get_text(), plain._doc[0].get_text())
                count += len(highlights)
            finally:
                renderer.close(); plain.close()
        self.assertEqual(count, 13)
        self.assertGreater(pixels, 500)


if __name__ == '__main__':
    unittest.main()
