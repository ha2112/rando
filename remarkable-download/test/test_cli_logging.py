import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rm_sync.cli import app


class LoggingCommandTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.root = logging.getLogger()
        self.addCleanup(self.root.setLevel, self.root.level)

    def render(self, flags, fail=False):
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)

        def process(**kwargs):
            for name in ("rm-rebuilder.parser", "rmscene.tagged_block_reader"):
                logger = logging.getLogger(name)
                logger.debug("debug diagnostic")
                logger.info("info diagnostic")
                logger.warning("warning diagnostic")
            if fail:
                raise ValueError("invalid scene")
            return True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(self.root, "handlers", [handler]),
                patch("rm_sync.cli.discover_cached_uuids", return_value=["test-uuid"]),
                patch("rm_sync.cli._process_document", side_effect=process),
            ):
                result = self.runner.invoke(
                    app,
                    flags + ["render", "--cache", str(root / "cache"),
                             "--output", str(root / "output")],
                )
        return result, logs.getvalue()

    def test_default_shows_progress_without_diagnostic_logs(self):
        result, logs = self.render([])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Render Summary", result.output)
        self.assertEqual(logs, "")

    def test_verbose_shows_debug_info_and_warnings(self):
        for flag in ("--verbose", "-v"):
            with self.subTest(flag=flag):
                result, logs = self.render([flag])

                self.assertEqual(result.exit_code, 0, result.output)
                for message in ("debug diagnostic", "info diagnostic", "warning diagnostic"):
                    self.assertEqual(logs.count(message), 2)

    def test_default_after_verbose_hides_diagnostic_logs(self):
        self.render(["--verbose"])
        result, logs = self.render([])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(logs, "")

    def test_failure_stays_visible_and_traceback_requires_verbose(self):
        for flags in ([], ["--verbose"]):
            with self.subTest(flags=flags):
                result, logs = self.render(flags, fail=True)

                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("invalid scene", result.output)
                if flags:
                    self.assertIn("Traceback", logs)
                else:
                    self.assertEqual(logs, "")


if __name__ == "__main__":
    unittest.main()
