"""Capture-tool regressions using the image records from Nhap copy."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import fitz
from rmscene import scene_items as si
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.scene_stream import SceneLineItemBlock, SceneTreeBlock, SceneGroupItemBlock, TreeNodeBlock
from rmscene.tagged_block_common import CrdtId, LwwValue
from rmscene.tagged_block_writer import TaggedBlockWriter

from rm_sync.main import RenderStateTracker, _process_document
from rm_sync.parser import StrokeProcessor
from rm_sync.renderer import PDFRenderer, PageInfo


# Exact 0x0e and 0x0f payloads in the supplied notebook's capture page.
ASSETS = bytes.fromhex(
    '1c5c000000010c560000002c4f725effa473b91e4d5680ffb7cf2d'
    '1c320000001f010e2c2a0000002801'
    '64306636373430322d326262622d343563352d383562622d6632393332333164623961362e706e67'
    '2c0a0000001f00002c020000001100')
IMAGE = bytes.fromhex(
    '1f000b2f010f3f00004f000054000000006c8500000007'
    '1c180000001f01132c100000002c4f725effa473b91e4d5680ffb7cf2d2f0112'
    '3c4100000010ca806ec2416111440000000000000000'
    '32a05b43416111440000803f00000000'
    '32a05b43bf1e65440000803f0000803f'
    'ca806ec2bf1e6544000000000000803f'
    '4c1900000006000000000100000002000000020000000300000000000000')
# Pasted/moved snippets append tagged ID 5 (an earlier image item) inside subblock 6.
PASTED_IMAGE = IMAGE.replace(bytes.fromhex('6c85'), bytes.fromhex('6c88'), 1) + bytes.fromhex('5f0128')
PNG_NAME = 'd0f67402-2bbb-45c5-85bb-f293231db9a6.png'
ZERO, ROOT, LAYER = CrdtId(0, 0), CrdtId(0, 1), CrdtId(0, 11)


def write_scene(root, *, assets=ASSETS, image=IMAGE, visible=True, ink=False,
                asset_version=(3, 3), image_version=(2, 2)):
    folder = root / 'doc'
    folder.mkdir(exist_ok=True)
    path = folder / 'page.rm'
    with path.open('wb') as fh:
        writer = TaggedBlockWriter(fh)
        writer.write_header()
        with writer.write_block(14, *asset_version):
            writer.data.write_bytes(assets)
        blocks = [SceneTreeBlock(LAYER, ZERO, True, ROOT),
                  TreeNodeBlock(si.Group(LAYER, visible=LwwValue(ZERO, visible))),
                  SceneGroupItemBlock(ROOT, CrdtSequenceItem(CrdtId(0, 13), ZERO, ZERO, 0, LAYER))]
        if ink:
            line = si.Line(si.PenColor.BLACK, si.Pen.FINELINER_2,
                           [si.Point(0, 600, 0, 0, 80, 255)], 1, 0)
            blocks.append(SceneLineItemBlock(LAYER, CrdtSequenceItem(CrdtId(1, 20), ZERO, ZERO, 0, line)))
        for block in blocks:
            block.write(writer)
        if image is not None:
            with writer.write_block(15, *image_version):
                writer.data.write_bytes(image)
    return path


def write_png(root):
    path = root / 'doc' / 'page' / PNG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Distinct corners expose UV flips; the final corner exposes lost alpha.
    samples = bytes([255, 0, 0, 255, 0, 255, 0, 255,
                     0, 0, 255, 255, 0, 0, 0, 0])
    fitz.Pixmap(fitz.csRGB, 2, 2, samples, True).save(path)
    return path


class CaptureImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def decode(self, **kwargs):
        path = write_scene(self.root, **kwargs)
        processor = StrokeProcessor()
        strokes, highlights = processor.decode_rm_file(path)
        return processor, strokes, highlights

    def test_observed_capture_resolves_asset_and_mesh_in_scene_order(self):
        png = write_png(self.root)
        processor, strokes, highlights = self.decode(ink=True)
        self.assertEqual(len(processor.images), 1)
        image = processor.images[0]
        self.assertEqual(image.data, png.read_bytes())
        self.assertEqual(image.triangles, [(0, 1, 2), (2, 3, 0)])
        self.assertEqual(image.vertices[0][2:], (0, 0))
        self.assertAlmostEqual(image.vertices[0][0], -59.62577, places=4)
        self.assertAlmostEqual(image.vertices[0][1], 581.51959, places=4)
        self.assertEqual((image.scene_order, strokes[0].scene_order), (0, 1))
        self.assertEqual(highlights, [])
        self.assertEqual(processor.unsupported_blocks, [])

    def test_pasted_snippet_with_source_item_id_renders_same_mesh(self):
        write_png(self.root)
        processor, _, _ = self.decode()
        pasted, _, _ = self.decode(image=PASTED_IMAGE)
        self.assertEqual(len(pasted.images), 1)
        self.assertEqual(pasted.images[0].vertices, processor.images[0].vertices)
        self.assertEqual(pasted.images[0].triangles, processor.images[0].triangles)

    def test_unknown_field_after_pasted_snippet_id_remains_failure(self):
        write_png(self.root)
        # Same length change, but tag index 6 instead of the observed index 5.
        unknown = PASTED_IMAGE[:-3] + bytes.fromhex('6f0128')
        with self.assertRaisesRegex(ValueError, 'Unsupported scene block'):
            self.decode(image=unknown)

    def test_empty_asset_table_and_image_tombstone_need_no_png(self):
        processor, strokes, highlights = self.decode(
            assets=bytes.fromhex('1c0100000000'),
            image=bytes.fromhex('1f000b2f01143f00004f00005401000000'))
        self.assertEqual((strokes, highlights, processor.images), ([], [], []))
        self.assertEqual(processor.unsupported_blocks, [])
        # feliz navidad has just the empty table, without an image item.
        processor, _, _ = self.decode(assets=bytes.fromhex('1c0100000000'), image=None)
        self.assertEqual(processor.images, [])

    def test_hidden_and_deleted_images_do_not_require_the_asset(self):
        processor, _, _ = self.decode(visible=False)
        self.assertEqual(processor.images, [])
        deleted = IMAGE.replace(bytes.fromhex('5400000000'), bytes.fromhex('5401000000'), 1)
        processor, _, _ = self.decode(image=deleted)
        self.assertEqual(processor.images, [])

    def test_missing_asset_is_an_explicit_failure(self):
        with self.assertRaisesRegex(ValueError, 'image asset'):
            self.decode()

    def test_images_reset_when_next_page_has_no_scene_file(self):
        write_png(self.root)
        processor, _, _ = self.decode()
        self.assertEqual(len(processor.images), 1)
        self.assertEqual(processor.decode_rm_file(self.root / 'missing.rm'), ([], []))
        self.assertEqual(processor.images, [])

    def test_invalid_records_and_unverified_versions_remain_failures(self):
        write_png(self.root)
        for kwargs in ({'assets': b'changed'}, {'image': IMAGE[:-1]},
                       {'asset_version': (3, 4)}, {'image_version': (2, 3)},
                       {'image': IMAGE[:-4] + bytes.fromhex('63000000')}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'Unsupported scene block'):
                self.decode(**kwargs)

    def test_scaling_keeps_uvs_and_applies_scene_offset(self):
        write_png(self.root)
        processor, _, _ = self.decode()
        original = processor.images[0]
        image = replace(original, scene_offset=(12, 34))
        result = processor.scale_images([image], 2808, 936, source_size=(1404, 1872),
                                        x_origin_pt=7, y_origin_pt=11)[0]
        x, y, u, v = original.vertices[0]
        self.assertEqual(result.vertices[0], ((x + 12 + 702) * 2 + 7, (y + 34) / 2 + 11, u, v))
        self.assertEqual(result.scene_offset, (0, 0))
        self.assertEqual(original.vertices[0], (x, y, u, v))

    def test_capture_renders_on_notebook_and_pdf_with_alpha_and_uv_orientation(self):
        write_png(self.root)
        for pdf_backed in (False, True):
            with self.subTest(pdf_backed=pdf_backed):
                processor, strokes, highlights = self.decode()
                processor.paper_size = (1404, 1872)
                base = fitz.open()
                base.new_page(width=446, height=594).draw_rect(fitz.Rect(0, 0, 446, 594), fill=(1, 1, 0))
                renderer = PDFRenderer()
                self.addCleanup(renderer.close)
                self.addCleanup(base.close)
                info = PageInfo(0, 'page', 0 if pdf_backed else None, None, None)
                renderer.build_document([info], base if pdf_backed else None,
                                        processor, lambda p: (strokes, highlights))
                page = renderer._doc[0]
                pix = page.get_pixmap()
                vertices = processor.scale_images(processor.images, 446 if pdf_backed else 1404 * 72 / 227,
                                                   594 if pdf_backed else 1872 * 72 / 227,
                                                   is_pdf_page=pdf_backed)[0].vertices
                x0, y0 = vertices[0][:2]
                x1, y1 = vertices[2][:2]
                expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                            (255, 255, 0) if pdf_backed else (255, 255, 255)]
                for (u, v), color in zip(((.1, .1), (.9, .1), (.1, .9), (.9, .9)), expected):
                    self.assertEqual(pix.pixel(round(x0 + u * (x1 - x0)), round(y0 + v * (y1 - y0))), color)

    def test_image_bounds_extend_page_and_paint_order_includes_ink(self):
        write_png(self.root)
        processor, strokes, highlights = self.decode(ink=True)
        processor.paper_size = (1404, 1872)
        processor.images = [replace(processor.images[0], scene_offset=(0, 1600))]
        renderer = PDFRenderer()
        self.addCleanup(renderer.close)
        renderer.build_document([PageInfo(0, 'page', None, None, None)], None,
                                processor, lambda p: (strokes, highlights))
        expected_bottom = (processor.images[0].vertices[2][1] + 1600) * 72 / 227
        self.assertAlmostEqual(renderer._doc[0].rect.height, expected_bottom, places=4)
        with patch.object(renderer, '_draw_stroke') as ink, patch.object(renderer, '_draw_image') as image:
            events = []
            ink.side_effect = lambda *args: events.append('ink')
            image.side_effect = lambda *args: events.append('image')
            renderer._draw_items(None, strokes, highlights, processor.images)
            self.assertEqual(events, ['image', 'ink'])

    def test_rotated_mesh_clips_the_image_and_keeps_texture_orientation(self):
        write_png(self.root)
        processor, _, _ = self.decode()
        # Rotate the texture clockwise into a diamond and retain only one triangle.
        image = replace(processor.images[0], vertices=[(50, 10, 0, 0), (90, 50, 1, 0),
                        (50, 90, 1, 1), (10, 50, 0, 1)], triangles=[(0, 1, 2)])
        renderer = PDFRenderer()
        self.addCleanup(renderer.close)
        renderer._doc = fitz.open()
        page = renderer._doc.new_page(width=100, height=100)
        renderer._draw_image(page, image)
        pix = page.get_pixmap()
        self.assertEqual(pix.pixel(50, 20), (255, 0, 0))
        self.assertEqual(pix.pixel(80, 50), (0, 255, 0))
        self.assertEqual(pix.pixel(20, 50), (255, 255, 255))
        self.assertEqual(pix.pixel(90, 10), (255, 255, 255))

    def test_png_content_addition_and_removal_invalidate_render_state(self):
        write_scene(self.root)
        png = write_png(self.root)
        output = self.root / 'result.pdf'
        output.write_bytes(b'previous')
        tracker = RenderStateTracker(self.root, self.root)
        tracker.record_rendered('doc', output)
        self.assertFalse(tracker.needs_render('doc'))
        stat = png.stat()
        png.write_bytes(b'changed')
        os.utime(png, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertTrue(tracker.needs_render('doc'))
        tracker.record_rendered('doc', output)
        png.unlink()
        self.assertTrue(tracker.needs_render('doc'))
        tracker.record_rendered('doc', output)
        write_png(self.root)
        self.assertTrue(tracker.needs_render('doc'))

    def test_offline_pipeline_and_failed_asset_preserve_previous_output(self):
        write_scene(self.root)
        png = write_png(self.root)
        (self.root / 'doc.metadata').write_text(json.dumps({'visibleName': 'Capture'}))
        (self.root / 'doc.content').write_text(json.dumps({'pages': ['page']}))
        tracker = RenderStateTracker(self.root, self.root)
        self.assertTrue(_process_document('doc', self.root, None, self.root, tracker))
        self.assertFalse(_process_document('doc', self.root, None, self.root, tracker))
        output = self.root / 'Capture.pdf'
        before = output.read_bytes()
        state = dict(tracker._state)
        for data in (None, b'corrupt image'):
            if data is None:
                png.unlink()
            else:
                png.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'image asset'):
                _process_document('doc', self.root, None, self.root, tracker)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(tracker._state, state)


if __name__ == '__main__':
    unittest.main()
