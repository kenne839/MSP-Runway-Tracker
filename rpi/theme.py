"""
LED Theme Manager for MSP Runway Tracker.
Handles theme configuration, color presets, brightness clamping,
persistence in led_theme.json, and in-band injection for the ESP32.
"""

import os
import json
import threading
import logging

logger = logging.getLogger(__name__)

try:
    from .solar import get_solar_elevation, get_solar_phase, compute_circadian_brightness
except (ImportError, ValueError):
    from solar import get_solar_elevation, get_solar_phase, compute_circadian_brightness

THEME_PRESETS = {
    "classic": {
        "name": "Classic Emerald & Amber",
        "brightness": 24,
        "landing_head": "0xFFFFFF",
        "landing_tail": "0x00FF88",
        "takeoff_head": "0xFFE0A0",
        "takeoff_tail": "0xFF4500",
        "idle_threshold": "0x002200",
        "conflict_warn": "0xFF0000",
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "brightness": 28,
        "landing_head": "0xFFFFFF",
        "landing_tail": "0x00E5FF",
        "takeoff_head": "0xFFE0A0",
        "takeoff_tail": "0xFF007F",
        "idle_threshold": "0x1A0033",
        "conflict_warn": "0xFF0055",
    },
    "sapphire": {
        "name": "Midnight Sapphire",
        "brightness": 24,
        "landing_head": "0xE0F0FF",
        "landing_tail": "0x0077FF",
        "takeoff_head": "0xFFFFFF",
        "takeoff_tail": "0x00FFCC",
        "idle_threshold": "0x001133",
        "conflict_warn": "0xFF2200",
    },
    "sunset": {
        "name": "Sunset Afterburner",
        "brightness": 26,
        "landing_head": "0xFFF0D0",
        "landing_tail": "0xFFB300",
        "takeoff_head": "0xFFFFFF",
        "takeoff_tail": "0xFF1E00",
        "idle_threshold": "0x331100",
        "conflict_warn": "0xFF0000",
    },
    "night": {
        "name": "Night Vision Red",
        "brightness": 12,
        "landing_head": "0xFF6666",
        "landing_tail": "0xFF2222",
        "takeoff_head": "0xFF8844",
        "takeoff_tail": "0xFF4400",
        "idle_threshold": "0x220000",
        "conflict_warn": "0xAA0000",
    }
}

DEFAULT_THEME = dict(THEME_PRESETS["classic"])
DEFAULT_THEME["version"] = 1
DEFAULT_THEME["solar_sync"] = False
DEFAULT_THEME["night_floor"] = 8
DEFAULT_THEME["day_ceiling"] = 40

HARDWARE_MIN_SAFE_BRIGHTNESS = 5
HARDWARE_MAX_SAFE_BRIGHTNESS = 48


def sanitize_hex_color(val, default="0xFFFFFF") -> str:
    """Converts #RRGGBB, 0xRRGGBB, or int to standardized 0xRRGGBB string."""
    if val is None:
        return default
    if isinstance(val, int):
        return f"0x{val & 0xFFFFFF:06X}"
    s = str(val).strip()
    if s.startswith("#"):
        s = s[1:]
    elif s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    try:
        num = int(s, 16) & 0xFFFFFF
        return f"0x{num:06X}"
    except (ValueError, TypeError):
        return default


class ThemeManager:
    """Thread-safe manager for dynamic LED theme customization, solar circadian sync, and persistence."""
    def __init__(self, theme_file_path=None):
        self._lock = threading.Lock()
        if theme_file_path:
            self._file_path = theme_file_path
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self._file_path = os.path.join(base_dir, "led_theme.json")
        self._theme = self._load_theme()

    def _load_theme(self) -> dict:
        if os.path.isfile(self._file_path) and os.path.getsize(self._file_path) > 0:
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    theme = dict(DEFAULT_THEME)
                    theme.update(data)
                    theme["version"] = int(theme.get("version", 1))
                    theme["brightness"] = max(
                        HARDWARE_MIN_SAFE_BRIGHTNESS,
                        min(HARDWARE_MAX_SAFE_BRIGHTNESS, int(theme.get("brightness", 24)))
                    )
                    theme["solar_sync"] = bool(theme.get("solar_sync", False))
                    theme["night_floor"] = max(5, min(20, int(theme.get("night_floor", 8))))
                    theme["day_ceiling"] = max(
                        theme["night_floor"],
                        min(HARDWARE_MAX_SAFE_BRIGHTNESS, int(theme.get("day_ceiling", 40)))
                    )
                    for k in ("landing_head", "landing_tail", "takeoff_head", "takeoff_tail", "idle_threshold", "conflict_warn"):
                        if k in theme:
                            theme[k] = sanitize_hex_color(theme[k], DEFAULT_THEME.get(k))
                    return theme
            except Exception as e:
                logger.warning("Failed to load theme (%s): %s", self._file_path, e)
        return dict(DEFAULT_THEME)

    def _save_theme_unlocked(self):
        try:
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(self._theme, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save theme (%s): %s", self._file_path, e)

    def get_theme(self) -> dict:
        with self._lock:
            res = dict(self._theme)
        
        elev = get_solar_elevation()
        res["solar_elevation"] = elev
        res["solar_phase"] = get_solar_phase(elev)
        night_floor = res.get("night_floor", 8)
        day_ceiling = res.get("day_ceiling", 40)
        if res.get("solar_sync", False):
            res["effective_brightness"] = compute_circadian_brightness(elev, night_floor, day_ceiling)
        else:
            res["effective_brightness"] = res.get("brightness", 24)
        return res

    def update_theme(self, updates: dict) -> dict:
        with self._lock:
            old_ver = self._theme.get("version", 1)
            # If user selected a preset directly
            if "preset" in updates and updates["preset"] in THEME_PRESETS:
                p = THEME_PRESETS[updates["preset"]]
                for k, v in p.items():
                    if k != "name":
                        self._theme[k] = v
                self._theme["preset_name"] = p.get("name")

            if "brightness" in updates:
                try:
                    b = int(updates["brightness"])
                    self._theme["brightness"] = max(
                        HARDWARE_MIN_SAFE_BRIGHTNESS,
                        min(HARDWARE_MAX_SAFE_BRIGHTNESS, b)
                    )
                except (ValueError, TypeError):
                    pass

            if "solar_sync" in updates:
                self._theme["solar_sync"] = bool(updates["solar_sync"])

            if "night_floor" in updates:
                try:
                    nf = int(updates["night_floor"])
                    self._theme["night_floor"] = max(5, min(20, nf))
                except (ValueError, TypeError):
                    pass

            if "day_ceiling" in updates:
                try:
                    dc = int(updates["day_ceiling"])
                    self._theme["day_ceiling"] = max(
                        self._theme.get("night_floor", 8),
                        min(HARDWARE_MAX_SAFE_BRIGHTNESS, dc)
                    )
                except (ValueError, TypeError):
                    pass

            for k in ("landing_head", "landing_tail", "takeoff_head", "takeoff_tail", "idle_threshold", "conflict_warn"):
                if k in updates:
                    self._theme[k] = sanitize_hex_color(updates[k], self._theme.get(k))

            # Monotonic version bump
            self._theme["version"] = old_ver + 1
            self._save_theme_unlocked()
        return self.get_theme()
