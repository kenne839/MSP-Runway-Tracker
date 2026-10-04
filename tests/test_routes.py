"""
Unit and integration tests for multi-tier flight route resolution and caching.
"""

import os
import sys
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpi.metadata import MetadataResolver
from script import format_route_string, get_flight_route


class TestRouteResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = MetadataResolver()

    def test_routes_cache_loaded(self):
        """Verify pre-seeded persistent routes database is loaded on initialization."""
        self.assertGreater(len(self.resolver.routes_db), 100, "Should have loaded pre-seeded routes")

    def test_cache_hits_arrivals_and_departures(self):
        """Test direction-aware route formatting from cache for MSP movements."""
        # DAL900 is MSP -> LGA
        dep_route = self.resolver.resolve_route("DAL900", "TAKING OFF")
        self.assertEqual(dep_route, "To LGA")

        # SCX102 is LAS -> MSP
        arr_route = self.resolver.resolve_route("SCX102", "LANDING")
        self.assertEqual(arr_route, "From LAS")

        # SKW3842 is MSP -> RAP
        skw_route = self.resolver.resolve_route("SKW3842", "TAKING OFF")
        self.assertEqual(skw_route, "To RAP")

    def test_format_route_string_helper(self):
        """Test direction-aware helper formatting."""
        self.assertEqual(format_route_string("DEN", "MSP", "LANDING"), "From DEN")
        self.assertEqual(format_route_string("MSP", "ORD", "TAKING OFF"), "To ORD")
        # Turnaround / paired leg inference:
        self.assertEqual(format_route_string("MSP", "LAS", "LANDING"), "From LAS")
        self.assertEqual(format_route_string("SAN", "MSP", "TAKING OFF"), "To SAN")

    def test_live_adsbdb_lookup(self):
        """Test live query against adsbdb.com for flight not in memory cache."""
        # Query known commercial flight
        test_cs = "DAL1045" # LAX -> SEA
        res = self.resolver.resolve_route(test_cs, "TAKING OFF")
        self.assertTrue(res.startswith("To ") or res.startswith("From "), f"Got: {res}")
        self.assertIn(test_cs, self.resolver.routes_db)

    def test_negative_caching_and_invalid(self):
        """Verify invalid or unknown flights return 'Unknown' safely."""
        self.assertEqual(self.resolver.resolve_route("", "LANDING"), "Unknown")
        self.assertEqual(self.resolver.resolve_route("UNKNOWN", "TAKING OFF"), "Unknown")
        self.assertEqual(self.resolver.resolve_route("FAKE999999", "LANDING"), "Unknown")

    def test_script_get_flight_route(self):
        """Verify script.py route resolution matches."""
        r = get_flight_route("DAL900", "TAKING OFF")
        self.assertEqual(r, "To LGA")

    def test_ttl_stale_revalidation(self):
        """Verify entries older than 14 days are revalidated and updated."""
        import time
        # Simulate a 30-day old flight
        stale_cs = "DAL900"
        self.resolver.routes_db[stale_cs]["updated"] = int(time.time() - 30 * 86400)
        # Clear session formatted cache
        self.resolver.route_cache.pop(f"{stale_cs}:TAKING OFF", None)

        res = self.resolver.resolve_route(stale_cs, "TAKING OFF")
        self.assertEqual(res, "To LGA")
        # Timestamp should now be freshly updated (< 10 seconds old)
        new_age = time.time() - self.resolver.routes_db[stale_cs]["updated"]
        self.assertLess(new_age, 10, "Timestamp should have refreshed upon revalidation")

    def test_ttl_stale_fallback_when_offline(self):
        """Verify stale entry is preserved if network lookup fails (no downgrade to 'Unknown')."""
        fake_cs = "STALE999"
        self.resolver.routes_db[fake_cs] = {
            "origin": "MSP",
            "destination": "FAR",
            "airline": "Test Airlines",
            "updated": 0  # Ancient timestamp
        }
        self.resolver.route_cache.pop(f"{fake_cs}:TAKING OFF", None)

        # Network will 404 on STALE999, but stale fallback should return "To FAR"
        res = self.resolver.resolve_route(fake_cs, "TAKING OFF")
        self.assertEqual(res, "To FAR", "Should gracefully retain stale route if network fails")


if __name__ == "__main__":
    unittest.main()
