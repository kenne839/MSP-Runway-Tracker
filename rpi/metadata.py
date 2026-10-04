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
    ROUTES_CACHE_FILE,
    OPENSKY_ROUTES_URL,
    ADSDB_ROUTES_URL,
    ROUTE_CACHE_TTL_SEC
)

class MetadataResolver:
    def __init__(self):
        self.airlines = self._load_json(AIRLINES_FILE, "airlines")
        self.airframes = self._load_json(AIRFRAMES_FILE, "airframes")
        self.aircraft_db = self._load_json(AIRCRAFT_DB_FILE, "aircraft DB")
        self.routes_db = self._load_json(ROUTES_CACHE_FILE, "routes cache")
        self.route_cache: dict[str, str] = {}
        self._db_modified = False
        self._routes_db_modified = False
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

    @staticmethod
    def _format_route_string(origin: str, dest: str, action: str) -> str:
        """
        Formats origin and destination into a direction-aware string for display.
        Handles MSP hub logic (arrivals vs departures) and prefers 3-letter IATA codes.
        """
        orig_clean = str(origin).strip().upper()
        dest_clean = str(dest).strip().upper()

        is_orig_msp = orig_clean in ("MSP", "KMSP")
        is_dest_msp = dest_clean in ("MSP", "KMSP")

        if action == "LANDING":
            if is_dest_msp:
                return f"From {orig_clean}"
            elif is_orig_msp:
                # Database might list outbound leg for flight number; infer city pair
                return f"From {dest_clean}"
            else:
                return f"From {orig_clean}"
        elif action == "TAKING OFF":
            if is_orig_msp:
                return f"To {dest_clean}"
            elif is_dest_msp:
                # Database might list inbound leg for flight number; infer city pair
                return f"To {orig_clean}"
            else:
                return f"To {dest_clean}"
        else:
            return f"{orig_clean} -> {dest_clean}"

    def resolve_route(self, callsign: str, action: str) -> str:
        """
        Multi-tier route resolver with Stale-While-Revalidate TTL (14-day expiry):
        1. In-memory session cache (0ms)
        2. Persistent on-disk routes DB (msp_routes_cache.json) (0ms if fresh < 14 days)
        3. ADS-B DB API (adsbdb.com) (~150ms for uncached or stale records)
        4. OpenSky Network API (/api/routes) fallback (~250ms)
        5. Stale Fallback: If network lookup fails, retain existing cached itinerary!
        6. In-memory negative caching to prevent network spam for truly unknown callsigns
        """
        if not callsign or callsign == "UNKNOWN":
            return "Unknown"

        callsign = callsign.strip().upper()
        cache_key = f"{callsign}:{action}"
        if cache_key in self.route_cache:
            return self.route_cache[cache_key]

        now = time.time()
        cached_entry = self.routes_db.get(callsign)

        # 1. Check persistent on-disk routes database
        if cached_entry:
            if isinstance(cached_entry, dict):
                origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
                dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
                last_updated = cached_entry.get("updated", 0)
                age = now - last_updated

                if origin and dest and age < ROUTE_CACHE_TTL_SEC:
                    formatted = self._format_route_string(origin, dest, action)
                    self.route_cache[cache_key] = formatted
                    return formatted
            elif isinstance(cached_entry, str) and cached_entry and cached_entry != "Unknown":
                if " -> " in cached_entry:
                    parts = cached_entry.split(" -> ")
                    formatted = self._format_route_string(parts[0], parts[1], action)
                    self.route_cache[cache_key] = formatted
                    return formatted
                self.route_cache[cache_key] = cached_entry
                return cached_entry

        # 2. Query adsbdb.com API (uncached or stale revalidation)
        try:
            url = f"{ADSDB_ROUTES_URL}/{callsign}"
            res = requests.get(url, headers={"User-Agent": "MSP-Runway-Tracker/1.0"}, timeout=3)
            if res.status_code == 200:
                data = res.json()
                fr = data.get("response", {}).get("flightroute", {})
                if fr and fr.get("origin") and fr.get("destination"):
                    orig_iata = fr["origin"].get("iata_code") or fr["origin"].get("icao_code")
                    dest_iata = fr["destination"].get("iata_code") or fr["destination"].get("icao_code")
                    if orig_iata and dest_iata:
                        self.routes_db[callsign] = {
                            "origin": orig_iata,
                            "destination": dest_iata,
                            "airline": fr.get("airline", {}).get("name", ""),
                            "updated": int(now)
                        }
                        self._routes_db_modified = True
                        self._maybe_save_db()
                        formatted = self._format_route_string(orig_iata, dest_iata, action)
                        self.route_cache[cache_key] = formatted
                        return formatted
        except Exception:
            pass

        # 3. Fallback: Query OpenSky routes endpoint
        try:
            url = f"{OPENSKY_ROUTES_URL}?callsign={callsign}"
            res = requests.get(url, timeout=3, verify=False)
            if res.status_code == 200:
                data = res.json()
                route = data.get("route", [])
                if len(route) >= 2:
                    origin, dest = route[0], route[1]
                    self.routes_db[callsign] = {
                        "origin": origin,
                        "destination": dest,
                        "updated": int(now)
                    }
                    self._routes_db_modified = True
                    self._maybe_save_db()
                    formatted = self._format_route_string(origin, dest, action)
                    self.route_cache[cache_key] = formatted
                    return formatted
        except Exception:
            pass

        # 4. Graceful Stale Fallback: If network query failed but we have a stale entry, keep using it!
        if cached_entry and isinstance(cached_entry, dict):
            origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
            dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
            if origin and dest:
                # Extend the update timestamp slightly (1 day) so we don't spam the network during an outage
                cached_entry["updated"] = int(now - ROUTE_CACHE_TTL_SEC + 86400)
                formatted = self._format_route_string(origin, dest, action)
                self.route_cache[cache_key] = formatted
                return formatted

        # 5. Negative caching in memory only (do not persist "Unknown" to disk)
        self.route_cache[cache_key] = "Unknown"
        return "Unknown"

    def _maybe_save_db(self, force: bool = False):
        """Batches writes to persistent DB to avoid high I/O overhead on SD cards."""
        now = time.time()
        should_save = force or (now - self._last_db_save > 300)

        if self._db_modified and should_save:
            try:
                with open(AIRCRAFT_DB_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.aircraft_db, f)
                self._db_modified = False
                self._last_db_save = now
            except Exception as e:
                print(f"Warning: Failed to save aircraft DB cache: {e}")

        if self._routes_db_modified and should_save:
            try:
                with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.routes_db, f, indent=2)
                self._routes_db_modified = False
                self._last_db_save = now
            except Exception as e:
                print(f"Warning: Failed to save routes DB cache: {e}")
