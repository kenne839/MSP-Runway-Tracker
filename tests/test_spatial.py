"""
Unit and regression tests for spatial runway matching, departure fan corridor,
and turn tolerance gating (DAL2089 regression test).
"""

import math
import os
import sys
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpi.spatial import get_runway_match, to_meters
from script import get_active_runway


class TestSpatialRunwayMatching(unittest.TestCase):
    def test_runway_touchdown_centerline(self):
        """Test exact centerline points for landing aircraft on 30R and 12L."""
        # Touchdown point on 30R (~44.882, -93.195, heading 301)
        match_30r = get_runway_match(44.882, -93.195, 301.0, vertical_rate=-3.0)
        self.assertIsNotNone(match_30r)
        self.assertEqual(match_30r["runway"], "30R")

        # Threshold point on 12L (~44.892, -93.220, heading 121)
        match_12l = get_runway_match(44.892, -93.220, 121.0, vertical_rate=-2.5)
        self.assertIsNotNone(match_12l)
        self.assertEqual(match_12l["runway"], "12L")

    def test_dal2089_immediate_departure_turn(self):
        """
        Regression Test for DAL2089 missed takeoff:
        Aircraft departs Runway 30L (301 deg) and immediately turns left towards West (268 deg).
        With a 30s OpenSky polling interval, points along the climb-out fan
        have cross-track drift up to 800m and heading deltas up to 32 deg.
        Verify that all sample points match Runway 30L!
        """
        # Actual ADS-B telemetry coordinates from DAL2089 departure:
        dal2089_points = [
            # lat, lon, heading, vertical_rate_ms, label
            (44.8882, -93.2352, 291.3, 3.0, "Pt 0: Liftoff at threshold"),
            (44.8911, -93.2457, 286.8, 5.5, "Pt 1: 10s post-liftoff, 165m cross-track"),
            (44.8930, -93.2546, 280.9, 7.2, "Pt 2: 18s post-liftoff, 351m cross-track"),
            (44.8940, -93.2619, 273.7, 8.5, "Pt 3: 24s post-liftoff, 557m cross-track"),
            (44.8943, -93.2685, 268.8, 10.0, "Pt 4: 30s post-liftoff, 800m cross-track, 32 deg turn"),
        ]

        for lat, lon, hdg, vr, label in dal2089_points:
            match = get_runway_match(lat, lon, hdg, vertical_rate=vr)
            self.assertIsNotNone(match, f"Failed to match DAL2089 at {label}")
            self.assertEqual(match["runway"], "30L", f"Expected 30L at {label}, got {match['runway']}")

            # Verify script.py get_active_runway matches as well
            script_match = get_active_runway(lat, lon, hdg, vertical_rate=vr)
            self.assertEqual(script_match, "30L", f"script.py get_active_runway failed at {label}")

    def test_descending_off_course_rejected(self):
        """
        Verify that a descending aircraft (landing) off-course is strictly rejected.
        Landing aircraft must adhere to strict 150m cross-track and cannot use departure fan.
        """
        # Pt 4 from DAL2089 (800m cross-track), but descending with vert_rate = -3.0 m/s
        match = get_runway_match(44.8943, -93.2685, 268.8, vertical_rate=-3.0)
        self.assertIsNone(match, "Descending aircraft with 800m cross-track must NOT match departure fan!")

    def test_heading_deviation_over_90_rejected(self):
        """
        Verify that aircraft heading opposite direction (> 90 deg delta) is rejected,
        even if climbing.
        """
        # Heading East (090 deg) while in 30L NW departure sector (301 deg) -> 149 deg delta
        match = get_runway_match(44.8943, -93.2685, 90.0, vertical_rate=8.0)
        self.assertIsNone(match, "Reverse heading must be rejected")

    def test_parallel_runway_disambiguation_in_departure_fan(self):
        """
        Verify that an aircraft climbing in the 30L fan selects 30L and not parallel 30R.
        30L should have significantly smaller cross-track distance (~800m vs ~1820m).
        """
        match = get_runway_match(44.8943, -93.2685, 268.8, vertical_rate=10.0)
        self.assertIsNotNone(match)
        self.assertEqual(match["runway"], "30L")
        self.assertLess(match["cross_track_m"], 900.0)


if __name__ == "__main__":
    unittest.main()
