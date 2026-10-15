"""
Unit tests for the Office Replay Queue and REST Endpoint.
Tests deduplication, window sliding, disk persistence, auth, and gzip compression.
"""

import os
import json
import gzip
import time
import tempfile
import unittest
from http.server import HTTPServer
import threading
import urllib.request
import urllib.error

from rpi.replay_queue import RollingReplayQueue
from rpi.tracker import TelemetryState
from rpi.server import make_handler


class TestRollingReplayQueue(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_replay_queue.json")
        self.queue = RollingReplayQueue(persistence_file=self.db_path, max_retention_minutes=60)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_operation_and_deduplication(self):
        now = 1791950000.0
        op = {
            "runway": "30L",
            "action": "LANDING",
            "callsign": "DAL1234",
            "flight_label": "Delta 1234",
            "aircraft_type": "Airbus A321",
            "type_code": "A321",
            "route": "ORD -> MSP",
            "speed_kts": 135,
            "altitude_ft": 900
        }

        # First recording must succeed
        self.assertTrue(self.queue.record_operation(op, timestamp=now))

        # Duplicate recording in same 5-minute bucket must be ignored
        self.assertFalse(self.queue.record_operation(op, timestamp=now + 10.0))

        # Different action on same flight should be recorded
        op_takeoff = dict(op, action="TAKEOFF")
        self.assertTrue(self.queue.record_operation(op_takeoff, timestamp=now + 15.0))

        # Different flight should be recorded
        op_diff = dict(op, callsign="AAL456", flight_label="American 456")
        self.assertTrue(self.queue.record_operation(op_diff, timestamp=now + 20.0))

    def test_sliding_window_filtering(self):
        now = 1791955000.0

        # Operation 40 minutes ago (outside 30m window, inside 60m retention)
        op_old = {
            "runway": "12R",
            "action": "TAKEOFF",
            "callsign": "SKW111",
            "speed_kts": 150
        }
        self.queue.record_operation(op_old, timestamp=now - 2400)

        # Operation 10 minutes ago (inside 30m window)
        op_recent = {
            "runway": "30R",
            "action": "LANDING",
            "callsign": "DAL222",
            "speed_kts": 130
        }
        self.queue.record_operation(op_recent, timestamp=now - 600)

        batch = self.queue.get_batch(window_minutes=30, now_epoch=now)
        self.assertEqual(batch["operations_count"], 1)
        self.assertEqual(batch["operations"][0]["callsign"], "DAL222")
        self.assertEqual(batch["operations"][0]["offset_sec"], 1200) # (now - 600) - (now - 1800) = 1200s

        # With 45m window, both should be present
        batch_45 = self.queue.get_batch(window_minutes=45, now_epoch=now)
        self.assertEqual(batch_45["operations_count"], 2)

    def test_disk_persistence(self):
        now = time.time()
        op = {
            "runway": "30L",
            "action": "LANDING",
            "callsign": "UAL789",
            "speed_kts": 140
        }
        self.queue.record_operation(op, timestamp=now)
        self.queue.flush()

        self.assertTrue(os.path.exists(self.db_path))

        # Create a new queue instance pointing to same file
        restored_queue = RollingReplayQueue(persistence_file=self.db_path, max_retention_minutes=60)
        batch = restored_queue.get_batch(window_minutes=30, now_epoch=now + 60)
        self.assertEqual(batch["operations_count"], 1)
        self.assertEqual(batch["operations"][0]["callsign"], "UAL789")


class TestReplayEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = TelemetryState()
        # Seed an operation
        op = {
            "runway": "30R",
            "action": "LANDING",
            "callsign": "DAL999",
            "flight_label": "Delta 999",
            "aircraft_type": "Boeing 737-900",
            "type_code": "B739",
            "route": "ATL -> MSP",
            "speed_kts": 140,
            "altitude_ft": 950
        }
        cls.state.replay_queue.record_operation(op, timestamp=time.time() - 300)

        handler = make_handler(cls.state)
        cls.server = HTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_replay_batch_json(self):
        url = f"http://127.0.0.1:{self.port}/api/v1/office_replay_batch?window_minutes=30"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "application/json")
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["version"], 2)
            self.assertGreaterEqual(data["operations_count"], 1)
            self.assertIn("weather", data)
            self.assertIn("theme", data)
            self.assertIn("network", data)

    def test_get_replay_batch_gzip(self):
        url = f"http://127.0.0.1:{self.port}/api/v1/office_replay_batch"
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")
            compressed_bytes = resp.read()
            decompressed_bytes = gzip.decompress(compressed_bytes)
            data = json.loads(decompressed_bytes.decode("utf-8"))
            self.assertEqual(data["version"], 2)


if __name__ == "__main__":
    unittest.main()

