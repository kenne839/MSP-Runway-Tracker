"""
Unit tests for OpenSky authentication, credential discovery, and rate-limit handling.
"""

import os
import sys
import json
import time
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpi.opensky_auth import OpenSkyAuth
from rpi.tracker import TelemetryTracker


class TestOpenSkyAuth(unittest.TestCase):
    def setUp(self):
        # Save original environment
        self.orig_env = {
            "OPENSKY_CLIENT_ID": os.environ.get("OPENSKY_CLIENT_ID"),
            "OPENSKY_CLIENT_SECRET": os.environ.get("OPENSKY_CLIENT_SECRET"),
            "OPENSKY_API_KEY": os.environ.get("OPENSKY_API_KEY"),
            "OPENSKY_TOKEN": os.environ.get("OPENSKY_TOKEN"),
            "OPENSKY_USERNAME": os.environ.get("OPENSKY_USERNAME"),
            "OPENSKY_PASSWORD": os.environ.get("OPENSKY_PASSWORD"),
        }
        for k in self.orig_env:
            os.environ.pop(k, None)

    def tearDown(self):
        # Restore original environment
        for k, v in self.orig_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_anonymous_mode(self):
        """Test default unauthenticated / anonymous behavior."""
        auth = OpenSkyAuth(project_root=tempfile.gettempdir())
        self.assertFalse(auth.is_authenticated())
        self.assertIn("Anonymous", auth.get_auth_status_str())
        self.assertIn("400 req/day", auth.get_auth_status_str())
        headers = auth.get_headers()
        self.assertNotIn("Authorization", headers)
        self.assertIsNone(auth.get_basic_auth())

    def test_api_token_auth(self):
        """Test authentication via OPENSKY_API_KEY / OPENSKY_TOKEN."""
        os.environ["OPENSKY_API_KEY"] = "mock_api_token_12345"
        auth = OpenSkyAuth(project_root=tempfile.gettempdir())
        self.assertTrue(auth.is_authenticated())
        self.assertIn("API Token", auth.get_auth_status_str())
        headers = auth.get_headers()
        self.assertEqual(headers.get("Authorization"), "Bearer mock_api_token_12345")

    def test_basic_auth_fallback(self):
        """Test legacy username and password configuration."""
        os.environ["OPENSKY_USERNAME"] = "pilot_user"
        os.environ["OPENSKY_PASSWORD"] = "secret_pass"
        auth = OpenSkyAuth(project_root=tempfile.gettempdir())
        self.assertTrue(auth.is_authenticated())
        self.assertIn("Basic Auth", auth.get_auth_status_str())
        self.assertEqual(auth.get_basic_auth(), ("pilot_user", "secret_pass"))

    def test_json_credentials_file_discovery(self):
        """Test discovery and parsing of downloaded credentials.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = os.path.join(tmpdir, "credentials.json")
            with open(cred_file, "w", encoding="utf-8") as f:
                json.dump({
                    "client_id": "client_abc_123",
                    "client_secret": "secret_xyz_789"
                }, f)

            auth = OpenSkyAuth(project_root=tmpdir)
            self.assertTrue(auth.is_authenticated())
            self.assertEqual(auth.client_id, "client_abc_123")
            self.assertEqual(auth.client_secret, "secret_xyz_789")
            self.assertIn("OAuth2", auth.get_auth_status_str())

    def test_tracker_lockout_backoff(self):
        """Verify TelemetryTracker suppresses queries during rate-limit lockout period."""
        tracker = TelemetryTracker()
        # Simulate active lockout until 1 hour from now
        tracker.opensky_lockout_until = time.time() + 3600
        result = tracker.fetch_opensky()
        self.assertEqual(result, [])
        self.assertFalse(tracker.last_fetch_success)


if __name__ == "__main__":
    unittest.main()
