import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rm_sync.cli import app
from rm_sync.main import RenderStateTracker, discover_cached_uuids


class DeletedTabletDocumentTests(unittest.TestCase):
    def test_full_sync_moves_untracked_deleted_document_output_to_trash(self):
        for force in (False, True):
            with self.subTest(force=force), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache = root / "cache"
                output = root / "output"
                cache.mkdir()
                output.mkdir()

                deleted_folder = output / "Deleted folder" / "Nested"
                deleted_folder.mkdir(parents=True)
                stale = deleted_folder / "Deleted note.pdf"
                stale.write_bytes(b"previously rendered PDF")
                old_empty = output / "Old empty folder" / "Child"
                old_empty.mkdir(parents=True)
                folder_uuid = "33333333-3333-3333-3333-333333333333"
                (cache / f"{folder_uuid}.metadata").write_text(
                    json.dumps({"visibleName": "Current empty folder", "type": "CollectionType"})
                )
                current_empty = output / "Current empty folder"
                kept_folder = output / "Current folder"
                kept_folder.mkdir()
                (kept_folder / "keep.txt").write_text("keep")
                empty_trash_folder = output / ".TRASH" / "Deleted folder" / "Child"
                empty_trash_folder.mkdir(parents=True)
                if force:
                    previous_trash = output / ".TRASH" / "older.pdf"
                    previous_trash.parent.mkdir(exist_ok=True)
                    previous_trash.write_bytes(b"older deleted PDF")

                with patch("rm_sync.cli.RsyncClient.full_sync") as full_sync:
                    result = CliRunner().invoke(
                        app,
                        (["--force"] if force else [])
                        + ["sync", "--mode", "usb", "--cache", str(cache), "--output", str(output)],
                    )

                full_sync.assert_called_once_with()
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertFalse(stale.exists())
                self.assertTrue((output / ".TRASH" / stale.name).is_file())
                self.assertFalse(deleted_folder.parent.exists())
                self.assertFalse(old_empty.parent.exists())
                self.assertTrue(current_empty.is_dir())
                self.assertTrue(kept_folder.is_dir())
                self.assertFalse(empty_trash_folder.parent.exists())
                self.assertTrue((output / ".TRASH").is_dir())
                if force:
                    self.assertFalse(previous_trash.exists())

                offline = CliRunner().invoke(
                    app, ["render", "--cache", str(cache), "--output", str(output)]
                )
                self.assertEqual(offline.exit_code, 0, offline.output)
                self.assertTrue(current_empty.is_dir())

    def test_untracked_output_for_current_document_stays_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            output = root / "output"
            cache.mkdir()
            output.mkdir()
            uuid = "11111111-1111-1111-1111-111111111111"
            (cache / f"{uuid}.metadata").write_text(
                json.dumps({"visibleName": "Current note", "type": "DocumentType"})
            )
            current = output / "Current note.pdf"
            current.write_bytes(b"existing PDF")

            moved = RenderStateTracker(cache, output).move_untracked_outputs()

            self.assertEqual(moved, 0)
            self.assertTrue(current.is_file())

    def test_deleted_metadata_moves_rendered_output_to_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            output = root / "output"
            cache.mkdir()
            output.mkdir()
            uuid = "22222222-2222-2222-2222-222222222222"
            (cache / f"{uuid}.metadata").write_text(
                json.dumps({"visibleName": "Deleted note", "type": "DocumentType", "deleted": True})
            )
            (cache / f"{uuid}.content").write_text(json.dumps({"pages": [uuid]}))
            pdf = output / "Deleted note.pdf"
            pdf.write_bytes(b"existing PDF")
            tracker = RenderStateTracker(cache, output)
            tracker.record_rendered(uuid, pdf)

            moved = tracker.scan_and_move_trash()

            self.assertEqual(moved, 1)
            self.assertFalse(pdf.exists())
            self.assertTrue((output / ".TRASH" / pdf.name).is_file())
            self.assertNotIn(uuid, discover_cached_uuids(cache))


if __name__ == "__main__":
    unittest.main()
