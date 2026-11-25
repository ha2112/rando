import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError


spec = importlib.util.spec_from_file_location(
    "uploader", Path(__file__).with_name("youtube_footage_uploader.py")
)
uploader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploader)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.token = Path(self.temp_dir.name) / "token.json"
        self.token.write_text("existing token")
        self.creds = Mock(valid=False, expired=True, refresh_token="test")
        self.creds.to_json.return_value = "refreshed token"

        patches = (
            patch.object(uploader, "TOKEN_FILE", self.token),
            patch.object(uploader.Credentials, "from_authorized_user_file", return_value=self.creds),
            patch.object(uploader, "Request"),
            patch.object(uploader, "build"),
            patch.object(uploader.time, "sleep"),
        )
        self.mocks = [self.enterContext(item) for item in patches]
        self.build = self.mocks[3]
        self.sleep = self.mocks[4]

    def test_temporary_dns_failure_recovers(self):
        self.creds.refresh.side_effect = [TransportError("DNS lookup failed"), None]

        service = uploader.get_authenticated_service()

        self.assertIs(service, self.build.return_value)
        self.assertEqual(self.creds.refresh.call_count, 2)
        self.sleep.assert_called_once_with(2)
        self.assertEqual(self.token.read_text(), "refreshed token")

    def test_persistent_network_failure_keeps_existing_token(self):
        self.creds.refresh.side_effect = TransportError("DNS lookup failed")

        with self.assertRaises(TransportError):
            uploader.get_authenticated_service()

        self.assertEqual(self.creds.refresh.call_count, uploader.MAX_RETRIES + 1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [2, 4, 8, 16, 32])
        self.assertEqual(self.token.read_text(), "existing token")
        self.build.assert_not_called()

    def test_invalid_credentials_are_not_retried(self):
        self.creds.refresh.side_effect = RefreshError("invalid_grant")

        with self.assertRaises(RefreshError):
            uploader.get_authenticated_service()

        self.creds.refresh.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(self.token.read_text(), "existing token")

    def test_main_explains_connection_failure(self):
        with (
            patch.object(uploader, "FOOTAGE_FOLDER", self.temp_dir.name),
            patch.object(uploader.sys, "argv", ["uploader", "--live"]),
            patch.object(uploader, "get_authenticated_service", side_effect=TransportError("DNS lookup failed")),
        ):
            with self.assertRaisesRegex(SystemExit, "Check your internet connection, DNS, and VPN"):
                uploader.main()

        self.build.assert_not_called()


class UploadLoggingTests(unittest.TestCase):
    def setUp(self):
        self.file_path = Mock(spec=Path)
        self.file_path.name = "video.mp4"
        self.file_path.stat.return_value.st_size = 4 * 1024 ** 3
        self.youtube = Mock()
        self.request = self.youtube.videos.return_value.insert.return_value
        self.enterContext(patch.object(uploader, "LOG_MODE", True))
        self.enterContext(patch.object(uploader, "MediaFileUpload"))
        self.print = self.enterContext(patch("builtins.print"))
        self.sleep = self.enterContext(patch.object(uploader.time, "sleep"))
        self.clock = self.enterContext(patch.object(uploader.time, "monotonic"))

    def progress_lines(self):
        return [call.args[0] for call in self.print.call_args_list if "[PROGRESS]" in call.args[0]]

    def test_periodic_progress_and_completion_are_flushed(self):
        self.clock.side_effect = [0, 10, 30, 40]
        self.request.next_chunk.side_effect = [
            (Mock(resumable_progress=1024 ** 3), None),
            (Mock(resumable_progress=2 * 1024 ** 3), None),
            (None, {"id": "video-id"}),
        ]

        result = uploader.upload_video(self.youtube, self.file_path, "title", "description")

        self.assertEqual(result, "video-id")
        lines = self.progress_lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("0.0%", lines[0])
        self.assertIn("50.0%", lines[1])
        self.assertIn("2147483648/4294967296 bytes", lines[1])
        self.assertIn("68.27 MiB/s", lines[1])
        self.assertIn("100.0%", lines[2])
        self.assertIn("4294967296/4294967296 bytes", lines[2])
        self.assertTrue(all("\r" not in line and "\x1b" not in line for line in lines))
        for call in self.print.call_args_list:
            if "[PROGRESS]" in call.args[0]:
                self.assertTrue(call.kwargs.get("flush"))

    def test_retry_does_not_count_bytes_twice(self):
        self.clock.side_effect = [0, 30, 60]
        error = HttpError(Mock(status=503, reason="Unavailable"), b'{}')
        self.request.next_chunk.side_effect = [
            error,
            (Mock(resumable_progress=1024 ** 3), None),
            (None, {"id": "video-id"}),
        ]

        uploader.upload_video(self.youtube, self.file_path, "title", "description")

        self.sleep.assert_called_once_with(2)
        self.assertIn("25.0%", self.progress_lines()[1])
        self.assertIn("1073741824/4294967296 bytes", self.progress_lines()[1])

    def test_failure_does_not_log_completion(self):
        self.clock.side_effect = [0]
        self.request.next_chunk.side_effect = HttpError(Mock(status=403, reason="Forbidden"), b'{}')

        with self.assertRaises(HttpError):
            uploader.upload_video(self.youtube, self.file_path, "title", "description")

        self.assertEqual(len(self.progress_lines()), 1)
        self.assertFalse(any("[OK]" in call.args[0] for call in self.print.call_args_list))


if __name__ == "__main__":
    unittest.main()
