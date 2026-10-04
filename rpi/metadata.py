"""
Metadata resolution for airlines, airframes, and flight routes with caching
and negative hit rate-limit protection.
"""

import os
import json
import time
import re
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
    def _is_msp(airport: str | None) -> bool:
        if not airport:
            return False
        return str(airport).strip().upper() in ("MSP", "KMSP")

    @staticmethod
    def _clean_airport_code(code: str | None) -> str:
        if not code:
            return ""
        c = str(code).strip().upper()
        if len(c) == 4 and c.startswith("K"):
            return c[1:]
        if len(c) == 4 and c.startswith("C") and c[1:] in ("YYZ", "YVR", "YYC", "YUL", "YEG", "YOW", "YWG", "YHZ"):
            return c[1:]
        return c

    @classmethod
    def _validate_and_format_route(cls, origin: str | None, dest: str | None, action: str) -> str | None:
        """
        Strict MSP Anchor Sanity Check:
        A flight movement at MSP can ONLY have a route that touches MSP.
        If neither origin nor destination is MSP/KMSP, the route is rejected (returns None).
        Handles MSP hub logic (arrivals vs departures) and turnaround leg inference.
        """
        if not origin or not dest:
            return None

        orig_clean = str(origin).strip().upper()
        dest_clean = str(dest).strip().upper()

        is_orig_msp = cls._is_msp(orig_clean)
        is_dest_msp = cls._is_msp(dest_clean)

        # REJECT any itinerary that does not touch Minneapolis-St. Paul!
        if not is_orig_msp and not is_dest_msp:
            return None

        orig_disp = cls._clean_airport_code(orig_clean)
        dest_disp = cls._clean_airport_code(dest_clean)
        act = str(action).strip().upper() if action else ""

        if act == "LANDING":
            if is_dest_msp and not is_orig_msp:
                return f"From {orig_disp}"
            elif is_orig_msp and not is_dest_msp:
                # Turnaround / paired leg in DB (e.g. flight scheduled MSP->XYZ returning)
                return f"From {dest_disp}"
            elif is_orig_msp and is_dest_msp:
                return f"From {orig_disp}"
            return None

        elif act in ("TAKING OFF", "TAKEOFF"):
            if is_orig_msp and not is_dest_msp:
                return f"To {dest_disp}"
            elif is_dest_msp and not is_orig_msp:
                # Turnaround / paired leg in DB (e.g. scheduled XYZ->MSP outbound)
                return f"To {orig_disp}"
            elif is_orig_msp and is_dest_msp:
                return f"To {dest_disp}"
            return None

        else:
            if is_orig_msp or is_dest_msp:
                return f"{orig_disp} -> {dest_disp}"
            return None

    @classmethod
    def _format_route_string(cls, origin: str, dest: str, action: str) -> str | None:
        """Backwards compatibility alias for _validate_and_format_route."""
        return cls._validate_and_format_route(origin, dest, action)

    def _query_flightaware(self, callsign: str) -> tuple[str, str, str] | None:
        """
        Queries FlightAware live tracking page for real-time filed flight plan.
        Extracts trackpollBootstrap JSON and looks for active legs touching MSP.
        Returns: (origin, destination, airline_name) or None
        """
        if not callsign or callsign == "UNKNOWN":
            return None

        try:
            url = f"https://www.flightaware.com/live/flight/{callsign}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            }
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code != 200:
                return None

            match = re.search(r"trackpollBootstrap\s*=\s*(\{.+?\});", res.text)
            if not match:
                return None

            data = json.loads(match.group(1))
            flights = data.get("flights", {})
            if not flights:
                return None

            for fid, fval in flights.items():
                orig = fval.get("origin", {}).get("iata") or fval.get("origin", {}).get("icao")
                dest = fval.get("destination", {}).get("iata") or fval.get("destination", {}).get("icao")
                airline = fval.get("airline", {}).get("fullName") or ""

                if orig and dest:
                    if self._is_msp(orig) or self._is_msp(dest):
                        return str(orig).upper(), str(dest).upper(), airline

            return None
        except Exception:
            return None

    def resolve_route(self, callsign: str, action: str) -> str:
        """
        Multi-tier route resolver with Strict MSP Anchor Sanity Check:
        1. In-memory session cache (0ms)
        2. Persistent on-disk routes DB (msp_routes_cache.json) (0ms if fresh < 14 days and valid MSP anchor)
        3. ADS-B DB API (adsbdb.com) (~150ms; only accepted if it touches MSP)
        4. FlightAware live real-time flight plan scraper (~400ms; parses filed FAA/ADS-B flight plan)
        5. OpenSky Network API (/api/routes) fallback (~250ms; only accepted if it touches MSP)
        6. Stale Fallback: If network lookup fails, retain existing cached itinerary IF it touches MSP.
        7. Negative caching to prevent repeated network spam for unresolvable callsigns.
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

                formatted = self._validate_and_format_route(origin, dest, action)
                if formatted is None:
                    # Bogus or non-MSP entry - purge it from persistent cache
                    self.routes_db.pop(callsign, None)
                    self._routes_db_modified = True
                    cached_entry = None
                elif age < ROUTE_CACHE_TTL_SEC:
                    self.route_cache[cache_key] = formatted
                    return formatted

            elif isinstance(cached_entry, str) and cached_entry and cached_entry != "Unknown":
                if " -> " in cached_entry:
                    parts = cached_entry.split(" -> ")
                    formatted = self._validate_and_format_route(parts[0], parts[1], action)
                    if formatted is not None:
                        self.route_cache[cache_key] = formatted
                        return formatted
                    else:
                        self.routes_db.pop(callsign, None)
                        self._routes_db_modified = True
                        cached_entry = None

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
                        formatted = self._validate_and_format_route(orig_iata, dest_iata, action)
                        if formatted is not None:
                            self.routes_db[callsign] = {
                                "origin": self._clean_airport_code(orig_iata),
                                "destination": self._clean_airport_code(dest_iata),
                                "airline": fr.get("airline", {}).get("name", ""),
                                "updated": int(now)
                            }
                            self._routes_db_modified = True
                            self._maybe_save_db()
                            self.route_cache[cache_key] = formatted
                            return formatted
        except Exception:
            pass

        # 3. FlightAware live flight plan fallback scraper (e.g. DAL2225 MCO->MSP)
        fa_data = self._query_flightaware(callsign)
        if fa_data:
            orig_fa, dest_fa, airline_fa = fa_data
            formatted = self._validate_and_format_route(orig_fa, dest_fa, action)
            if formatted is not None:
                self.routes_db[callsign] = {
                    "origin": self._clean_airport_code(orig_fa),
                    "destination": self._clean_airport_code(dest_fa),
                    "airline": airline_fa,
                    "updated": int(now)
                }
                self._routes_db_modified = True
                self._maybe_save_db()
                self.route_cache[cache_key] = formatted
                return formatted

        # 4. Fallback: Query OpenSky routes endpoint
        try:
            url = f"{OPENSKY_ROUTES_URL}?callsign={callsign}"
            res = requests.get(url, timeout=3, verify=False)
            if res.status_code == 200:
                data = res.json()
                route = data.get("route", [])
                if len(route) >= 2:
                    origin, dest = route[0], route[1]
                    formatted = self._validate_and_format_route(origin, dest, action)
                    if formatted is not None:
                        self.routes_db[callsign] = {
                            "origin": self._clean_airport_code(origin),
                            "destination": self._clean_airport_code(dest),
                            "updated": int(now)
                        }
                        self._routes_db_modified = True
                        self._maybe_save_db()
                        self.route_cache[cache_key] = formatted
                        return formatted
        except Exception:
            pass

        # 5. Graceful Stale Fallback: If network query failed but we have a valid stale MSP entry, keep using it!
        if cached_entry and isinstance(cached_entry, dict):
            origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
            dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
            formatted = self._validate_and_format_route(origin, dest, action)
            if formatted is not None:
                # Extend the update timestamp slightly (1 day) so we don't spam the network during an outage
                cached_entry["updated"] = int(now - ROUTE_CACHE_TTL_SEC + 86400)
                self.route_cache[cache_key] = formatted
                return formatted

        # 6. Negative caching in memory only (never persist "Unknown" or non-MSP routes to disk)
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
