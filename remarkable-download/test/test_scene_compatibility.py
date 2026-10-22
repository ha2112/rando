"""Compatibility regressions for the two supplied October 2026 exports."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import rmscene
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequence, CrdtSequenceItem
from rmscene.scene_stream import RootTextBlock, SceneLineItemBlock, TreeNodeBlock
from rmscene.tagged_block_common import CrdtId, LwwValue

from rm_sync.parser import DocumentParser, StrokeProcessor
from rm_sync.renderer import PDFRenderer

FIXTURE = Path(__file__).resolve().parents[1] / 'test_data/compatibility'
ZERO, ROOT = CrdtId(0, 0), CrdtId(0, 1)


def text_blocks(text='', anchored=True, style=si.ParagraphStyle.HEADING, anchor=None):
    items = [] if not text else [CrdtSequenceItem(CrdtId(1, 2), ZERO, ZERO, 0, text)]
    root_text = si.Text(CrdtSequence(items), {ZERO: LwwValue(ZERO, style)}, -370, 234, 740)
    group = si.Group(ROOT)
    if anchored:
        group.anchor_id = LwwValue(ZERO, anchor or CrdtId(0, 281474976710654))
        group.anchor_origin_x = LwwValue(ZERO, -366)
        group.anchor_type = LwwValue(ZERO, 2)
    line = si.Line(si.PenColor.BLACK, si.Pen.FINELINER_2,
                   [si.Point(0, -200, 3, 4, 16, 0), si.Point(10, -190, 7, 8, 16, 100)], 2, 0)
    return [RootTextBlock(ZERO, root_text), TreeNodeBlock(group),
            SceneLineItemBlock(ROOT, CrdtSequenceItem(CrdtId(1, 4), ZERO, ZERO, 0, line))]


def decode_blocks(blocks):
    processor = StrokeProcessor()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'test.rm'; path.touch()
        with patch('rmscene.read_blocks', return_value=blocks):
            strokes, highlights = processor.decode_rm_file(path)
    return processor, strokes, highlights


class EmptyTextCompatibilityTests(unittest.TestCase):
    def test_empty_heading_is_not_visible_typed_text_and_preserves_raw_measurements(self):
        for anchor in (281474976710654, 281474976710655):
            with self.subTest(anchor=anchor):
                processor, strokes, _ = decode_blocks(text_blocks(anchor=CrdtId(0, anchor)))
                self.assertEqual(len(strokes), 1)
                self.assertEqual((strokes[0].points[0].x, strokes[0].points[0].y), (0, -200))
                self.assertEqual(strokes[0].points[0].pressure, 0)
                scaled = processor.scale_strokes(strokes, 1404, 1872, source_size=(1404, 1872))[0]
                self.assertAlmostEqual(scaled.points[0].x, 336)
                self.assertAlmostEqual(scaled.points[0].y, 97.1875)
                self.assertEqual(scaled.points[0].width, 16)

    def test_empty_unanchored_text_container_does_not_move_ink(self):
        processor, strokes, _ = decode_blocks(text_blocks(anchored=False))
        scaled = processor.scale_strokes(strokes, 1404, 1872, source_size=(1404, 1872))[0]
        self.assertEqual((scaled.points[0].x, scaled.points[0].y), (702, -200))

    def test_actual_typed_text_still_fails_instead_of_disappearing(self):
        with self.assertRaisesRegex(ValueError, 'Typed scene text'):
            decode_blocks(text_blocks(text='Keep this visible'))

    def test_unverified_anchor_profiles_still_fail(self):
        for blocks in (text_blocks(style=si.ParagraphStyle.PLAIN),
                       text_blocks(anchor=CrdtId(1, 999))):
            with self.assertRaisesRegex(ValueError, 'anchor'):
                decode_blocks(blocks)


@unittest.skipUnless((FIXTURE / 'manifest.json').exists(), 'local compatibility fixtures unavailable')
class ExportCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FIXTURE / 'manifest.json').read_text())

    def test_fixture_and_export_hashes(self):
        for item in self.manifest['files']:
            data = (FIXTURE / item['path']).read_bytes()
            self.assertEqual(len(data), item['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        for doc in self.manifest['documents']:
            ref = doc['reference']
            self.assertEqual(hashlib.sha256((FIXTURE / ref['path_from_fixture']).read_bytes()).hexdigest(), ref['sha256'])

    def test_nhap_first_page_242_anchored_paths_match_export_without_coordinate_fitting(self):
        doc = self.manifest['documents'][0]; processor = StrokeProcessor()
        strokes, highlights = processor.decode_rm_file(FIXTURE / doc['uuid'] / (doc['failing_page'] + '.rm'))
        self.assertEqual(len(strokes), 242)
        renderer = PDFRenderer(); page_info = DocumentParser(FIXTURE, doc['uuid']).parse_pages()[0]
        renderer.build_document([page_info], None, processor, lambda p: (strokes, highlights))
        reference = fitz.open(FIXTURE / doc['reference']['path_from_fixture'])
        try:
            page = renderer._doc[0]
            self.assertEqual(tuple(page.rect), tuple(reference[0].rect))
            expected = [p for p in reference[0].get_drawings() if p['width'] > .2]
            actual = page.get_drawings()
            self.assertEqual(len(actual), len(expected))
            for a, b in zip(actual, expected):
                self.assertEqual(len(a['items']), len(b['items']))
                self.assertAlmostEqual(a['width'], b['width'], places=5)
                for ai, bi in zip(a['items'], b['items']):
                    self.assertEqual(ai[0], bi[0])
                    for ap, bp in zip(ai[1:], bi[1:]):
                        self.assertAlmostEqual(ap.x, bp.x, places=3)
                        self.assertAlmostEqual(ap.y, bp.y, places=3)
        finally:
            reference.close(); renderer.close()

    def test_book_unknown_pair_preserves_base_page_pixels_and_reports_partial_fidelity(self):
        doc = self.manifest['documents'][1]; processor = StrokeProcessor()
        strokes, highlights = processor.decode_rm_file(FIXTURE / doc['uuid'] / (doc['failing_page'] + '.rm'))
        self.assertEqual((strokes, highlights), ([], []))
        self.assertEqual([b['block_type'] for b in processor.unsupported_blocks], [14, 15])
        self.assertEqual(sum(len(b['data']) for b in processor.unsupported_blocks), 23)
        renderer = PDFRenderer(); base = fitz.open(FIXTURE / (doc['uuid'] + '.pdf'))
        reference = fitz.open(FIXTURE / doc['reference']['path_from_fixture'])
        page_info = DocumentParser(FIXTURE, doc['uuid']).parse_pages()[0]
        renderer.build_document([page_info], base, processor, lambda p: (strokes, highlights))
        try:
            self.assertEqual(renderer._doc[0].get_pixmap().samples, reference[0].get_pixmap().samples)
            self.assertEqual(len(renderer.fidelity_warnings[0]['unsupported_blocks']), 2)
        finally:
            renderer.close(); base.close(); reference.close()

    def test_changed_unknown_payload_or_version_remains_a_failure(self):
        from dataclasses import replace
        doc = self.manifest['documents'][1]
        with (FIXTURE / doc['uuid'] / (doc['failing_page'] + '.rm')).open('rb') as fh:
            blocks = list(rmscene.read_blocks(fh))
        index = next(i for i, b in enumerate(blocks) if b.__class__.__name__ == 'UnreadableBlock')
        for change in (replace(blocks[index], data=b'changed'),
                       replace(blocks[index], info=replace(blocks[index].info, current_version=4))):
            modified = list(blocks); modified[index] = change
            with self.assertRaisesRegex(ValueError, 'Unsupported scene block'):
                decode_blocks(modified)


if __name__ == '__main__':
    unittest.main()
