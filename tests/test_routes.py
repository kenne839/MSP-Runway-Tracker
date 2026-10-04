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
        """Test direction-aware helper formatting and strict rejection of non-MSP routes."""
        self.assertEqual(format_route_string("DEN", "MSP", "LANDING"), "From DEN")
        self.assertEqual(format_route_string("MSP", "ORD", "TAKING OFF"), "To ORD")
        # Turnaround / paired leg inference:
        self.assertEqual(format_route_string("MSP", "LAS", "LANDING"), "From LAS")
        self.assertEqual(format_route_string("SAN", "MSP", "TAKING OFF"), "To SAN")
        # Strict MSP anchor rejection for non-MSP city pairs:
        self.assertIsNone(format_route_string("ATL", "ALB", "LANDING"), "Must reject ATL->ALB (DAL2225 bug)")
        self.assertIsNone(format_route_string("YYZ", "DTW", "LANDING"), "Must reject YYZ->DTW (UAL8172 bug)")
        self.assertIsNone(format_route_string("LAX", "SEA", "TAKING OFF"), "Must reject LAX->SEA")

    def test_live_adsbdb_lookup(self):
        """Test live query against adsbdb.com for flight not in memory cache."""
        # Query known commercial flight touching MSP (DAL1420 is MSP -> PHX)
        test_cs = "DAL1420"
        res = self.resolver.resolve_route(test_cs, "TAKING OFF")
        self.assertEqual(res, "To PHX")
        self.assertIn(test_cs, self.resolver.routes_db)

    def test_dal2225_resolution(self):
        """Verify DAL2225 resolves to MCO -> MSP (Orlando), avoiding false ATL."""
        res = self.resolver.resolve_route("DAL2225", "LANDING")
        self.assertEqual(res, "From MCO")

    def test_ual8172_resolution(self):
        """Verify UAL8172 resolves to IAH -> MSP (Houston), avoiding false YYZ."""
        res = self.resolver.resolve_route("UAL8172", "LANDING")
        self.assertEqual(res, "From IAH")

    def test_non_msp_flight_rejected(self):
        """Verify non-MSP flight (e.g. LAX->SEA) is strictly rejected and returns 'Unknown'."""
        res = self.resolver.resolve_route("DAL1045", "TAKING OFF")
        self.assertEqual(res, "Unknown")

    def test_negative_caching_and_invalid(self):
        """Verify invalid or unknown flights return 'Unknown' safely."""
        self.assertEqual(self.resolver.resolve_route("", "LANDING"), "Unknown")
        self.assertEqual(self.resolver.resolve_route("UNKNOWN", "TAKING OFF"), "Unknown")
        self.assertEqual(self.resolver.resolve_route("FAKE999999", "LANDING"), "Unknown")

    def test_script_get_flight_route(self):
        """Verify script.py route resolution matches."""
        r = get_flight_route("DAL900", "TAKING OFF")
        self.assertEqual(r, "To LGA")
        r_dal2225 = get_flight_route("DAL2225", "LANDING")
        self.assertEqual(r_dal2225, "From MCO")
        r_ual8172 = get_flight_route("UAL8172", "LANDING")
        self.assertEqual(r_ual8172, "From IAH")

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

    def test_flightaware_scraper_live(self):
        """Verify live FlightAware scraper resolves filed flight plan touching MSP."""
        fa_res = self.resolver._query_flightaware("DAL2225")
        if fa_res:
            orig, dest, airline = fa_res
            self.assertEqual(orig, "MCO")
            self.assertEqual(dest, "MSP")

    def test_sd_card_protection_unknown_hex_cache(self):
        """Verify unknown hex codes are cached in RAM only and do NOT dirty the 17MB database."""
        self.resolver._db_modified = False
        fake_hex = "deadbeef99"
        raw, readable = self.resolver.resolve_airframe(fake_hex)
        self.assertEqual(raw, "UNKNOWN")
        self.assertIn(fake_hex, self.resolver.unknown_hex_cache)
        self.assertNotIn(fake_hex, self.resolver.aircraft_db)
        self.assertFalse(self.resolver._db_modified, "Unknown hex must NEVER mark 17MB DB as modified!")


if __name__ == "__main__":
    unittest.main()
