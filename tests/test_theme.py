"""
Unit tests for the Dynamic LED Theme Architecture.
Verifies preset loading, brightness clamping, hex color sanitization,
monotonic version increments, and in-band snapshot integration.
"""

import unittest
import tempfile
import os
import json
from rpi.theme import (
    ThemeManager,
    THEME_PRESETS,
    DEFAULT_THEME,
    sanitize_hex_color,
    HARDWARE_MIN_SAFE_BRIGHTNESS,
    HARDWARE_MAX_SAFE_BRIGHTNESS
)
from rpi.tracker import TelemetryState


class TestThemeManager(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.temp_file.close()
        self.mgr = ThemeManager(theme_file_path=self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_default_theme_loaded(self):
        theme = self.mgr.get_theme()
        self.assertEqual(theme["version"], 1)
        self.assertEqual(theme["brightness"], 24)
        self.assertEqual(theme["landing_tail"], "0x00FF88")
        self.assertEqual(theme["takeoff_tail"], "0xFF4500")

    def test_brightness_clamping(self):
        # Test ceiling clamp (48)
        updated = self.mgr.update_theme({"brightness": 100})
        self.assertEqual(updated["brightness"], HARDWARE_MAX_SAFE_BRIGHTNESS)
        self.assertEqual(updated["version"], 2)

        # Test floor clamp (5)
        updated2 = self.mgr.update_theme({"brightness": 1})
        self.assertEqual(updated2["brightness"], HARDWARE_MIN_SAFE_BRIGHTNESS)
        self.assertEqual(updated2["version"], 3)

    def test_hex_sanitization(self):
        self.assertEqual(sanitize_hex_color("#00e5ff"), "0x00E5FF")
        self.assertEqual(sanitize_hex_color("0x00E5FF"), "0x00E5FF")
        self.assertEqual(sanitize_hex_color("0XFF007F"), "0xFF007F")
        self.assertEqual(sanitize_hex_color(0x00FF88), "0x00FF88")
        self.assertEqual(sanitize_hex_color("invalid", "0xFFFFFF"), "0xFFFFFF")

    def test_apply_preset(self):
        updated = self.mgr.update_theme({"preset": "cyberpunk"})
        self.assertEqual(updated["landing_tail"], "0x00E5FF")
        self.assertEqual(updated["takeoff_tail"], "0xFF007F")
        self.assertEqual(updated["brightness"], 28)
        self.assertEqual(updated["version"], 2)

    def test_persistence_reload(self):
        self.mgr.update_theme({"preset": "sunset", "brightness": 30})
        # Create second manager pointing to same file
        mgr2 = ThemeManager(theme_file_path=self.temp_file.name)
        theme = mgr2.get_theme()
        self.assertEqual(theme["landing_tail"], "0xFFB300")
        self.assertEqual(theme["takeoff_tail"], "0xFF1E00")
        self.assertEqual(theme["brightness"], 30)
        self.assertEqual(theme["version"], 2)

    def test_in_band_telemetry_snapshot(self):
        state = TelemetryState()
        # Override theme manager to use our temp file
        state.theme_manager = self.mgr
        state.update_theme({"preset": "sapphire"})
        snapshot = state.get_snapshot()

        self.assertIn("theme", snapshot)
        self.assertEqual(snapshot["theme"]["landing_tail"], "0x0077FF")
        self.assertEqual(snapshot["theme"]["takeoff_tail"], "0x00FFCC")
        self.assertEqual(snapshot["theme"]["version"], 2)


if __name__ == "__main__":
    unittest.main()

