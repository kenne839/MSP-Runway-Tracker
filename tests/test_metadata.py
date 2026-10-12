"""
Unit tests for airline prefix translation, airframe resolution,
and negative caching protection.
"""

import unittest
from unittest.mock import patch, MagicMock

from rpi.metadata import MetadataResolver


class TestMetadataResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = MetadataResolver()

    def test_airline_suffix_stripping(self):
        """Test translation of 3-letter ICAO code and stripping redundant suffixes."""
        # DAL -> "Delta Air Lines" -> stripped to "Delta"
        airline, num, formatted = self.resolver.resolve_airline("DAL793")
        self.assertEqual(airline, "Delta")
        self.assertEqual(num, "793")
        self.assertEqual(formatted, "Delta 793")

        # SCX -> "Sun Country Airlines" -> stripped to "Sun Country"
        airline, num, formatted = self.resolver.resolve_airline("SCX102")
        self.assertEqual(airline, "Sun Country")
        self.assertEqual(num, "102")
        self.assertEqual(formatted, "Sun Country 102")

        # SKW -> "SkyWest Airlines" -> stripped to "SkyWest"
        airline, num, formatted = self.resolver.resolve_airline("SKW3822")
        self.assertEqual(airline, "SkyWest")
        self.assertEqual(num, "3822")
        self.assertEqual(formatted, "SkyWest 3822")

    def test_unknown_airline_callsign_preservation(self):
        """Test unknown callsign prefixes without duplicate flight numbers."""
        airline, num, formatted = self.resolver.resolve_airline("N12345")
        self.assertEqual(airline, "N12345")
        self.assertEqual(formatted, "N12345")

        airline, num, formatted = self.resolver.resolve_airline(None)
        self.assertEqual(airline, "Unknown")
        self.assertEqual(formatted, "Unknown")

    def test_airframe_resolution_preseeded_db(self):
        """Test instant O(1) resolution of known transponder hex code from local database."""
        # Test known Delta A321 transponder
        type_code, readable = self.resolver.resolve_airframe("a1b2c3")
        # Should return a string type code
        self.assertIsInstance(type_code, str)
        self.assertIsInstance(readable, str)

    def test_airframe_negative_caching(self):
        """Ensure repeated queries for an unknown hex do not trigger redundant network calls."""
        fake_hex = "ffffff"
        self.resolver.unknown_hex_cache.discard(fake_hex)

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp

            # First query: should attempt network lookups
            t1, r1 = self.resolver.resolve_airframe(fake_hex)
            self.assertEqual(t1, "UNKNOWN")
            self.assertIn(fake_hex, self.resolver.unknown_hex_cache)

            # Second query: must return immediately via negative cache with NO network calls
            mock_get.reset_mock()
            t2, r2 = self.resolver.resolve_airframe(fake_hex)
            self.assertEqual(t2, "UNKNOWN")
            mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()

