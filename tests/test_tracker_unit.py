"""
Unit tests for TelemetryState snapshot immutability, ESP32 heartbeat parsing,
and OpenSky UTC midnight quota reset logic.
"""

import time
import datetime
import unittest
from unittest.mock import patch, MagicMock

from rpi.tracker import TelemetryState, TelemetryTracker


class TestTrackerUnit(unittest.TestCase):
    def setUp(self):
        self.state = TelemetryState()

    def test_snapshot_immutability(self):
        """Verify that modifying a snapshot does not mutate the internal TelemetryState (tests deepcopy)."""
        snapshot = self.state.get_snapshot()
        self.assertEqual(snapshot["tracked_count"], 0)

        # Mutate the snapshot
        snapshot["tracked_count"] = 99
        snapshot["active_operations"].append({"runway": "30R", "action": "LANDING"})

        # Subsequent fetch of state must remain untainted
        fresh_snapshot = self.state.get_snapshot()
        self.assertEqual(fresh_snapshot["tracked_count"], 0)
        self.assertEqual(len(fresh_snapshot["active_operations"]), 0)

    def test_esp32_heartbeat_update(self):
        """Test telemetry heartbeat ingestion from ESP32 headers."""
        self.state.update_esp32_heartbeat(
            ip="192.168.0.214",
            rssi=-28,
            heap=273408,  # ~267 KB
            uptime=3600,
            fps=72
        )

        snapshot = self.state.get_snapshot()
        node = snapshot.get("esp32_node")
        self.assertIsNotNone(node)
        self.assertEqual(node["ip"], "192.168.0.214")
        self.assertEqual(node["rssi"], -28)
        self.assertEqual(node["free_heap_kb"], 267.0)
        self.assertEqual(node["uptime_sec"], 3600)
        self.assertEqual(node["fps"], 72)
        self.assertTrue(node["online"])

    def test_opensky_utc_date_rollover(self):
        """Verify OpenSky daily requests counter resets based on 00:00 UTC."""
        tracker = TelemetryTracker(state=self.state)

        # Simulate yesterday's UTC date
        yesterday_utc = datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)
        tracker.daily_reset_date = yesterday_utc
        tracker.daily_requests_count = 3995

        # Trigger fetch_opensky with rate limit lock to avoid network call
        tracker.opensky_lockout_until = time.time() + 100
        tracker.fetch_opensky()

        # Counter should not increment when locked out
        self.assertEqual(tracker.daily_requests_count, 3995)

        # Remove lock, mock network request
        tracker.opensky_lockout_until = 0.0
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"states": []}
            mock_get.return_value = mock_resp

            tracker.fetch_opensky()

        # Daily reset date must now be today UTC and requests counter reset to 1
        today_utc = datetime.datetime.now(datetime.timezone.utc).date()
        self.assertEqual(tracker.daily_reset_date, today_utc)
        self.assertEqual(tracker.daily_requests_count, 1)

    def test_opensky_quota_backoff_threshold(self):
        """Test proactive poll backoff when approaching 85% of daily quota."""
        tracker = TelemetryTracker(state=self.state)
        tracker.quota_limit = 4000
        tracker.current_poll_interval = 30.0

        # Simulate 85% consumption: 3400 requests
        tracker.daily_requests_count = 3400

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"states": []}
            mock_get.return_value = mock_resp

            tracker.fetch_opensky()

        # Poll interval should automatically back off to at least 40.0s
        self.assertGreaterEqual(tracker.current_poll_interval, 40.0)


if __name__ == "__main__":
    unittest.main()

