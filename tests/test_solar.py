"""
Unit test suite for KMSP Solar Calculation and Circadian Brightness Transfer Function.
"""

import unittest
import datetime
from rpi.solar import (
    get_solar_elevation,
    get_solar_phase,
    compute_circadian_brightness,
    CIVIL_TWILIGHT_ELEVATION_DEG,
    FULL_DAYLIGHT_ELEVATION_DEG
)


class TestSolarCalculations(unittest.TestCase):
    def test_summer_solstice_noon(self):
        # Minneapolis Summer Solstice ~June 21 at 18:00 UTC (1:00 PM CDT)
        summer_noon = datetime.datetime(2026, 6, 21, 18, 0, 0, tzinfo=datetime.timezone.utc)
        elev = get_solar_elevation(summer_noon)
        # Should be peak elevation > 65 degrees
        self.assertGreater(elev, 65.0)
        self.assertLess(elev, 70.0)

        # Circadian brightness should be clamped to day ceiling
        b = compute_circadian_brightness(elev, night_floor=8, day_ceiling=32)
        self.assertEqual(b, 32)
        self.assertEqual(get_solar_phase(elev), "Peak Daylight")

    def test_winter_solstice_midnight(self):
        # Minneapolis Winter Solstice ~Dec 21 at 06:00 UTC (12:00 AM CST)
        winter_midnight = datetime.datetime(2026, 12, 21, 6, 0, 0, tzinfo=datetime.timezone.utc)
        elev = get_solar_elevation(winter_midnight)
        # Should be deep night < -60 degrees
        self.assertLess(elev, -60.0)

        # Circadian brightness should be clamped to night floor
        b = compute_circadian_brightness(elev, night_floor=8, day_ceiling=32)
        self.assertEqual(b, 8)
        self.assertEqual(get_solar_phase(elev), "Night")

    def test_smoothstep_monotonicity(self):
        # As sun rises from -10 to +30, brightness must be non-decreasing
        elevations = [-15.0, -8.0, -6.0, -3.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 35.0]
        brightness_values = [
            compute_circadian_brightness(e, night_floor=8, day_ceiling=32)
            for e in elevations
        ]
        for i in range(len(brightness_values) - 1):
            self.assertLessEqual(
                brightness_values[i],
                brightness_values[i + 1],
                f"Non-monotonic jump from elev {elevations[i]} to {elevations[i+1]}"
            )
        self.assertEqual(brightness_values[0], 8)
        self.assertEqual(brightness_values[-1], 32)

    def test_boundary_limits(self):
        # Below civil twilight
        b_low = compute_circadian_brightness(CIVIL_TWILIGHT_ELEVATION_DEG - 5.0, night_floor=8, day_ceiling=40)
        self.assertEqual(b_low, 8)

        # Above full daylight
        b_high = compute_circadian_brightness(FULL_DAYLIGHT_ELEVATION_DEG + 10.0, night_floor=8, day_ceiling=40)
        self.assertEqual(b_high, 40)


class TestThemeSolarIntegration(unittest.TestCase):
    def test_theme_solar_toggle(self):
        import tempfile
        import os
        from rpi.theme import ThemeManager

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            tm = ThemeManager(theme_file_path=temp_path)
            t1 = tm.get_theme()
            self.assertFalse(t1["solar_sync"])
            self.assertEqual(t1["effective_brightness"], t1["brightness"])
            self.assertIn("solar_elevation", t1)
            self.assertIn("solar_phase", t1)

            # Enable solar sync
            t2 = tm.update_theme({"solar_sync": True})
            self.assertTrue(t2["solar_sync"])
            # Effective brightness is circadian calculated, independent of manual brightness
            elev = t2["solar_elevation"]
            expected = compute_circadian_brightness(elev, t2["night_floor"], t2["day_ceiling"])
            self.assertEqual(t2["effective_brightness"], expected)
            self.assertGreater(t2["version"], t1["version"])

            # Disable solar sync
            t3 = tm.update_theme({"solar_sync": False, "brightness": 18})
            self.assertFalse(t3["solar_sync"])
            self.assertEqual(t3["brightness"], 18)
            self.assertEqual(t3["effective_brightness"], 18)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
