import json
import subprocess
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from rm_sync.cli import app
from rm_sync.config import RM_ROOT


FIRST = "2b683e50-82bf-425d-95a2-21dd7909c84f"
SECOND = "fcf5d7ba-95e1-421b-a7d8-e03f1d634897"


def completed(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout=stdout)


class TriggerCommandTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.listing = completed(
            f"{RM_ROOT}/{SECOND}.metadata\n{RM_ROOT}/{FIRST}.metadata\n"
        )

    @patch("rm_sync.cli.time.time", return_value=100.0)
    @patch("rm_sync.cli.subprocess.run")
    def test_triggers_every_document_with_script_metadata(self, run, _time):
        run.side_effect = [
            self.listing,
            completed('{"visibleName":"First","version":7}'),
            completed(),
            completed('{"visibleName":"Second","version":4}'),
            completed(),
        ]

        result = self.runner.invoke(app, ["trigger", "--mode", "usb"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Triggered 2/2 document(s)", result.output)
        self.assertEqual(run.call_count, 5)
        for call, uuid, name in (
            (run.call_args_list[2], FIRST, "First"),
            (run.call_args_list[4], SECOND, "Second"),
        ):
            self.assertEqual(
                call.args[0],
                ["ssh", "remarkable-usb", f"cat > '{RM_ROOT}/{uuid}.metadata'"],
            )
            self.assertEqual(
                json.loads(call.kwargs["input"]),
                {
                    "visibleName": name,
                    "version": 0,
                    "lastModified": "100000",
                    "lastOpened": "88000",
                    "modified": True,
                    "synced": False,
                },
            )

    @patch("rm_sync.cli.subprocess.run")
    def test_dry_run_only_lists_documents(self, run):
        run.return_value = self.listing

        result = self.runner.invoke(app, ["--dry-run", "trigger", "--mode", "usb"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("would trigger 2 document(s)", result.output)
        run.assert_called_once()

    @patch("rm_sync.cli.subprocess.run")
    def test_continues_after_document_failure(self, run):
        run.side_effect = [
            self.listing,
            subprocess.CalledProcessError(1, ["ssh", "remarkable-usb"], stderr="read failed"),
            completed("{}"),
            completed(),
        ]

        result = self.runner.invoke(app, ["trigger", "--mode", "usb"])

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Triggered 1/2 document(s)", result.output)
        self.assertEqual(run.call_count, 4)


if __name__ == "__main__":
    unittest.main()
