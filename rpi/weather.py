"""
MSP Runway Tracker - METAR Weather Ingestion Module
====================================================
Fetches and decodes real-time aviation surface weather (METAR) for KMSP
from the NOAA / FAA Aviation Weather API (aviationweather.gov).

Update Frequency:
- Routine METAR observations are generated hourly (~:53Z) with SPECI reports
  issued immediately upon sudden weather shifts (wind gusts, ceiling drop, storm onset).
- Polling every 300 seconds (5 minutes) provides optimal timeliness with minimal
  bandwidth and zero load on the API.
"""

import time
import json
import re
import threading
import urllib.request
import urllib.error

METAR_URL = "https://aviationweather.gov/api/data/metar?ids=KMSP&format=json"
METAR_POLL_INTERVAL = 300  # 5 minutes

WX_PHENOMENA_MAP = {
    "-RA": "Light Rain",
    "RA": "Rain",
    "+RA": "Heavy Rain",
    "-SN": "Light Snow",
    "SN": "Snow",
    "+SN": "Heavy Snow",
    "TSRA": "Thunderstorm",
    "-TSRA": "Light T-Storm",
    "+TSRA": "Heavy T-Storm",
    "FZRA": "Freezing Rain",
    "-FZRA": "Lt Frz Rain",
    "DZ": "Drizzle",
    "-DZ": "Light Drizzle",
    "PL": "Ice Pellets",
    "GS": "Small Hail",
    "GR": "Hail",
    "BR": "Mist",
    "FG": "Fog",
    "FZFG": "Freezing Fog",
    "HZ": "Haze",
    "FU": "Smoke",
    "SQ": "Squall"
}

CLOUD_COVER_MAP = {
    "CLR": "Clear",
    "SKC": "Clear",
    "FEW": "Few Clouds",
    "SCT": "Partly Cloudy",
    "BKN": "Broken",
    "OVC": "Overcast",
    "VV": "Vertical Obscuration"
}


class MetarService:
    """Thread-safe background service maintaining current KMSP surface weather."""
    def __init__(self, poll_interval: int = METAR_POLL_INTERVAL):
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._last_poll = 0
        self._weather = {
            "flight_category": "VFR",
            "temp_f": 59,
            "temp_c": 15,
            "wind": "270@11kt",
            "pressure": "30.06 inHg",
            "condition": "Clear",
            "raw": "METAR KMSP (Pending initialization)",
            "updated_at": 0,
            "is_stale": True
        }
        # Run initial fetch immediately
        self._fetch_metar()

    def get_weather(self) -> dict:
        """Returns the latest parsed METAR observation, refreshing if interval elapsed."""
        now = time.time()
        if now - self._last_poll >= self.poll_interval:
            self._fetch_metar()
        with self._lock:
            return dict(self._weather)

    def _fetch_metar(self):
        self._last_poll = time.time()
        req = urllib.request.Request(
            METAR_URL,
            headers={"User-Agent": "KMSP-Runway-Tracker/1.0 (Aviation Weather Hub)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data or not isinstance(data, list):
                    return
                ob = data[0]
                parsed = self._parse_observation(ob)
                if parsed:
                    with self._lock:
                        self._weather = parsed
        except Exception as e:
            # Maintain previous weather cache if network momentarily fails
            print(f"[{time.strftime('%X')}] METAR fetch notice: {e}")

    def _parse_observation(self, ob: dict) -> dict:
        temp_c = ob.get("temp")
        temp_f = int(round(temp_c * 9 / 5 + 32)) if temp_c is not None else 60
        wdir = ob.get("wdir")
        wspd = ob.get("wspd")
        wgst = ob.get("wgst")

        # Wind formatting: e.g. "270@11kt" or "270@11G18kt" or "Calm"
        if wdir is None or wspd is None or wspd == 0:
            wind_str = "Calm"
        elif wgst:
            wind_str = f"{wdir:03d}@{wspd}G{wgst}kt"
        else:
            wind_str = f"{wdir:03d}@{wspd}kt"

        # Altimeter pressure parsing
        raw = ob.get("rawOb", "")
        m_alt = re.search(r"\bA(\d{2})(\d{2})\b", raw)
        if m_alt:
            alt_str = f"{m_alt.group(1)}.{m_alt.group(2)} inHg"
        elif ob.get("altim"):
            # Convert hPa to inHg if raw string missing
            in_hg = ob.get("altim") * 0.02953
            alt_str = f"{in_hg:.2f} inHg"
        else:
            alt_str = "30.00 inHg"

        # Precipitation / Phenomena & Cloud Cover
        wx_raw = ob.get("wxString") or ""
        condition = None
        if wx_raw:
            condition = WX_PHENOMENA_MAP.get(wx_raw.strip(), wx_raw)
        
        if not condition:
            cover = ob.get("cover") or "CLR"
            condition = CLOUD_COVER_MAP.get(cover, cover)

        flt_cat = ob.get("fltCat") or "VFR"

        return {
            "flight_category": flt_cat,
            "temp_f": temp_f,
            "temp_c": temp_c if temp_c is not None else 15,
            "wind": wind_str,
            "pressure": alt_str,
            "condition": condition,
            "raw": raw,
            "updated_at": int(time.time()),
            "is_stale": False
        }


# Global singleton instance for easy import across modules
_service_instance = None

def get_current_weather() -> dict:
    global _service_instance
    if _service_instance is None:
        _service_instance = MetarService()
    return _service_instance.get_weather()

