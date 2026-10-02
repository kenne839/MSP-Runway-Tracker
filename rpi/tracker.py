"""
Core telemetry daemon that polls ADS-B state vectors, executes kinematic & spatial
corridor gating, and produces a synchronized, multi-aircraft JSON payload.
"""

import time
import json
import threading
import datetime
import requests
import urllib3
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
    RUNWAYS
)
from .spatial import get_runway_match
from .metadata import MetadataResolver

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ALL_RUNWAYS = ["12L", "30R", "12R", "30L", "4", "22", "17", "35"]

class TelemetryState:
    """Thread-safe state container storing active flight operations."""
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "timestamp": int(time.time()),
            "iso_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "INITIALIZING",
            "source": DATA_SOURCE,
            "tracked_count": 0,
            "active_count": 0,
            "active_operations": [],
            "primary_operation": None,
            "runway_summary": {rw: {"status": "IDLE", "callsign": None} for rw in ALL_RUNWAYS}
        }

    def get_snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def update(self, tracked_count: int, active_ops: list[dict], source_name: str):
        now_ts = int(time.time())
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build individual runway status summary
        runway_summary = {rw: {"status": "IDLE", "callsign": None, "action": None} for rw in ALL_RUNWAYS}
        for op in active_ops:
            rw = op.get("runway")
            if rw in runway_summary:
                runway_summary[rw] = {
                    "status": op.get("action"),
                    "callsign": op.get("flight_label"),
                    "aircraft_type": op.get("aircraft_type")
                }

        # Select primary operation (first active or closest to threshold)
        primary = active_ops[0] if active_ops else None

        new_state = {
            "timestamp": now_ts,
            "iso_time": now_iso,
            "status": "ACTIVE" if active_ops else "IDLE",
            "source": source_name,
            "tracked_count": tracked_count,
            "active_count": len(active_ops),
            "active_operations": active_ops,
            "primary_operation": primary,
            "runway_summary": runway_summary
        }

        with self._lock:
            self._state = new_state

        # Optionally write atomic snapshot to RAM disk (/dev/shm)
        try:
            temp_file = f"{STATE_FILE}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(new_state, f, indent=2)
            # Atomic rename prevents file read tearing
            import os
            os.replace(temp_file, STATE_FILE)
        except Exception:
            pass


class TelemetryTracker:
    """Telemetry collector and spatial reasoning engine."""
    def __init__(self, state: TelemetryState):
        self.state = state
        self.meta = MetadataResolver()
        self.running = False
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TelemetryWorker")
        self._thread.start()
        print(f"TelemetryTracker background worker started using source: '{DATA_SOURCE}'.")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run_loop(self):
        while self.running:
            try:
                if DATA_SOURCE == "dump1090":
                    self._poll_dump1090()
                    time.sleep(DUMP1090_POLL_INTERVAL)
                else:
                    self._poll_opensky()
                    time.sleep(OPENSKY_POLL_INTERVAL)
            except Exception as e:
                print(f"[Tracker Error] Unhandled exception in poll loop: {e}")
                time.sleep(5.0)

    def _poll_opensky(self):
        auth = (OPENSKY_USERNAME, OPENSKY_PASSWORD) if OPENSKY_USERNAME and OPENSKY_PASSWORD else None
        try:
            res = requests.get(OPENSKY_URL, params=MSP_BBOX, auth=auth, timeout=10, verify=False)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"[{time.strftime('%X')}] OpenSky API request failed: {e}")
            return

        states = data.get("states") or []
        parsed_aircraft = []
        for plane in states:
            # OpenSky state vector format:
            # [0] icao24, [1] callsign, [5] lon, [6] lat, [7] baro_alt, [9] velocity, [10] track, [11] vert_rate
            parsed_aircraft.append({
                "icao24": str(plane[0]).strip().lower() if plane[0] else None,
                "callsign": str(plane[1]).strip() if plane[1] else "",
                "lon": plane[5],
                "lat": plane[6],
                "altitude_m": plane[7],
                "velocity_ms": plane[9],
                "heading": plane[10],
                "vertical_rate_ms": plane[11]
            })

        self._process_aircraft_list(parsed_aircraft, "OpenSky")

    def _poll_dump1090(self):
        """Polls a local dump1090 / readsb / tar1090 server running on the Pi."""
        try:
            res = requests.get(DUMP1090_URL, timeout=3)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print(f"[{time.strftime('%X')}] Local dump1090 request failed: {e}")
            return

        aircraft_raw = data.get("aircraft", [])
        parsed_aircraft = []
        for ac in aircraft_raw:
            # dump1090 format: hex, flight, lon, lat, alt_baro (ft), speed (kts), track, baro_rate (ft/min)
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

    def _process_aircraft_list(self, aircraft_list: list[dict], source_label: str):
        active_operations = []

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

            # 2. Spatial Runway Corridor Matching & Disambiguation
            rw_match = get_runway_match(lat, lon, heading)
            if not rw_match:
                continue

            # 3. Action Classification
            if vert_rate is not None and vert_rate > TAKEOFF_MIN_VERT_RATE:
                action = "TAKEOFF"
            elif vert_rate is not None and vert_rate < LANDING_MAX_VERT_RATE:
                action = "LANDING"
            else:
                # Level transit in corridor is skipped
                continue

            # 4. Metadata Resolution
            raw_callsign = ac.get("callsign", "UNKNOWN")
            airline_name, flight_num, flight_label = self.meta.resolve_airline(raw_callsign)
            type_code, aircraft_type = self.meta.resolve_airframe(ac.get("icao24"))
            route = self.meta.resolve_route(raw_callsign, action)

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
            active_operations.append(op)

            print(f"  ✈ MATCH! [{aircraft_type}] {flight_label} | {action} on {rw_match['runway']} | "
                  f"Prog: {int(rw_match['t_progress']*100)}% | Spd: {speed_kts}kts, Alt: {alt_ft}ft")

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
                    # Score: prioritize more matched flights, then minimal cross-track offset
                    score = (100 - len(matches) * 50) + sum(m["cross_track_m"] for m in matches) / len(matches)
                    if score < best_score:
                        best_score = score
                        best_matches = matches
            active_operations = best_matches if best_matches else [active_operations[0]]

        self.state.update(len(aircraft_list), active_operations, source_label)
