"""Offline regressions; the frozen Pen fixture is optional in clean checkouts."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import rmscene
from rmscene.scene_items import Pen

from rm_sync.models import PenType, Point, Stroke
from rm_sync.parser import DocumentParser, StrokeProcessor
from rm_sync.renderer import PDFRenderer
from rm_sync.main import RenderStateTracker, _process_document

FIXTURE = Path(__file__).resolve().parents[1] / "test_data" / "pen_test"


def stroke(tool=17, widths=(8, 8), pressures=(0, 255), rgba=None):
    return Stroke(tool=tool, color=0, thickness_scale=3,
                  points=[Point(20 + i * 40, 30, pressure=p, width=w,
                                speed=12, direction=64)
                          for i, (w, p) in enumerate(zip(widths, pressures))],
                  block_index=0, color_rgba=rgba)


class PenModelTests(unittest.TestCase):
    def test_pen_ids_match_decoder_including_legacy_and_erasers(self):
        names = {'MECH_PENCIL_1': 'MECHANICAL_PENCIL_1',
                 'MECH_PENCIL_2': 'MECHANICAL_PENCIL_2',
                 'CALLIGRAPHY': 'CALIGRAPHY', 'HIGHLIGHTER': 'HIGHLIGHTER_1'}
        for name, value in PenType.__members__.items():
            self.assertEqual(int(value), int(Pen[names.get(name, name)]), name)

    def test_highlight_uses_tool_not_palette(self):
        processor = StrokeProcessor()
        for tool in (0, 2, 4, 6, 8, 15, 17, 23, 999, None):
            value = SimpleNamespace(points=[SimpleNamespace(x=0, y=0)],
                                    tool=tool, color=3, thickness_scale=1)
            parsed = processor._parse_stroke(value, 0)
            self.assertFalse(parsed.is_highlight, tool)
            self.assertEqual(parsed.tool, tool)
        for tool in (5, 18):
            self.assertTrue(stroke(tool=tool).is_highlight)

    def test_glyph_rgba_survives_transform(self):
        from rmscene.scene_items import GlyphRange, Rectangle
        glyph = GlyphRange(None, 1, 'x', 9, [Rectangle(2, 4, 6, 8)],
                           color_rgba=(10, 20, 30, 64))
        p = StrokeProcessor()
        parsed = p._parse_highlight(glyph, 0)
        result = p.scale_highlights([parsed], 1404, 1872)[0]
        self.assertEqual(result.color_rgba, glyph.color_rgba)

    def test_transform_preserves_measurements_and_transforms_nib(self):
        original = stroke(widths=(8, 16))
        p = StrokeProcessor()
        scaled = p.scale_strokes([original], 2808, 936,
                                 source_size=(1404, 1872),
                                 x_origin_pt=7, y_origin_pt=11)[0]
        self.assertEqual((scaled.points[0].x, scaled.points[0].y), (1451, 26))
        for a, b in zip(original.points, scaled.points):
            for field in ('width', 'pressure', 'speed', 'direction'):
                self.assertEqual(getattr(a, field), getattr(b, field))
        self.assertEqual(scaled.geometry_scale, (2, .5))
        self.assertEqual(original.points[0].x, 20)

    def test_corrupt_input_is_not_a_successful_blank_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.rm'
            path.write_bytes(b'broken')
            with self.assertRaises(ValueError):
                StrokeProcessor().decode_rm_file(path)


class SceneResolutionTests(unittest.TestCase):
    def blocks(self, visible=True):
        from rmscene.scene_items import Group, Line, PenColor, Point as RawPoint, GlyphRange, Rectangle
        from rmscene.scene_stream import (SceneTreeBlock, TreeNodeBlock, SceneGroupItemBlock,
                                          SceneLineItemBlock, SceneGlyphItemBlock)
        from rmscene.crdt_sequence import CrdtSequenceItem
        from rmscene.tagged_block_common import CrdtId, LwwValue
        zero, root, layer = CrdtId(0, 0), CrdtId(0, 1), CrdtId(1, 1)
        a, b, c = (CrdtId(1, i) for i in (10, 11, 12))
        line = Line(PenColor.BLACK, Pen.FINELINER_2,
                    [RawPoint(0, 0, 0, 0, 8, 0)], 1, 0)
        glyph = GlyphRange(None, 1, 'x', PenColor.HIGHLIGHT,
                           [Rectangle(0, 0, 2, 2)], (255, 0, 0, 64))
        group = Group(layer, visible=LwwValue(zero, visible))
        return [SceneTreeBlock(layer, layer, False, root), TreeNodeBlock(group),
                SceneGroupItemBlock(root, CrdtSequenceItem(CrdtId(1, 2), zero, zero, 0, layer)),
                # Physical file order differs from the CRDT painting order.
                SceneLineItemBlock(layer, CrdtSequenceItem(c, b, zero, 1, line)),
                SceneGlyphItemBlock(layer, CrdtSequenceItem(b, a, c, 0, glyph)),
                SceneLineItemBlock(layer, CrdtSequenceItem(a, zero, b, 0, line))]

    def decode(self, blocks):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scene.rm'; path.touch()
            with patch('rmscene.read_blocks', return_value=blocks):
                return StrokeProcessor().decode_rm_file(path)

    def test_hidden_layer_and_deleted_ink_stay_absent_in_resolved_order(self):
        self.assertEqual(self.decode(self.blocks(visible=False)), ([], []))
        strokes, glyphs = self.decode(self.blocks())
        self.assertEqual(len(strokes), 1)
        self.assertEqual(len(glyphs), 1)
        self.assertEqual((strokes[0].scene_order, glyphs[0].scene_order), (0, 1))
        self.assertEqual((strokes[0].block_index, glyphs[0].block_index), (5, 4))
        renderer = PDFRenderer()
        with patch.object(renderer, '_draw_stroke') as ink, \
             patch.object(renderer, '_draw_glyph_highlight') as highlight:
            events = []
            ink.side_effect = lambda *args: events.append('ink')
            highlight.side_effect = lambda *args: events.append('glyph')
            renderer._draw_items(None, strokes, glyphs)
        self.assertEqual(events, ['ink', 'glyph'])

    def test_unknown_blocks_are_explicit_failures(self):
        block = type('UnreadableBlock', (), {})()
        with self.assertRaisesRegex(ValueError, 'Unsupported scene block'):
            self.decode([block])


class InkGeometryTests(unittest.TestCase):
    def draw(self, value, sx=1, sy=1):
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        PDFRenderer()._draw_stroke(page, replace(value, geometry_scale=(sx, sy)))
        return doc

    def test_fineliner_has_stable_measured_width_without_size_or_pressure_double_count(self):
        for pressure in ((0, 255), (255, 0)):
            doc = self.draw(stroke(pressures=pressure))
            drawing = doc[0].get_drawings()[0]
            self.assertAlmostEqual(drawing['width'], 2)
            self.assertEqual(drawing['items'][0][1], fitz.Point(20, 30))
            self.assertEqual(drawing['items'][-1][-1], fitz.Point(60, 30))
            doc.close()

    def test_ballpoint_recorded_width_varies_and_alpha_has_no_join_seams(self):
        doc = self.draw(stroke(tool=15, widths=(8, 32), rgba=(0, 0, 0, 128)))
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
        def darkness(x, y):
            return 255 - pix.pixel(round(x * 4), round(y * 4))[0]
        thin = sum(darkness(22, y / 4) > 15 for y in range(80, 161))
        thick = sum(darkness(58, y / 4) > 15 for y in range(80, 161))
        self.assertGreater(thick, thin * 2)
        self.assertLessEqual(max(darkness(x, 30) for x in range(20, 61)), 130)
        self.assertGreater(darkness(40, 30), 125)
        doc.close()

    def test_duplicate_points_dots_and_anisotropic_nib_bounds(self):
        value = stroke(tool=15, widths=(16, 16))
        value.points[1].x = value.points[0].x
        doc = self.draw(value, sx=2, sy=.5)
        bbox = PDFRenderer()._compute_content_bbox(
            [replace(value, geometry_scale=(2, .5))], [], fitz.Rect(20, 30, 20, 30))
        self.assertEqual(tuple(bbox), (16, 29, 24, 31))
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        self.assertLess(pix.pixel(80, 120)[0], 10)
        doc.close()

    def test_missing_or_invalid_width_has_deterministic_size_fallback(self):
        from rm_sync.brushes import stroke_widths
        value = stroke(tool=15, widths=(None, float('nan'), 0, -1),
                       pressures=(0, 1, 200, 255))
        self.assertEqual(stroke_widths(value), [6, 6, 6, 6])

    def test_explicit_rgba_wins_over_palette_and_is_deterministic(self):
        value = stroke(rgba=(10, 20, 30, 64))
        docs = [self.draw(value) for _ in range(2)]
        drawings = [d[0].get_drawings()[0] for d in docs]
        for dr in drawings:
            self.assertAlmostEqual(dr['stroke_opacity'], 64 / 255, places=5)
            for actual, expected in zip(dr['color'], (10, 20, 30)):
                self.assertAlmostEqual(actual, expected / 255, places=5)
        self.assertEqual(docs[0][0].get_pixmap().samples, docs[1][0].get_pixmap().samples)
        for d in docs:
            d.close()

    def test_sharp_turn_and_self_overlap_do_not_multiply_one_stroke_alpha(self):
        value = stroke(tool=15, widths=(8, 16, 24, 8), pressures=(0, 100, 200, 0),
                       rgba=(0, 0, 0, 128))
        value.points[0].x, value.points[0].y = 20, 20
        value.points[1].x, value.points[1].y = 60, 60
        value.points[2].x, value.points[2].y = 20, 60
        value.points[3].x, value.points[3].y = 60, 20
        doc = self.draw(value)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        self.assertGreaterEqual(pix.pixel(160, 160)[0], 126)
        self.assertLessEqual(pix.pixel(160, 160)[0], 128)
        doc.close()

    def test_crop_origin_and_inserted_note_keep_base_pdf_text(self):
        from rm_sync.renderer import PageInfo
        base = fitz.open(); page = base.new_page(width=300, height=400)
        page.insert_text((80, 100), 'base text')
        page.set_cropbox(fitz.Rect(20, 30, 280, 370))
        renderer = PDFRenderer()
        pages = [PageInfo(0, 'p', 0, None, None), PageInfo(1, 'note', None, None, None)]
        renderer.build_document(pages, base, StrokeProcessor(), lambda p: ([], []))
        try:
            result = renderer._doc[0]
            self.assertEqual(tuple(result.rect), (0, 0, 260, 340))
            self.assertEqual(result.get_text().strip(), 'base text')
            self.assertEqual(len(renderer._doc), 2)
            expected = base[0].get_text('words')[0]
            actual = result.get_text('words')[0]
            for a, b in zip(actual[:4], expected[:4]):
                self.assertAlmostEqual(a, b, places=4)
        finally:
            renderer.close(); base.close()


class RenderStateTests(unittest.TestCase):
    def test_page_content_and_renderer_changes_invalidate_without_mtime_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'doc').mkdir()
            page = root / 'doc' / 'page.rm'; page.write_bytes(b'first')
            out = root / 'result.pdf'; out.write_bytes(b'pdf')
            tracker = RenderStateTracker(root, root)
            tracker.record_rendered('doc', out)
            self.assertFalse(tracker.needs_render('doc'))
            stat = page.stat(); page.write_bytes(b'other')
            os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertTrue(tracker.needs_render('doc'))
            tracker.record_rendered('doc', out)
            with patch('rm_sync.main.RENDERER_REVISION', 'future'):
                self.assertTrue(tracker.needs_render('doc'))
            out.unlink()
            self.assertTrue(tracker.needs_render('doc'))

    def test_failed_decode_preserves_previous_output_and_state_even_on_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'doc').mkdir()
            (root / 'doc.metadata').write_text(json.dumps({'visibleName': 'New'}))
            (root / 'doc.content').write_text(json.dumps({'pages': ['p']}))
            (root / 'doc' / 'p.rm').write_bytes(b'broken')
            out = root / 'Old.pdf'; out.write_bytes(b'previous')
            tracker = RenderStateTracker(root, root)
            tracker.record_rendered('doc', out)
            before = dict(tracker._state)
            with self.assertRaises(ValueError):
                _process_document('doc', root, None, root, tracker, force=True)
            self.assertEqual(out.read_bytes(), b'previous')
            self.assertEqual(tracker._state, before)
            self.assertFalse((root / 'New.pdf').exists())

    def test_save_failure_keeps_previous_output_state_and_cleans_temporary_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'doc.metadata').write_text(json.dumps({'visibleName': 'Old'}))
            (root / 'doc.content').write_text(json.dumps({'pages': ['p']}))
            out = root / 'Old.pdf'; out.write_bytes(b'previous')
            tracker = RenderStateTracker(root, root)
            tracker.record_rendered('doc', out); before = dict(tracker._state)
            with patch('fitz.Document.save', side_effect=RuntimeError('disk full')):
                with self.assertRaisesRegex(RuntimeError, 'disk full'):
                    _process_document('doc', root, None, root, tracker, force=True)
            self.assertEqual(out.read_bytes(), b'previous')
            self.assertEqual(tracker._state, before)
            self.assertEqual(list(root.glob('*.pdf')), [out])


@unittest.skipUnless((FIXTURE / 'manifest.json').exists(), 'local Pen test fixture unavailable')
class PenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FIXTURE / 'manifest.json').read_text())

    def test_all_input_hashes_and_page_order(self):
        m = self.manifest
        for item in m['files'] + [dict(m['reference_pdf'], path=m['reference_pdf']['path_from_fixture'])]:
            data = (FIXTURE / item['path']).read_bytes()
            self.assertEqual(len(data), item['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        pages = DocumentParser(FIXTURE, m['document_uuid']).parse_pages()
        self.assertEqual([p.uuid for p in pages], [p['page_uuid'] for p in m['pages']])

    def test_all_323_lines_and_15258_points_preserve_decoder_values(self):
        counts = [0, 0]
        for page in self.manifest['pages']:
            path = FIXTURE / self.manifest['document_uuid'] / (page['page_uuid'] + '.rm')
            with path.open('rb') as fh:
                blocks = list(rmscene.read_blocks(fh))
            expected = {i: b.item.value for i, b in enumerate(blocks)
                        if hasattr(b, 'item') and hasattr(b.item.value, 'points')}
            p = StrokeProcessor(); strokes, _ = p.decode_rm_file(path)
            self.assertEqual(len(strokes), page['line_values'])
            for s in strokes:
                raw = expected[s.block_index]
                self.assertEqual(s.thickness_scale, raw.thickness_scale)
                self.assertEqual(s.tool, int(raw.tool))
                self.assertEqual(s.color_rgba, raw.color_rgba)
                self.assertEqual(len(s.points), len(raw.points))
                for a, b in zip(s.points, raw.points):
                    for field in ('x', 'y', 'width', 'speed', 'direction', 'pressure'):
                        self.assertEqual(getattr(a, field), getattr(b, field))
                counts[0] += 1; counts[1] += len(s.points)
            self.assertEqual(p.paper_size, (1404, 1872))
            self.assertEqual(sum(len(r['data']) for r in p.extensions), 111)
        self.assertEqual(counts, [323, 15258])

    def test_notebook_geometry_preserves_extended_page_origin_and_painted_bounds(self):
        m = self.manifest; processor = StrokeProcessor(); renderer = PDFRenderer()
        pages = DocumentParser(FIXTURE, m['document_uuid']).parse_pages()
        renderer.build_document(pages, None, processor, lambda p: processor.decode_rm_file(
            FIXTURE / m['document_uuid'] / (p.uuid + '.rm')))
        try:
            self.assertEqual([tuple(p.rect)[2:] for p in renderer._doc],
                             [tuple(p['pdf_size_pt']) for p in m['pages']])
            first = renderer._doc[1].get_drawings()[0]
            self.assertAlmostEqual(first['items'][0][1].y, 529.6787, places=3)
            self.assertAlmostEqual(first['items'][0][1].x, 53.62203, places=3)
            self.assertAlmostEqual(first['width'], 2 * 72 / 227, places=5)
        finally:
            renderer.close()

    def test_offline_cli_render_then_skip_with_partial_fidelity_state(self):
        from typer.testing import CliRunner
        from rm_sync.cli import app
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root / 'cache'; output = root / 'output'
            cache.mkdir(); uuid = self.manifest['document_uuid']
            shutil.copytree(FIXTURE / uuid, cache / uuid)
            for ext in ('.content', '.metadata'):
                shutil.copyfile(FIXTURE / (uuid + ext), cache / (uuid + ext))
            runner = CliRunner()
            command = ['render', '--cache', str(cache), '--output', str(output)]
            with patch('rm_sync.cli.RsyncClient.full_sync') as sync:
                first = runner.invoke(app, command)
                self.assertEqual(first.exit_code, 0, first.output)
                second = runner.invoke(app, command)
                self.assertEqual(second.exit_code, 0, second.output)
                self.assertIn('Skipped (unchanged)', second.output)
                self.assertIn('Rendered            │ 0', second.output)
                sync.assert_not_called()
            state = json.loads((cache / '.rm_render_state.json').read_text())[uuid]
            self.assertEqual(len(state['fidelity_warnings']), 9)
            self.assertTrue((output / 'Pen test.pdf').exists())


if __name__ == '__main__':
    unittest.main()
