"""
Core telemetry daemon that polls ADS-B state vectors, executes kinematic & spatial
corridor gating, tracks runway operational roles (Arrivals / Departures),
ingests real-time KMSP METAR surface weather, and produces a synchronized JSON payload.
"""

import time
import json
import threading
import datetime
import requests
import urllib3
import copy
from .config import (
    MSP_BBOX,
    MIN_VELOCITY_MS,
    MAX_ALTITUDE_M,
    TAKEOFF_MIN_VERT_RATE,
    LANDING_MAX_VERT_RATE,
    OPENSKY_URL,
    OPENSKY_POLL_INTERVAL,
    OPENSKY_USERNAME,
    OPENSKY_PASSWORD,
    DUMP1090_URL,
    DUMP1090_POLL_INTERVAL,
    DATA_SOURCE,
    STATE_FILE,
    RUNWAYS,
    REPLAY_QUEUE_FILE
)
from .spatial import get_runway_match
from .metadata import MetadataResolver
from .weather import get_current_weather
from .atis import get_current_datis
from .opensky_auth import OpenSkyAuth
from .theme import ThemeManager
from .replay_queue import RollingReplayQueue

import sys
import logging
import certifi
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Ensure UTF-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ALL_RUNWAYS = ["12L", "30R", "12R", "30L", "4", "22", "17", "35"]

FLOW_GROUP_MAP = {
    "30R": "30", "30L": "30",
    "12L": "12", "12R": "12",
    "4": "4", "22": "22",
    "17": "17", "35": "35"
}


class TelemetryState:
    """Thread-safe state container storing active flight operations, weather, and runway roles."""
    def __init__(self):
        self._lock = threading.Lock()
        self._esp32_heartbeat = None
        self.theme_manager = ThemeManager()
        self.replay_queue = RollingReplayQueue(persistence_file=REPLAY_QUEUE_FILE)
        self._state = {
            "timestamp": int(time.time()),
            "iso_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "INITIALIZING",
            "source": DATA_SOURCE,
            "tracked_count": 0,
            "active_count": 0,
            "active_operations": [],
            "primary_operation": None,
            "runway_summary": {rw: {"status": "IDLE", "callsign": None} for rw in ALL_RUNWAYS},
            "weather": {
                "flight_category": "VFR",
                "temp_f": 59,
                "temp_c": 15,
                "wind": "270@11kt",
                "pressure": "30.06 inHg",
                "condition": "Clear",
                "raw": "METAR KMSP (Pending initialization)"
            },
            "runway_roles": {
                "landing": None,
                "departure": None,
                "summary": "RW: Standby",
                "full_summary": "Standby / Waiting for Traffic"
            },
            "runway_roles_summary": "RW: Standby",
            "theme": self.theme_manager.get_theme()
        }

    def get_snapshot(self) -> dict:
        with self._lock:
            # Ensure latest theme is always attached
            self._state["theme"] = self.theme_manager.get_theme()
            return copy.deepcopy(self._state)

    def update_theme(self, updates: dict) -> dict:
        new_theme = self.theme_manager.update_theme(updates)
        with self._lock:
            self._state["theme"] = new_theme
        return new_theme

    def get_theme(self) -> dict:
        return self.theme_manager.get_theme()

    def update_esp32_heartbeat(self, ip: str, rssi=None, heap=None, uptime=None, fps=None):
        now_ts = int(time.time())
        with self._lock:
            node = {
                "ip": ip,
                "rssi": int(rssi) if rssi is not None else None,
                "free_heap_kb": round(int(heap) / 1024, 1) if heap is not None else None,
                "uptime_sec": int(uptime) if uptime is not None else None,
                "fps": int(fps) if fps is not None else None,
                "last_seen_ts": now_ts,
                "online": True
            }
            self._esp32_heartbeat = node
            self._state["esp32_node"] = node

    def update(self, tracked_count: int, active_ops: list[dict], source_name: str,
               weather: dict = None, runway_roles: dict = None, network_status: dict = None):
        now_ts = int(time.time())
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build individual runway status summary
        runway_summary = {rw: {"status": "IDLE", "callsign": None, "action": None} for rw in ALL_RUNWAYS}
        for op in active_ops:
            rw = op.get("runway")
            if rw in runway_summary:
                prev_status = runway_summary[rw].get("status")
                # Prioritize LANDING if multiple operations exist on the same runway
                if prev_status == "LANDING" and op.get("action") != "LANDING":
                    continue
                runway_summary[rw] = {
                    "status": op.get("action"),
                    "callsign": op.get("flight_label"),
                    "aircraft_type": op.get("aircraft_type")
                }

        # Select primary operation (prioritize LANDING over TAKEOFF)
        landings = [op for op in active_ops if op.get("action") == "LANDING"]
        primary = landings[0] if landings else (active_ops[0] if active_ops else None)

        current_roles = runway_roles or {
            "landing": None,
            "departure": None,
            "summary": "RW: Standby",
            "full_summary": "Standby / Waiting for Traffic"
        }
        current_weather = weather or get_current_weather()
        current_network = network_status or {
            "online": True,
            "last_successful_poll": now_ts,
            "consecutive_failures": 0
        }

        now_dt = datetime.datetime.now()
        updated_time_str = now_dt.strftime("%I:%M:%S%p")

        new_state = {
            "timestamp": now_ts,
            "iso_time": now_iso,
            "updated_time": updated_time_str,
            "status": "ACTIVE" if active_ops else "IDLE",
            "source": source_name,
            "tracked_count": tracked_count,
            "active_count": len(active_ops),
            "active_operations": active_ops,
            "primary_operation": primary,
            "runway_summary": runway_summary,
            "weather": current_weather,
            "runway_roles": current_roles,
            "runway_roles_summary": current_roles.get("summary", "RW: Standby"),
            "network": current_network
        }

        if self._esp32_heartbeat:
            # Mark offline if last contact > 45s
            self._esp32_heartbeat["online"] = (now_ts - self._esp32_heartbeat.get("last_seen_ts", 0)) < 45
            new_state["esp32_node"] = self._esp32_heartbeat

        with self._lock:
            self._state = new_state

        # Enqueue active runway operations into rolling replay queue
        for op in active_ops:
            try:
                self.replay_queue.record_operation(op, timestamp=now_ts)
            except Exception as e:
                logger.debug("Replay queue recording error: %s", e)

        # Optionally write atomic snapshot to RAM disk (/dev/shm)
        try:
            temp_file = f"{STATE_FILE}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(new_state, f, indent=2)
            import os
            os.replace(temp_file, STATE_FILE)
        except Exception:
            pass

    def get_replay_batch(self, window_minutes: int = 30) -> dict:
        """Returns structured 30-minute replay batch for office mode."""
        snapshot = self.get_snapshot()
        return self.replay_queue.get_batch(
            window_minutes=window_minutes,
            weather=snapshot.get("weather"),
            runway_roles=snapshot.get("runway_roles"),
            theme=snapshot.get("theme"),
            network=snapshot.get("network")
        )


