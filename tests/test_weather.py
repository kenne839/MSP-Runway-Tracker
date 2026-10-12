"""
Unit tests for METAR surface weather ingestion, parsing, and fault tolerance.
"""

import unittest
from unittest.mock import patch, MagicMock

from rpi.weather import MetarService, WX_PHENOMENA_MAP, CLOUD_COVER_MAP


class TestWeatherService(unittest.TestCase):
    def setUp(self):
        with patch.object(MetarService, "_fetch_metar"):
            self.service = MetarService(poll_interval=300)

    def test_parse_standard_metar_observation(self):
        """Test full parsing of a realistic KMSP METAR observation with gusting wind."""
        sample_ob = {
            "temp": 15.0,        # 15C = 59F
            "wdir": 300,
            "wspd": 12,
            "wgst": 22,
            "visib": 10.0,
            "fltcat": "VFR",
            "cover": "SCT",
            "rawOb": "METAR KMSP 112353Z 30012G22KT 10SM SCT045 15/05 A3006 RMK AO2",
            "clouds": [{"cover": "SCT", "base": 4500}]
        }

        parsed = self.service._parse_observation(sample_ob)
        self.assertEqual(parsed.get("temp_c"), 15.0)
        self.assertEqual(parsed.get("temp_f"), 59)
        self.assertEqual(parsed.get("wind"), "300@12G22kt")
        self.assertEqual(parsed.get("pressure"), "30.06 inHg")
        self.assertEqual(parsed.get("flight_category"), "VFR")
        self.assertFalse(parsed.get("is_stale"))

    def test_parse_calm_wind(self):
        """Test calm wind parsing when wspd is 0 or wdir is None."""
        sample_ob = {
            "temp": 20.0,
            "wdir": None,
            "wspd": 0,
            "rawOb": "METAR KMSP 112353Z 00000KT 10SM CLR 20/10 A2992"
        }

        parsed = self.service._parse_observation(sample_ob)
        self.assertEqual(parsed.get("wind"), "Calm")

    def test_parse_weather_phenomena_and_clouds(self):
        """Test weather phenomena mapping (e.g. Thunderstorm, Snow, Rain)."""
        sample_ob = {
            "temp": -2.0,
            "wdir": 340,
            "wspd": 18,
            "wxString": "-SN",
            "cover": "OVC",
            "rawOb": "METAR KMSP 112353Z 34018KT 2SM -SN OVC012 M02/M06 A2980"
        }

        parsed = self.service._parse_observation(sample_ob)
        self.assertEqual(parsed.get("temp_f"), 28)
        self.assertIn("Light Snow", parsed.get("condition"))

    def test_fallback_pressure_calculation(self):
        """Test altimeter pressure fallback from hPa when rawOb string format differs."""
        sample_ob = {
            "temp": 10.0,
            "wdir": 180,
            "wspd": 5,
            "altim": 1013.25,  # Standard atmosphere ~ 29.92 inHg
            "rawOb": "KMSP 1013HPA"
        }

        parsed = self.service._parse_observation(sample_ob)
        self.assertIn("inHg", parsed.get("pressure"))
        self.assertTrue(parsed.get("pressure").startswith("29.92"))

    def test_network_failure_preserves_cached_weather(self):
        """Test that temporary network exceptions retain previously known good weather."""
        self.service._weather = {
            "flight_category": "VFR",
            "temp_f": 72,
            "wind": "250@08kt",
            "is_stale": False
        }

        with patch("urllib.request.urlopen", side_effect=Exception("HTTP 503 Service Unavailable")):
            self.service._fetch_metar()

        cached = self.service.get_weather()
        self.assertEqual(cached.get("temp_f"), 72)
        self.assertEqual(cached.get("wind"), "250@08kt")


if __name__ == "__main__":
    unittest.main()

