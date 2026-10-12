"""
Unit tests for KMSP D-ATIS (Digital ATIS) advisory parsing and concurrency.
"""

import time
import unittest
from unittest.mock import patch, MagicMock

from rpi.atis import DatisService, KNOWN_RUNWAYS


class TestDatisService(unittest.TestCase):
    def setUp(self):
        # Instantiate with a long poll interval and prevent initial auto-fetch
        with patch.object(DatisService, "_fetch_datis"):
            self.service = DatisService(poll_interval=600)

    def test_parse_datis_parallel_flow(self):
        """Test parsing standard KMSP parallel operations (30R arrivals, 30L departures)."""
        sample_payload = [
            {
                "type": "arr",
                "code": "R",
                "datis": "MSP ATIS ARR R 0353Z. 30012KT 10SM CLR 18/06 A3006. SIMUL APCH IN USE. ILS OR VIS RWY 30R APCH IN USE. NOTICES TO AIRMEN: RWY 12L/30R CLSD."
            },
            {
                "type": "dep",
                "code": "S",
                "datis": "MSP ATIS DEP S 0353Z. 30012KT 10SM CLR 18/06 A3006. DEPARTING RWY 30L."
            }
        ]

        parsed = self.service._parse_datis_payload(sample_payload)
        self.assertTrue(parsed.get("valid"))
        self.assertEqual(parsed.get("code"), "R/S")
        self.assertEqual(parsed.get("arr_code"), "R")
        self.assertEqual(parsed.get("dep_code"), "S")
        self.assertIn("30R", parsed.get("landing_runways"))
        self.assertIn("30L", parsed.get("departure_runways"))
        self.assertEqual(parsed.get("primary_arr"), "30R")
        self.assertEqual(parsed.get("primary_dep"), "30L")
        self.assertEqual(parsed.get("summary"), "ARR 30R / DEP 30L")

    def test_parse_datis_runway_closures(self):
        """Test extraction of closed runways from ATIS advisory text."""
        sample_payload = [
            {
                "type": "arr",
                "code": "A",
                "datis": "MSP ATIS ARR A. 30010KT. ILS RWY 12R APCH IN USE. RWY 4/22 CLSD. RWY 35 CLOSED FOR MAINT."
            }
        ]

        parsed = self.service._parse_datis_payload(sample_payload)
        self.assertTrue(parsed.get("valid"))
        closed = parsed.get("closed_runways", [])
        self.assertIn("4", closed)
        self.assertIn("22", closed)
        self.assertIn("35", closed)

    def test_parse_datis_single_code_and_summary(self):
        """Test single code composite formatting when ARR and DEP share same letter."""
        sample_payload = [
            {
                "type": "arr",
                "code": "M",
                "datis": "MSP ATIS ARR M. VIS RWY 12R IN USE."
            },
            {
                "type": "dep",
                "code": "M",
                "datis": "MSP ATIS DEP M. DEPARTING RWY 12L."
            }
        ]

        parsed = self.service._parse_datis_payload(sample_payload)
        self.assertTrue(parsed.get("valid"))
        self.assertEqual(parsed.get("code"), "M")
        self.assertEqual(parsed.get("primary_arr"), "12R")
        self.assertEqual(parsed.get("primary_dep"), "12L")
        self.assertEqual(parsed.get("summary"), "ARR 12R / DEP 12L")

    def test_parse_datis_invalid_payload(self):
        """Test graceful rejection of empty or malformed ATIS payloads."""
        self.assertFalse(self.service._parse_datis_payload([]).get("valid"))
        self.assertFalse(self.service._parse_datis_payload([{"type": "arr", "datis": "NO RUNWAYS"}]).get("valid"))

    def test_concurrent_get_datis_thread_guard(self):
        """Verify that concurrent calls to get_datis() do NOT spawn duplicate worker threads."""
        self.service._last_poll = 0
        self.service.poll_interval = 10  # Elapsed

        spawned_threads = []

        def mock_thread_start(thread_self):
            spawned_threads.append(thread_self.name)

        with patch("threading.Thread.start", mock_thread_start):
            # Rapidly call get_datis 5 times concurrently
            results = [self.service.get_datis() for _ in range(5)]

        # Exactly ONE thread should have been spawned thanks to the mutex guard
        self.assertEqual(len(spawned_threads), 1)
        self.assertEqual(spawned_threads[0], "DatisFetchWorker")

    def test_datis_network_failure_graceful_degradation(self):
        """Ensure network errors do not raise exceptions and preserve existing state."""
        self.service._datis = {"valid": True, "code": "K", "summary": "ARR 30R / DEP 30L"}

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            self.service._fetch_datis()

        # Cache remains preserved and intact
        cached = self.service.get_datis()
        self.assertIsNotNone(cached)
        self.assertEqual(cached.get("code"), "K")


if __name__ == "__main__":
    unittest.main()

