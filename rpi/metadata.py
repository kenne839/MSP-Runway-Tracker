"""
Metadata resolution for airlines, airframes, and flight routes with thread safety,
SQLite-backed persistence, asynchronous background enrichment, and negative hit caching.
"""

import os
import json
import time
import re
import queue
import sqlite3
import threading
import logging
import requests
import certifi

from .config import (
    AIRLINES_FILE,
    AIRFRAMES_FILE,
    AIRCRAFT_DB_FILE,
    AIRCRAFT_SQLITE_FILE,
    ROUTES_CACHE_FILE,
    OPENSKY_ROUTES_URL,
    ADSDB_ROUTES_URL,
    ROUTE_CACHE_TTL_SEC
)
from .opensky_auth import OpenSkyAuth

logger = logging.getLogger(__name__)

# Default CA bundle for secure TLS verification
CA_BUNDLE = certifi.where()


class MetadataResolver:
    """Thread-safe, multi-tier resolver for airline brands, airframes, and flight routes."""

    CHARTER_CACHE_TTL_SEC = 21600  # 6 hours for ad-hoc charter/ferry operations
    _flightaware_cooldown_until: float = 0.0

    def __init__(self, auth: OpenSkyAuth = None):
        self._lock = threading.RLock()
        self.auth = auth or OpenSkyAuth()

        # Static JSON lookup tables
        self.airlines = self._load_json(AIRLINES_FILE, "airlines")
        self.airframes = self._load_json(AIRFRAMES_FILE, "airframes")
        self.routes_db = self._load_json(ROUTES_CACHE_FILE, "routes cache")

        # In-memory caches
        self.route_cache: dict[str, str] = {}
        self.unknown_hex_cache: set[str] = set()
        self.aircraft_db: dict[str, str] = {}  # In-memory fallback and fast O(1) cache
        self._sqlite_conn: sqlite3.Connection | None = None

        self._db_modified = False
        self._routes_db_modified = False
        self._last_db_save = time.time()

        # Initialize SQLite database and populate in-memory database
        self._init_database()

        # Asynchronous enrichment worker queue
        self._enrichment_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._inflight_keys: set = set()
        self._worker_thread = threading.Thread(
            target=self._enrichment_worker_loop,
            daemon=True,
            name="MetadataEnrichmentWorker"
        )
        self._worker_thread.start()

    def _load_json(self, path: str, label: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info("Loaded %s records from %s (%s).", f"{len(data):,}", label, os.path.basename(path))
                    return data
            except Exception as e:
                logger.warning("Failed to load %s (%s): %s", label, path, e)
        else:
            logger.debug("%s file not found at %s.", label, path)
        return {}

    # --------------------------------------------------------------------------
    # Database Initialization & SQLite Migration
    # --------------------------------------------------------------------------
    def _init_database(self):
        """Initializes SQLite database and migrates records from JSON if necessary."""
        try:
            db_dir = os.path.dirname(AIRCRAFT_SQLITE_FILE)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            self._sqlite_conn = sqlite3.connect(AIRCRAFT_SQLITE_FILE, check_same_thread=False)
            self._sqlite_conn.execute("PRAGMA journal_mode=WAL")
            self._sqlite_conn.execute("PRAGMA synchronous=NORMAL")
            self._sqlite_conn.execute(
                "CREATE TABLE IF NOT EXISTS aircraft (hex TEXT PRIMARY KEY, type_code TEXT NOT NULL)"
            )
            self._sqlite_conn.commit()

            cursor = self._sqlite_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM aircraft")
            count = cursor.fetchone()[0]

            if count == 0 and os.path.exists(AIRCRAFT_DB_FILE):
                # Auto-migrate records from JSON into SQLite
                logger.info("Migrating aircraft records from JSON (%s) to SQLite...", os.path.basename(AIRCRAFT_DB_FILE))
                json_data = self._load_json(AIRCRAFT_DB_FILE, "aircraft DB")
                if json_data:
                    self._sqlite_conn.executemany(
                        "INSERT OR IGNORE INTO aircraft (hex, type_code) VALUES (?, ?)",
                        json_data.items()
                    )
                    self._sqlite_conn.commit()
                    cursor.execute("SELECT COUNT(*) FROM aircraft")
                    count = cursor.fetchone()[0]
                    self.aircraft_db = json_data
                    logger.info("Successfully migrated %s records to SQLite (%s).", f"{count:,}", os.path.basename(AIRCRAFT_SQLITE_FILE))
            else:
                logger.info("SQLite aircraft database ready with %s records (%s).", f"{count:,}", os.path.basename(AIRCRAFT_SQLITE_FILE))
                # Pre-seed in-memory aircraft_db for backward compatibility
                if os.path.exists(AIRCRAFT_DB_FILE) and len(self.aircraft_db) == 0:
                    self.aircraft_db = self._load_json(AIRCRAFT_DB_FILE, "aircraft DB")

        except Exception as e:
            logger.error("SQLite initialization failed: %s. Falling back to JSON cache.", e)
            if not self.aircraft_db:
                self.aircraft_db = self._load_json(AIRCRAFT_DB_FILE, "aircraft DB")

    def _query_sqlite_airframe(self, hex_code: str) -> str | None:
        """Fast indexed query into SQLite database."""
        if not self._sqlite_conn:
            return None
        try:
            with self._lock:
                cursor = self._sqlite_conn.cursor()
                cursor.execute("SELECT type_code FROM aircraft WHERE hex = ?", (hex_code,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.debug("SQLite airframe query error for %s: %s", hex_code, e)
            return None

    def _save_sqlite_airframe(self, hex_code: str, type_code: str):
        """Persists a newly discovered airframe into SQLite."""
        if not self._sqlite_conn:
            return
        try:
            with self._lock:
                self._sqlite_conn.execute(
                    "INSERT OR REPLACE INTO aircraft (hex, type_code) VALUES (?, ?)",
                    (hex_code, type_code)
                )
                self._sqlite_conn.commit()
        except Exception as e:
            logger.debug("Failed saving %s to SQLite: %s", hex_code, e)

    # --------------------------------------------------------------------------
    # Airline Resolution
    # --------------------------------------------------------------------------
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

        with self._lock:
            if icao_code in self.airlines:
                airline_name = self.airlines[icao_code]
                for sfx in (" Airlines", " Air Lines", " Airways", " Aviation", " Express"):
                    if airline_name.endswith(sfx):
                        airline_name = airline_name[:-len(sfx)].strip()

                formatted = f"{airline_name} {flight_num}".strip() if flight_num else airline_name
                return airline_name, flight_num, formatted

        return callsign, flight_num, callsign

    # --------------------------------------------------------------------------
    # Airframe Resolution (Multi-Tier with Optional Async Enrichment)
    # --------------------------------------------------------------------------
    def resolve_airframe(self, icao24: str | None, callsign: str | None = None, async_lookup: bool = False) -> tuple[str, str]:
        """
        Resolves Mode-S transponder hex address to (type_code, readable_name).
        Multi-tier:
        1. In-memory dictionary / SQLite indexed query (0ms)
        2. Negative hit cache check (0ms)
        3. If async_lookup=True: enqueues background worker and returns UNKNOWN immediately.
        4. If async_lookup=False: runs synchronous fallback (HexDB -> ADS-B DB -> FlightAware).
        """
        if not icao24 and not callsign:
            return "UNKNOWN", "UNKNOWN"

        hex_code = icao24.lower().strip() if icao24 else None

        with self._lock:
            # 1. Check in-memory aircraft_db
            if hex_code and hex_code in self.aircraft_db:
                type_code = self.aircraft_db[hex_code]
                readable = self.airframes.get(type_code, type_code)
                return type_code, readable

            # 2. Query SQLite
            if hex_code:
                sqlite_type = self._query_sqlite_airframe(hex_code)
                if sqlite_type:
                    self.aircraft_db[hex_code] = sqlite_type
                    readable = self.airframes.get(sqlite_type, sqlite_type)
                    return sqlite_type, readable

            # 3. In-memory negative hit cache
            if hex_code and hex_code in self.unknown_hex_cache and not callsign:
                return "UNKNOWN", "UNKNOWN"

        # 4. Asynchronous Background Lookup
        if async_lookup:
            item_key = f"airframe:{hex_code}:{callsign}"
            with self._lock:
                if item_key not in self._inflight_keys:
                    self._inflight_keys.add(item_key)
                    if hex_code:
                        self.unknown_hex_cache.add(hex_code)
                    try:
                        self._enrichment_queue.put_nowait(("airframe", (hex_code, callsign, item_key)))
                    except queue.Full:
                        pass
            return "UNKNOWN", "UNKNOWN"

        # 5. Synchronous Fallback (used when async_lookup=False, e.g. in tests)
        resolved_type = self._perform_airframe_web_lookup(hex_code, callsign)
        with self._lock:
            if resolved_type != "UNKNOWN":
                if hex_code:
                    self.aircraft_db[hex_code] = resolved_type
                    self._save_sqlite_airframe(hex_code, resolved_type)
                    self._db_modified = True
                    self.unknown_hex_cache.discard(hex_code)
                    self._maybe_save_db()
                readable = self.airframes.get(resolved_type, resolved_type)
                return resolved_type, readable
            else:
                if hex_code:
                    self.unknown_hex_cache.add(hex_code)
                return "UNKNOWN", "UNKNOWN"

    def _perform_airframe_web_lookup(self, hex_code: str | None, callsign: str | None) -> str:
        """Executes external HTTP requests to resolve aircraft type."""
        type_code = "UNKNOWN"

        # 1. HexDB Fallback
        if hex_code:
            try:
                url = f"https://hexdb.io/api/v1/aircraft/{hex_code}"
                res = requests.get(url, timeout=1.5, verify=CA_BUNDLE)
                if res.status_code == 200:
                    data = res.json()
                    resolved = data.get("ICAOTypeCode") or data.get("Type")
                    if resolved and resolved.strip().upper() != "UNKNOWN":
                        return resolved.strip().upper()
            except Exception:
                pass

            # 2. ADS-B DB API Fallback
            try:
                url = f"https://api.adsbdb.com/v0/aircraft/{hex_code}"
                res = requests.get(url, timeout=1.5, verify=CA_BUNDLE)
                if res.status_code == 200:
                    data = res.json()
                    ac = data.get("response", {}).get("aircraft", {})
                    resolved = ac.get("icao_type") or ac.get("type")
                    if resolved and resolved.strip().upper() != "UNKNOWN":
                        return resolved.strip().upper()
            except Exception:
                pass

        # 3. Callsign Flight Plan Fallback
        if callsign:
            cs = callsign.strip().upper()
            with self._lock:
                cached_route = self.routes_db.get(cs)
                if isinstance(cached_route, dict) and cached_route.get("aircraft_type"):
                    resolved = cached_route["aircraft_type"]
                    if resolved and resolved.strip().upper() != "UNKNOWN":
                        return resolved.strip().upper()

            fa_data = self._query_flightaware(cs)
            if fa_data:
                *_, ac_fa = fa_data
                if ac_fa and ac_fa.strip().upper() != "UNKNOWN":
                    return ac_fa.strip().upper()

        return type_code

    # --------------------------------------------------------------------------
    # Route Resolution (Multi-Tier with Strict MSP Anchor Sanity Check)
    # --------------------------------------------------------------------------
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
    def _validate_and_format_route(cls, origin: str | None, dest: str | None, action: str, allow_turnaround: bool = False) -> str | None:
        """
        Strict MSP Anchor Sanity Check:
        A flight movement at MSP can ONLY have a route that touches MSP.
        If neither origin nor destination is MSP/KMSP, the route is rejected (returns None).
        """
        if not origin or not dest:
            return None

        orig_clean = str(origin).strip().upper()
        dest_clean = str(dest).strip().upper()

        is_orig_msp = cls._is_msp(orig_clean)
        is_dest_msp = cls._is_msp(dest_clean)

        if not is_orig_msp and not is_dest_msp:
            return None

        orig_disp = cls._clean_airport_code(orig_clean)
        dest_disp = cls._clean_airport_code(dest_clean)
        act = str(action).strip().upper() if action else ""

        if act == "LANDING":
            if is_dest_msp and not is_orig_msp:
                return f"From {orig_disp}"
            elif is_orig_msp and is_dest_msp:
                return f"From {orig_disp}"
            elif is_orig_msp and not is_dest_msp and allow_turnaround:
                return f"From {dest_disp}"
            return None

        elif act in ("TAKING OFF", "TAKEOFF"):
            if is_orig_msp and not is_dest_msp:
                return f"To {dest_disp}"
            elif is_orig_msp and is_dest_msp:
                return f"To {dest_disp}"
            elif is_dest_msp and not is_orig_msp and allow_turnaround:
                return f"To {orig_disp}"
            return None

        else:
            if is_orig_msp or is_dest_msp:
                return f"{orig_disp} -> {dest_disp}"
            return None

    @classmethod
    def _format_route_string(cls, origin: str, dest: str, action: str, allow_turnaround: bool = True) -> str | None:
        return cls._validate_and_format_route(origin, dest, action, allow_turnaround=allow_turnaround)

    @classmethod
    def _is_charter_or_special(cls, callsign: str) -> bool:
        if not callsign or len(callsign) < 3:
            return False
        c = callsign.strip().upper()
        m = re.search(r"(\d+)$", c)
        if m and int(m.group(1)) >= 8000:
            return True
        return False

    def _query_flightaware(self, callsign: str, action: str = None) -> tuple[str, str, str, str] | None:
        """Queries FlightAware live tracking page with 2.0s timeout and cooldown guard."""
        if not callsign or callsign == "UNKNOWN":
            return None

        now = time.time()
        if now < MetadataResolver._flightaware_cooldown_until:
            return None

        try:
            url = f"https://www.flightaware.com/live/flight/{callsign}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            }
            res = requests.get(url, headers=headers, timeout=2.0)
            if res.status_code in (429, 403):
                MetadataResolver._flightaware_cooldown_until = now + 600.0
                logger.warning("FlightAware HTTP %s detected. Cooldown activated for 10 min.", res.status_code)
                return None
            if res.status_code != 200:
                return None

            match = re.search(r"trackpollBootstrap\s*=\s*(\{.+?\});", res.text)
            if not match:
                return None

            data = json.loads(match.group(1))
            flights = data.get("flights", {})
            if not flights:
                return None

            act = str(action).strip().upper() if action else ""
            candidate_legs = []

            for fid, fval in flights.items():
                orig = fval.get("origin", {}).get("iata") or fval.get("origin", {}).get("icao")
                dest = fval.get("destination", {}).get("iata") or fval.get("destination", {}).get("icao")
                airline = fval.get("airline", {}).get("fullName") or ""
                aircraft_type = fval.get("aircraft", {}).get("type") or fval.get("aircraftType") or ""

                if orig and dest:
                    orig_str = str(orig).upper()
                    dest_str = str(dest).upper()
                    is_orig_msp = self._is_msp(orig_str)
                    is_dest_msp = self._is_msp(dest_str)
                    if is_orig_msp or is_dest_msp:
                        candidate_legs.append((orig_str, dest_str, airline, aircraft_type, is_orig_msp, is_dest_msp))

            if not candidate_legs:
                return None

            if act in ("TAKEOFF", "TAKING OFF"):
                for orig_s, dest_s, airl, ac_t, is_o, is_d in candidate_legs:
                    if is_o:
                        return orig_s, dest_s, airl, ac_t
            elif act == "LANDING":
                for orig_s, dest_s, airl, ac_t, is_o, is_d in candidate_legs:
                    if is_d:
                        return orig_s, dest_s, airl, ac_t

            orig_s, dest_s, airl, ac_t, _, _ = candidate_legs[0]
            return orig_s, dest_s, airl, ac_t

        except Exception as e:
            logger.debug("FlightAware lookup failed for %s: %s", callsign, e)
            return None

    def resolve_route(self, callsign: str, action: str, async_lookup: bool = False) -> str:
        """
        Multi-tier route resolver with session caching and MSP anchor sanity check.
        """
        if not callsign or callsign == "UNKNOWN":
            return "Unknown"

        callsign = callsign.strip().upper()
        cache_key = f"{callsign}:{action}"

        with self._lock:
            if cache_key in self.route_cache:
                return self.route_cache[cache_key]

        now = time.time()
        is_charter = self._is_charter_or_special(callsign)

        with self._lock:
            cached_entry = self.routes_db.get(callsign)

        # 1. Charter Flights: check fresh charter cache (< 6 hours)
        if is_charter and cached_entry and isinstance(cached_entry, dict):
            orig = cached_entry.get("origin") or ""
            dest = cached_entry.get("destination") or ""
            age = now - cached_entry.get("updated", 0)
            if age < self.CHARTER_CACHE_TTL_SEC:
                formatted = self._validate_and_format_route(orig, dest, action)
                if formatted is not None:
                    with self._lock:
                        self.route_cache[cache_key] = formatted
                    return formatted

        # 2. Regular Flights: check persistent routes database
        if not is_charter and cached_entry and isinstance(cached_entry, dict):
            orig = cached_entry.get("origin") or ""
            dest = cached_entry.get("destination") or ""
            age = now - cached_entry.get("updated", 0)
            is_fa = (cached_entry.get("source") == "flightaware")
            formatted = self._validate_and_format_route(orig, dest, action, allow_turnaround=False)
            if formatted is not None and (is_fa or age < ROUTE_CACHE_TTL_SEC):
                with self._lock:
                    self.route_cache[cache_key] = formatted
                return formatted

        # 3. Asynchronous Lookup: enqueue to background worker
        if async_lookup:
            item_key = f"route:{callsign}:{action}"
            with self._lock:
                if item_key not in self._inflight_keys:
                    self._inflight_keys.add(item_key)
                    try:
                        self._enrichment_queue.put_nowait(("route", (callsign, action, item_key)))
                    except queue.Full:
                        pass
                # Check if we can use a stale MSP route while waiting
                if cached_entry and isinstance(cached_entry, dict):
                    orig = cached_entry.get("origin") or ""
                    dest = cached_entry.get("destination") or ""
                    fallback = self._validate_and_format_route(orig, dest, action, allow_turnaround=True)
                    if fallback:
                        return fallback
            return "Unknown"

        # 4. Synchronous Lookup
        formatted = self._perform_route_web_lookup(callsign, action, is_charter, cached_entry)
        with self._lock:
            self.route_cache[cache_key] = formatted
        return formatted

    def _perform_route_web_lookup(self, callsign: str, action: str, is_charter: bool, cached_entry: dict | None) -> str:
        """Executes external network requests to resolve flight route."""
        now = time.time()

        # 1. FlightAware Live Filed Plan
        fa_data = self._query_flightaware(callsign, action)
        if fa_data:
            orig_fa, dest_fa, airline_fa, *ac_extra = fa_data
            ac_type_fa = ac_extra[0] if ac_extra else ""
            formatted = self._validate_and_format_route(orig_fa, dest_fa, action, allow_turnaround=False)
            if formatted is not None:
                with self._lock:
                    self.routes_db[callsign] = {
                        "origin": self._clean_airport_code(orig_fa),
                        "destination": self._clean_airport_code(dest_fa),
                        "airline": airline_fa,
                        "aircraft_type": ac_type_fa,
                        "updated": int(now),
                        "source": "flightaware",
                        "is_charter": is_charter
                    }
                    self._routes_db_modified = True
                    self._maybe_save_db()
                return formatted

        # 2. Check fresh cached entry fallback
        if cached_entry and isinstance(cached_entry, dict):
            orig = cached_entry.get("origin") or ""
            dest = cached_entry.get("destination") or ""
            age = now - cached_entry.get("updated", 0)
            formatted = self._validate_and_format_route(orig, dest, action, allow_turnaround=False)
            if formatted is not None and age < ROUTE_CACHE_TTL_SEC:
                return formatted

        # 3. ADS-B DB API Fallback
        try:
            url = f"{ADSDB_ROUTES_URL}/{callsign}"
            res = requests.get(url, headers={"User-Agent": "MSP-Runway-Tracker/1.0"}, timeout=1.5, verify=CA_BUNDLE)
            if res.status_code == 200:
                data = res.json()
                fr = data.get("response", {}).get("flightroute", {})
                if fr and fr.get("origin") and fr.get("destination"):
                    orig_iata = fr["origin"].get("iata_code") or fr["origin"].get("icao_code")
                    dest_iata = fr["destination"].get("iata_code") or fr["destination"].get("icao_code")
                    if orig_iata and dest_iata:
                        formatted = self._validate_and_format_route(orig_iata, dest_iata, action, allow_turnaround=False)
                        if formatted is not None:
                            with self._lock:
                                self.routes_db[callsign] = {
                                    "origin": self._clean_airport_code(orig_iata),
                                    "destination": self._clean_airport_code(dest_iata),
                                    "airline": fr.get("airline", {}).get("name", ""),
                                    "updated": int(now),
                                    "source": "adsbdb"
                                }
                                self._routes_db_modified = True
                                self._maybe_save_db()
                            return formatted
        except Exception:
            pass

        # 4. OpenSky Routes Fallback
        try:
            url = f"{OPENSKY_ROUTES_URL}?callsign={callsign}"
            res = requests.get(
                url,
                headers=self.auth.get_headers(),
                auth=self.auth.get_basic_auth(),
                timeout=2.0,
                verify=CA_BUNDLE
            )
            if res.status_code == 200:
                data = res.json()
                route = data.get("route", [])
                if len(route) >= 2:
                    origin, dest = route[0], route[1]
                    formatted = self._validate_and_format_route(origin, dest, action, allow_turnaround=False)
                    if formatted is not None:
                        with self._lock:
                            self.routes_db[callsign] = {
                                "origin": self._clean_airport_code(origin),
                                "destination": self._clean_airport_code(dest),
                                "updated": int(now)
                            }
                            self._routes_db_modified = True
                            self._maybe_save_db()
                        return formatted
        except Exception:
            pass

        # 5. Stale Fallback
        if cached_entry and isinstance(cached_entry, dict):
            orig = cached_entry.get("origin") or ""
            dest = cached_entry.get("destination") or ""
            formatted = self._validate_and_format_route(orig, dest, action, allow_turnaround=True)
            if formatted is not None:
                with self._lock:
                    cached_entry["updated"] = int(now - ROUTE_CACHE_TTL_SEC + 86400)
                return formatted

        return "Unknown"

    # --------------------------------------------------------------------------
    # Background Enrichment Worker Loop
    # --------------------------------------------------------------------------
    def _enrichment_worker_loop(self):
        """Processes queued metadata resolution requests asynchronously."""
        while True:
            try:
                item = self._enrichment_queue.get(timeout=2.0)
            except queue.Empty:
                continue

            try:
                item_type, payload = item
                if item_type == "airframe":
                    hex_code, callsign, key = payload
                    self._resolve_airframe_async(hex_code, callsign)
                elif item_type == "route":
                    callsign, action, key = payload
                    self._resolve_route_async(callsign, action)
            except Exception as e:
                logger.debug("Background metadata enrichment exception: %s", e)
            finally:
                with self._lock:
                    if len(payload) >= 3:
                        self._inflight_keys.discard(payload[2])
                self._enrichment_queue.task_done()

    def _resolve_airframe_async(self, hex_code: str | None, callsign: str | None):
        resolved_type = self._perform_airframe_web_lookup(hex_code, callsign)
        with self._lock:
            if resolved_type != "UNKNOWN" and hex_code:
                self.aircraft_db[hex_code] = resolved_type
                self._save_sqlite_airframe(hex_code, resolved_type)
                self._db_modified = True
                self.unknown_hex_cache.discard(hex_code)
                self._maybe_save_db()
                logger.info("Async airframe resolved: [%s] -> %s (%s)", hex_code, resolved_type, callsign or "")
            elif hex_code:
                self.unknown_hex_cache.add(hex_code)

    def _resolve_route_async(self, callsign: str, action: str):
        is_charter = self._is_charter_or_special(callsign)
        with self._lock:
            cached_entry = self.routes_db.get(callsign)
        formatted = self._perform_route_web_lookup(callsign, action, is_charter, cached_entry)
        if formatted != "Unknown":
            cache_key = f"{callsign}:{action}"
            with self._lock:
                self.route_cache[cache_key] = formatted
            logger.info("Async route resolved: %s [%s] -> %s", callsign, action, formatted)

    # --------------------------------------------------------------------------
    # Persistence Management
    # --------------------------------------------------------------------------
    def _maybe_save_db(self, force: bool = False):
        """Batches writes to persistent JSON caches to avoid excessive MicroSD I/O."""
        now = time.time()
        should_save = force or (now - self._last_db_save > 300)

        with self._lock:
            if self._db_modified and should_save:
                try:
                    with open(AIRCRAFT_DB_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.aircraft_db, f)
                    self._db_modified = False
                    self._last_db_save = now
                except Exception as e:
                    logger.warning("Failed to save aircraft DB JSON cache: %s", e)

            if self._routes_db_modified and should_save:
                try:
                    with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.routes_db, f, indent=2)
                    self._routes_db_modified = False
                    self._last_db_save = now
                except Exception as e:
                    logger.warning("Failed to save routes DB cache: %s", e)

    def flush_caches(self):
        """Forces immediate disk flush of any modified caches upon clean shutdown."""
        self._maybe_save_db(force=True)


# ==============================================================================
# Public Module-Level Convenience Functions & Singletons
# ==============================================================================

def format_route_string(origin: str | None, dest: str | None, action: str, allow_turnaround: bool = True) -> str | None:
    """Validates and formats route string touching KMSP."""
    return MetadataResolver._validate_and_format_route(origin, dest, action, allow_turnaround=allow_turnaround)


_default_resolver: MetadataResolver | None = None
_default_lock = threading.Lock()


def get_default_resolver() -> MetadataResolver:
    global _default_resolver
    with _default_lock:
        if _default_resolver is None:
            _default_resolver = MetadataResolver()
        return _default_resolver


def get_flight_route(callsign: str, action: str) -> str:
    """Convenience helper resolving flight route via the default resolver."""
    resolver = get_default_resolver()
    return resolver.resolve_route(callsign, action)
