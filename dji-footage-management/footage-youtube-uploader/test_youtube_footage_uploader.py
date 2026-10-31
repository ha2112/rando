import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError, TransportError


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


if __name__ == "__main__":
    unittest.main()
