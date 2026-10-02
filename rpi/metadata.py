"""
Metadata resolution for airlines, airframes, and flight routes with caching
and negative hit rate-limit protection.
"""

import os
import json
import time
import requests
from .config import (
    AIRLINES_FILE,
    AIRFRAMES_FILE,
    AIRCRAFT_DB_FILE,
    OPENSKY_ROUTES_URL
)

class MetadataResolver:
    def __init__(self):
        self.airlines = self._load_json(AIRLINES_FILE, "airlines")
        self.airframes = self._load_json(AIRFRAMES_FILE, "airframes")
        self.aircraft_db = self._load_json(AIRCRAFT_DB_FILE, "aircraft DB")
        self.route_cache: dict[str, str] = {}
        self._db_modified = False
        self._last_db_save = time.time()

    def _load_json(self, path: str, label: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    print(f"Loaded {len(data):,} records from {label} ({os.path.basename(path)}).")
                    return data
            except Exception as e:
                print(f"Warning: Failed to load {label} ({path}): {e}")
        else:
            print(f"Notice: {label} file not found at {path}.")
        return {}

    def resolve_airline(self, raw_callsign: str) -> tuple[str, str, str]:
        """
        Translates a 3-letter ICAO callsign into airline name, flight number, and formatted label.
        Returns: (airline_name, flight_number, formatted_str)
        """
        if not raw_callsign or raw_callsign == "UNKNOWN":
            return "Unknown", "", "Unknown"

        callsign = raw_callsign.strip().upper()
        icao_code = callsign[:3]
        flight_num = callsign[3:].strip()

        if icao_code in self.airlines:
            airline_name = self.airlines[icao_code]
            formatted = f"{airline_name} {flight_num}".strip()
            return airline_name, flight_num, formatted

        return callsign, flight_num, callsign

    def resolve_airframe(self, icao24: str | None) -> tuple[str, str]:
        """
        Resolves the 24-bit Mode-S transponder hex address to:
        (raw_type_code, readable_airframe_name).
        """
        if not icao24:
            return "UNKNOWN", "UNKNOWN"

        hex_code = icao24.lower().strip()

        # 1. Local O(1) Cache
        if hex_code in self.aircraft_db:
            type_code = self.aircraft_db[hex_code]
            readable = self.airframes.get(type_code, type_code)
            return type_code, readable

        # 2. Dynamic HexDB Fallback
        type_code = "UNKNOWN"
        try:
            url = f"https://hexdb.io/api/v1/aircraft/{hex_code}"
            res = requests.get(url, timeout=3, verify=False)
            if res.status_code == 200:
                data = res.json()
                type_code = data.get("ICAOTypeCode") or data.get("Type") or "UNKNOWN"
        except Exception:
            pass

        # Negative caching prevents spamming external API on subsequent loops
        self.aircraft_db[hex_code] = type_code
        self._db_modified = True

        readable = self.airframes.get(type_code, type_code)
        self._maybe_save_db()
        return type_code, readable

    def resolve_route(self, callsign: str, action: str) -> str:
        """
        Fetches route origin/destination using OpenSky routes endpoint.
        Uses in-memory cache to avoid repeated HTTP calls.
        """
        if not callsign or callsign == "UNKNOWN":
            return "Unknown"

        callsign = callsign.strip().upper()
        if callsign in self.route_cache:
            return self.route_cache[callsign]

        route_str = "Unknown"
        try:
            url = f"{OPENSKY_ROUTES_URL}?callsign={callsign}"
            res = requests.get(url, timeout=3, verify=False)
            if res.status_code == 200:
                data = res.json()
                route = data.get("route", [])
                if len(route) >= 2:
                    origin, dest = route[0], route[1]
                    if action == "LANDING":
                        route_str = f"From {origin}"
                    elif action == "TAKING OFF":
                        route_str = f"To {dest}"
                    else:
                        route_str = f"{origin} -> {dest}"
        except Exception:
            pass

        self.route_cache[callsign] = route_str
        return route_str

    def _maybe_save_db(self):
        """Batches writes to persistent DB to avoid high I/O overhead on SD cards."""
        now = time.time()
        # Save at most once every 5 minutes if modified
        if self._db_modified and (now - self._last_db_save > 300):
            try:
                with open(AIRCRAFT_DB_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.aircraft_db, f)
                self._db_modified = False
                self._last_db_save = now
            except Exception as e:
                print(f"Warning: Failed to save aircraft DB cache: {e}")