class TelemetryTracker:
    """Telemetry collector and spatial reasoning engine."""
    def __init__(self, state: TelemetryState = None, auth: OpenSkyAuth = None, auth_profile: str = None, meta: MetadataResolver = None):
        self.state = state if state is not None else TelemetryState()
        self.auth = auth if auth is not None else OpenSkyAuth(profile=auth_profile)
        self.meta = meta if meta is not None else MetadataResolver(auth=self.auth)
        self.running = False
        self._thread = None
        
        # Runway operational role tracking
        self.last_landing_runway = None
        self.last_departure_runway = None
        self.active_flow_group = None

        # Multi-poll aircraft state history (tracks ground-to-air departure transitions)
        self._ground_aircraft = {}  # icao24 -> {"seen_at": ts, "lat": lat, "lon": lon, "callsign": callsign}

        # Active flight operations hysteresis hold cache (bridges rollout deceleration and telemetry drops)
        self._held_operations = {}  # op_key -> {"op": dict, "expires_at": float}

        # Network outage resilience & rate-limit tracking
        self.internet_online = True
        self.last_successful_poll_ts = int(time.time())
        self.consecutive_failures = 0
        self.current_poll_interval = OPENSKY_POLL_INTERVAL
        self.opensky_lockout_until = 0.0

        # OpenSky daily quota tracking & auto-backoff (resets at 00:00 UTC)
        self.daily_requests_count = 0
        self.daily_reset_date = datetime.datetime.now(datetime.timezone.utc).date()
        self.quota_limit = 4000 if self.auth.is_authenticated() else 400

        auth_desc = self.auth.get_auth_status_str()
        logger.info("TelemetryTracker initialized. [%s] | Poll interval: %ss", auth_desc, OPENSKY_POLL_INTERVAL)

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TelemetryWorker")
        self._thread.start()
        logger.info("TelemetryTracker background worker started using source: '%s'.", DATA_SOURCE)

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        # Flush modified caches to disk cleanly on shutdown
        try:
            self.meta.flush_caches()
        except Exception:
            pass

    def _run_loop(self):
        while self.running:
            try:
                if DATA_SOURCE == "dump1090":
                    self._poll_dump1090()
                    time.sleep(DUMP1090_POLL_INTERVAL)
                else:
                    self._poll_opensky()
                    time.sleep(self.current_poll_interval)
            except Exception as e:
                logger.error("Unhandled exception in poll loop: %s", e)
                time.sleep(5.0)

    def fetch_opensky(self) -> list[dict]:
        """Polls OpenSky directly using configured credentials and returns parsed aircraft state vectors."""
        now = time.time()
        if now < self.opensky_lockout_until:
            # Active rate-limit lockout period: avoid hammering OpenSky with doomed requests
            self.last_fetch_success = False
            return []

        headers = self.auth.get_headers()
        basic_auth = self.auth.get_basic_auth()

        # Update daily requests counter (resets at 00:00 UTC)
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self.daily_reset_date:
            self.daily_requests_count = 0
            self.daily_reset_date = today

        self.daily_requests_count += 1

        # Proactive Quota Protection: If using 85%+ of daily quota, back off polling interval
        if self.quota_limit and self.daily_requests_count >= int(self.quota_limit * 0.85):
            backoff_target = max(OPENSKY_POLL_INTERVAL, 40.0)
            if self.current_poll_interval < backoff_target:
                pct = (self.daily_requests_count / self.quota_limit) * 100
                logger.warning("Daily OpenSky quota at %.1f%% (%d/%d). Auto-slowing poll to %ss to prevent 429 lockout.",
                               pct, self.daily_requests_count, self.quota_limit, backoff_target)
                self.current_poll_interval = backoff_target

        try:
            try:
                res = requests.get(OPENSKY_URL, params=MSP_BBOX, headers=headers, auth=basic_auth, timeout=10, verify=certifi.where())
            except requests.exceptions.SSLError:
                res = requests.get(OPENSKY_URL, params=MSP_BBOX, headers=headers, auth=basic_auth, timeout=10, verify=False)

            if res.status_code == 429:
                retry_sec = 300
                try:
                    retry_sec = int(res.headers.get("X-Rate-Limit-Retry-After-Seconds") or res.headers.get("Retry-After") or "300")
                except Exception:
                    pass
                self.opensky_lockout_until = time.time() + retry_sec
                hours = retry_sec / 3600.0
                logger.warning("OpenSky rate limit reached (HTTP 429). Retry in %.1fh (%ss).", hours, retry_sec)
                if not self.auth.is_authenticated():
                    logger.info("TIP: Add OpenSky credentials in .env or credentials.json to increase daily quota from 400 to 4,000 requests!")
                self.last_fetch_success = False
                self.current_poll_interval = min(float(retry_sec), 60.0)
                return []

            res.raise_for_status()
            data = res.json()
            states = data.get("states") or []
            parsed = []
            for plane in states:
                parsed.append({
                    "icao24": str(plane[0]).strip().lower() if plane[0] else None,
                    "callsign": str(plane[1]).strip() if plane[1] else "",
                    "lon": plane[5],
                    "lat": plane[6],
                    "altitude_m": plane[7],
                    "velocity_ms": plane[9],
                    "heading": plane[10],
                    "vertical_rate_ms": plane[11]
                })

            if not self.internet_online:
                logger.info("Internet connectivity restored. Resumed OpenSky live telemetry.")

            self.internet_online = True
            self.last_fetch_success = True
            self.last_successful_poll_ts = int(time.time())
            self.consecutive_failures = 0
            if self.quota_limit and self.daily_requests_count >= int(self.quota_limit * 0.85):
                self.current_poll_interval = max(OPENSKY_POLL_INTERVAL, 40.0)
            else:
                self.current_poll_interval = OPENSKY_POLL_INTERVAL
            return parsed
        except requests.exceptions.HTTPError as e:
            self.consecutive_failures += 1
            self.last_fetch_success = False
            if hasattr(e, "response") and e.response is not None and e.response.status_code == 429:
                retry_sec = 300
                try:
                    retry_sec = int(e.response.headers.get("X-Rate-Limit-Retry-After-Seconds") or e.response.headers.get("Retry-After") or "300")
                except Exception:
                    pass
                self.opensky_lockout_until = time.time() + retry_sec
                hours = retry_sec / 3600.0
                logger.warning("OpenSky rate limit reached (HTTP 429). Retry in %.1fh (%ss).", hours, retry_sec)
                if not self.auth.is_authenticated():
                    logger.info("TIP: Add OpenSky credentials in .env or credentials.json to increase daily quota to 4,000 requests.")
                self.current_poll_interval = min(float(retry_sec), 60.0)
                return []

            if self.internet_online:
                logger.warning("OpenSky request failed (%s). Entering backoff retry mode...", e)
                self.internet_online = False
            self.current_poll_interval = min(60.0, OPENSKY_POLL_INTERVAL * (1.5 ** min(self.consecutive_failures, 4)))
            return []
        except Exception as e:
            self.consecutive_failures += 1
            self.last_fetch_success = False
            if self.internet_online:
                logger.warning("Internet connection lost (%s). Entering backoff retry mode...", e)
                self.internet_online = False

            # Exponential backoff capped at 60 seconds to avoid hammering network or syslog
            self.current_poll_interval = min(60.0, OPENSKY_POLL_INTERVAL * (1.5 ** min(self.consecutive_failures, 4)))
            return []

    def _poll_opensky(self):
        parsed_aircraft = self.fetch_opensky()
        if self.last_fetch_success:
            self._process_aircraft_list(parsed_aircraft, "OpenSky")
        else:
            # Preserve last known valid weather and runway roles during brief drops
            prev = self.state.get_snapshot()
            status_label = "OpenSky (Rate Limited)" if time.time() < self.opensky_lockout_until else "OpenSky (Offline)"
            net_status = {
                "online": False,
                "rate_limited": time.time() < self.opensky_lockout_until,
                "requests_today": self.daily_requests_count,
                "quota_limit": self.quota_limit,
                "quota_percent": round((self.daily_requests_count / self.quota_limit) * 100, 1) if self.quota_limit else 100.0,
                "last_successful_poll": self.last_successful_poll_ts,
                "consecutive_failures": self.consecutive_failures
            }
            self.state.update(
                tracked_count=0,
                active_ops=[],
                source_name=status_label,
                weather=prev.get("weather"),
                runway_roles=prev.get("runway_roles"),
                network_status=net_status
            )

    def _poll_dump1090(self):
        """Polls a local dump1090 / readsb / tar1090 server running on the Pi."""
        try:
            res = requests.get(DUMP1090_URL, timeout=3)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            logger.warning("Local dump1090 request failed: %s", e)
            return

        aircraft_raw = data.get("aircraft", [])
        parsed_aircraft = []
        for ac in aircraft_raw:
            alt_m = (ac.get("alt_baro") * 0.3048) if isinstance(ac.get("alt_baro"), (int, float)) else None
            speed_ms = (ac.get("gs") * 0.514444) if isinstance(ac.get("gs"), (int, float)) else None
            vert_ms = (ac.get("baro_rate") * 0.00508) if isinstance(ac.get("baro_rate"), (int, float)) else None

            parsed_aircraft.append({
                "icao24": ac.get("hex", "").strip().lower(),
                "callsign": ac.get("flight", "").strip(),
                "lon": ac.get("lon"),
                "lat": ac.get("lat"),
                "altitude_m": alt_m,
                "velocity_ms": speed_ms,
                "heading": ac.get("track"),
                "vertical_rate_ms": vert_ms
            })

        self._process_aircraft_list(parsed_aircraft, "dump1090")

    def process_telemetry(self, aircraft_list: list[dict], source_label: str = "OpenSky"):
        """Public method for external callers (e.g. Office BLE Bridge)."""
        self._process_aircraft_list(aircraft_list, source_label)

    def get_state_dict(self) -> dict:
        """Returns snapshot dictionary."""
        return self.state.get_snapshot()

    def _process_aircraft_list(self, aircraft_list: list[dict], source_label: str):
        now_ts = time.time()
        active_operations = []
        freshly_matched_keys = set()

        # 0. Track ground/taxi traffic at KMSP for multi-poll transition tracking
        for ac in aircraft_list:
            ic = ac.get("icao24")
            v = ac.get("velocity_ms")
            alt = ac.get("altitude_m")
            if ic and v is not None and v < MIN_VELOCITY_MS and alt is not None and alt < 500.0:
                self._ground_aircraft[ic] = {
                    "seen_at": now_ts,
                    "lat": ac.get("lat"),
                    "lon": ac.get("lon"),
                    "callsign": ac.get("callsign", "")
                }

        # Prune ground records older than 5 minutes
        ground_cutoff = now_ts - 300.0
        self._ground_aircraft = {k: v for k, v in self._ground_aircraft.items() if v["seen_at"] > ground_cutoff}

        for ac in aircraft_list:
            lat = ac.get("lat")
            lon = ac.get("lon")
            velocity = ac.get("velocity_ms")
            altitude = ac.get("altitude_m")
            heading = ac.get("heading")
            vert_rate = ac.get("vertical_rate_ms")

            # 1. Kinematic Gating
            if velocity is None or velocity < MIN_VELOCITY_MS:
                continue
            if altitude is None or altitude > MAX_ALTITUDE_M:
                continue

            # 2. Spatial Runway Corridor Matching & Disambiguation (with departure fan support)
            rw_match = get_runway_match(lat, lon, heading, vertical_rate=vert_rate)
            if not rw_match:
                continue

            # 3. Action Classification
            if vert_rate is not None and vert_rate > TAKEOFF_MIN_VERT_RATE:
                action = "TAKEOFF"
                # If aircraft was previously seen on the ground, clean up transition tracker
                ac_icao = ac.get("icao24")
                if ac_icao and ac_icao in self._ground_aircraft:
                    self._ground_aircraft.pop(ac_icao, None)
            elif vert_rate is not None and vert_rate < LANDING_MAX_VERT_RATE:
                action = "LANDING"
            else:
                # Level transit in corridor is skipped
                continue

            # 4. Metadata Resolution (uses async queue for instant poll loop latency)
            raw_callsign = ac.get("callsign", "UNKNOWN")
            airline_name, flight_num, flight_label = self.meta.resolve_airline(raw_callsign)
            type_code, aircraft_type = self.meta.resolve_airframe(ac.get("icao24"), callsign=raw_callsign, async_lookup=True)
            route = self.meta.resolve_route(raw_callsign, action, async_lookup=True)

            # Secondary backfill: if route resolution discovered aircraft_type from FlightAware, adopt it!
            if (type_code == "UNKNOWN" or aircraft_type == "UNKNOWN") and raw_callsign:
                cached_entry = self.meta.routes_db.get(raw_callsign)
                if isinstance(cached_entry, dict) and cached_entry.get("aircraft_type"):
                    type_code = cached_entry["aircraft_type"]
                    aircraft_type = self.meta.airframes.get(type_code, type_code)
                    if ac.get("icao24"):
                        self.meta.aircraft_db[ac.get("icao24").lower().strip()] = type_code
                        self.meta._db_modified = True

            # Conversions
            speed_kts = int(velocity * 1.94384) if velocity else 0
            alt_ft = int(altitude * 3.28084) if altitude else 0
            vert_fpm = int(vert_rate * 196.85) if vert_rate else 0

            op = {
                "runway": rw_match["runway"],
                "zone": rw_match["zone"],
                "action": action,
                "callsign": raw_callsign,
                "airline": airline_name,
                "flight_number": flight_num,
                "flight_label": flight_label,
                "aircraft_type": aircraft_type,
                "type_code": type_code,
                "route": route,
                "altitude_ft": alt_ft,
                "speed_kts": speed_kts,
                "vertical_rate_fpm": vert_fpm,
                "progress": rw_match["t_progress"],
                "cross_track_m": rw_match["cross_track_m"],
                "heading_deg": int(heading) if heading else 0,
                "lat": round(lat, 5),
                "lon": round(lon, 5)
            }
            op_key = f"{op['runway']}_{op['callsign']}_{op['action']}"
            freshly_matched_keys.add(op_key)
            self._held_operations[op_key] = {
                "op": op,
                "expires_at": now_ts + 25.0
            }
            active_operations.append(op)

            logger.info("  ✈ MATCH! [%s] %s | %s on %s | Prog: %d%% | Spd: %dkts, Alt: %dft",
                        aircraft_type, flight_label, action, rw_match['runway'],
                        int(rw_match['t_progress'] * 100), speed_kts, alt_ft)

        # Clean up expired held operations, and preserve unexpired active operations
        # to bridge telemetry drops, rollout deceleration, and OpenSky polling jitter.
        for op_key, held in list(self._held_operations.items()):
            if now_ts >= held["expires_at"]:
                del self._held_operations[op_key]
            elif op_key not in freshly_matched_keys:
                active_operations.append(held["op"])

        # 5. Airport Operational Flow Constraint:
        # Enforce that only 1 single runway OR 1 parallel pair (12L/12R or 30R/30L) is active at once.
        VALID_FLOWS = [
            {"30R", "30L"},  # Parallel NW flow
            {"12L", "12R"},  # Parallel SE flow
            {"4"},           # Crosswind NE
            {"22"},          # Crosswind SW
            {"17"},          # North-South S
            {"35"}           # North-South N
        ]
        if len(active_operations) > 1:
            best_matches = []
            best_score = float('inf')
            for flow in VALID_FLOWS:
                matches = [op for op in active_operations if op["runway"] in flow]
                if matches:
                    fresh_count = sum(1 for m in matches if f"{m['runway']}_{m['callsign']}_{m['action']}" in freshly_matched_keys)
                    score = (100 - fresh_count * 60 - len(matches) * 20) + sum(m["cross_track_m"] for m in matches) / len(matches)
                    if score < best_score:
                        best_score = score
                        best_matches = matches
            active_operations = best_matches if best_matches else [active_operations[0]]

            # Purge held operations that do not belong to the winning flow
            winning_runways = {op["runway"] for op in active_operations}
            for op_key in list(self._held_operations.keys()):
                rw = op_key.split("_")[0]
                if rw not in winning_runways:
                    del self._held_operations[op_key]

        # 6. Update Runway Operational Role Tracking (Arrivals vs Departures)
        for op in active_operations:
            rw = op.get("runway")
            act = op.get("action")
            flow_grp = FLOW_GROUP_MAP.get(rw)

            # Check for airport flow reversal (e.g. NW 30s -> SE 12s)
            if flow_grp and self.active_flow_group and flow_grp != self.active_flow_group:
                self.last_landing_runway = None
                self.last_departure_runway = None

            if flow_grp:
                self.active_flow_group = flow_grp

            if act == "LANDING":
                self.last_landing_runway = rw
            elif act == "TAKEOFF":
                self.last_departure_runway = rw

        # Determine operational runway roles (Hierarchy: 1. Live ADS-B -> 2. D-ATIS Advisory Fallback -> 3. Standby)
        datis = get_current_datis()
        closed_runways = datis.get("closed_runways", []) if datis else []
        atis_code = datis.get("code") if datis else None

        if self.last_landing_runway and self.last_departure_runway:
            summary = f"ARR {self.last_landing_runway} / DEP {self.last_departure_runway}"
            full_summary = f"LANDING {self.last_landing_runway} / DEPARTURES {self.last_departure_runway}"
            landing_role = self.last_landing_runway
            dep_role = self.last_departure_runway
            source_role = "ADS-B"
        elif self.last_landing_runway:
            summary = f"LANDING: {self.last_landing_runway}"
            full_summary = f"LANDING {self.last_landing_runway} (No Dep Active)"
            landing_role = self.last_landing_runway
            dep_role = None
            source_role = "ADS-B"
        elif self.last_departure_runway:
            summary = f"DEPARTURES: {self.last_departure_runway}"
            full_summary = f"DEPARTURES {self.last_departure_runway} (No Arr Active)"
            landing_role = None
            dep_role = self.last_departure_runway
            source_role = "ADS-B"
        elif datis and datis.get("valid"):
            # ADS-B has no recent physical movements. Use D-ATIS advisory as fallback.
            summary = datis["summary"]
            full_summary = datis["full_summary"]
            landing_role = datis.get("primary_arr")
            dep_role = datis.get("primary_dep")
            source_role = "D-ATIS"
        else:
            # Neither ADS-B nor D-ATIS available (or D-ATIS unreachable/unparseable). Fail safe to Standby.
            summary = "RW: Standby"
            full_summary = "Standby / Waiting for Traffic"
            landing_role = None
            dep_role = None
            source_role = "STANDBY"

        runway_roles = {
            "landing": landing_role,
            "departure": dep_role,
            "summary": summary,
            "full_summary": full_summary,
            "source": source_role,
            "closed_runways": closed_runways,
            "atis_code": atis_code
        }

        # 7. Ingest live KMSP METAR surface weather
        current_weather = get_current_weather()

        net_status = {
            "online": True,
            "rate_limited": False,
            "requests_today": self.daily_requests_count,
            "quota_limit": self.quota_limit,
            "quota_percent": round((self.daily_requests_count / self.quota_limit) * 100, 1) if self.quota_limit else 0,
            "last_successful_poll": self.last_successful_poll_ts,
            "consecutive_failures": 0
        }

        self.state.update(len(aircraft_list), active_operations, source_label, current_weather, runway_roles, network_status=net_status)
