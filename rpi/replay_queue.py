"""
Rolling Replay Queue for KMSP Runway Tracker Office Mode.
Maintains a sliding 60-minute in-memory buffer of confirmed runway operations with
disk persistence and deduplication for 30-minute batch replays.
"""

import os
import json
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_MINUTES = 60
DEFAULT_WINDOW_MINUTES = 30


class RollingReplayQueue:
    """Thread-safe sliding window event store for time-shifted replay."""

    def __init__(self, persistence_file: Optional[str] = None, max_retention_minutes: int = DEFAULT_RETENTION_MINUTES):
        self.persistence_file = persistence_file
        self.max_retention_sec = max_retention_minutes * 60
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._seen_signatures: dict[str, float] = {}
        self._db_dirty = False
        self._last_save_ts = 0.0

        if self.persistence_file and os.path.exists(self.persistence_file):
            self._load()

    def _load(self):
        """Loads unexpired events from disk on startup."""
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", [])
            now = time.time()
            max_ts = max((ev.get("timestamp", 0) for ev in events), default=now)
            ref_time = max(now, max_ts)
            cutoff = ref_time - self.max_retention_sec
            valid_events = []
            for ev in events:
                ts = ev.get("timestamp", 0)
                if ts >= cutoff:
                    valid_events.append(ev)
                    sig = ev.get("signature") or self._make_signature(ev.get("callsign", ""), ev.get("runway", ""), ev.get("action", ""), ts)
                    self._seen_signatures[sig] = ts

            self._events = sorted(valid_events, key=lambda x: x["timestamp"])
            logger.info("Restored %d replay events from %s (cutoff: -%dm)", len(self._events), self.persistence_file, self.max_retention_sec // 60)
        except Exception as e:
            logger.warning("Could not restore replay queue from %s: %s", self.persistence_file, e)

    def _save(self, force: bool = False):
        """Atomically snapshots the rolling queue to disk."""
        if not self.persistence_file:
            return
        now = time.time()
        if not force and not self._db_dirty:
            return
        if not force and (now - self._last_save_ts < 30.0):
            # Throttle disk writes to once every 30s
            return

        try:
            temp_path = f"{self.persistence_file}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({"updated_at": int(now), "events": self._events}, f, indent=2)
            os.replace(temp_path, self.persistence_file)
            self._db_dirty = False
            self._last_save_ts = now
        except Exception as e:
            logger.error("Failed to snapshot replay queue to %s: %s", self.persistence_file, e)

    @staticmethod
    def _make_signature(callsign: str, runway: str, action: str, timestamp: float) -> str:
        """Generates 5-minute bucket signature to prevent duplicate duplicate triggers for the same flight."""
        bucket = int(timestamp // 300)
        cs = (callsign or "UNKNOWN").strip().upper()
        rw = (runway or "").strip().upper()
        act = (action or "IDLE").strip().upper()
        return f"{cs}_{rw}_{act}_{bucket}"

    def record_operation(self, op: dict, timestamp: Optional[float] = None) -> bool:
        """
        Records a confirmed runway operation if not already seen in the current 5-minute window.
        Returns True if operation was newly enqueued, False if duplicate.
        """
        now = timestamp if timestamp is not None else time.time()
        callsign = op.get("callsign") or op.get("flight_label") or "UNKNOWN"
        runway = op.get("runway") or ""
        action = op.get("action") or "IDLE"

        if action not in ("LANDING", "TAKEOFF") or not runway:
            return False

        sig = self._make_signature(callsign, runway, action, now)

        with self._lock:
            # Check deduplication
            if sig in self._seen_signatures:
                return False

            # Calculate duration: faster aircraft traverse in less time (typically 20s to 30s)
            spd = op.get("speed_kts", 140)
            if action == "LANDING":
                duration_ms = max(18000, min(32000, int(3500000 / max(80, spd))))
            else:
                duration_ms = max(16000, min(28000, int(3200000 / max(80, spd))))

            event_id = f"{callsign}_{runway}_{int(now)}"
            event_obj = {
                "id": event_id,
                "signature": sig,
                "timestamp": now,
                "runway": runway,
                "action": action,
                "duration_ms": duration_ms,
                "callsign": callsign,
                "flight_label": op.get("flight_label") or callsign,
                "aircraft_type": op.get("aircraft_type") or "Unknown",
                "type_code": op.get("type_code") or "AIRC",
                "route": op.get("route") or "",
                "speed_kts": spd,
                "altitude_ft": op.get("altitude_ft", 0)
            }

            self._seen_signatures[sig] = now
            self._events.append(event_obj)
            self._db_dirty = True

            # Prune events older than retention limit
            cutoff = now - self.max_retention_sec
            self._events = [e for e in self._events if e["timestamp"] >= cutoff]

            # Prune old signatures
            self._seen_signatures = {k: ts for k, ts in self._seen_signatures.items() if ts >= cutoff}

            self._save()
            return True

    def get_batch(self, window_minutes: int = DEFAULT_WINDOW_MINUTES,
                  weather: Optional[dict] = None, runway_roles: Optional[dict] = None,
                  theme: Optional[dict] = None, network: Optional[dict] = None,
                  now_epoch: Optional[float] = None) -> dict:
        """
        Builds a structured replay batch dictionary covering the last `window_minutes`.
        """
        now = now_epoch if now_epoch is not None else time.time()
        window_minutes = max(5, min(60, window_minutes))
        window_sec = window_minutes * 60
        start_epoch = now - window_sec

        with self._lock:
            # Filter events falling within [start_epoch, now]
            batch_ops = []
            for ev in self._events:
                ts = ev["timestamp"]
                if ts >= start_epoch and ts <= now:
                    offset_sec = max(0, min(window_sec, int(ts - start_epoch)))
                    clean_op = {
                        "id": ev["id"],
                        "offset_sec": offset_sec,
                        "runway": ev["runway"],
                        "action": ev["action"],
                        "duration_ms": ev["duration_ms"],
                        "callsign": ev["callsign"],
                        "flight_label": ev["flight_label"],
                        "aircraft_type": ev["aircraft_type"],
                        "type_code": ev["type_code"],
                        "route": ev["route"],
                        "speed_kts": ev["speed_kts"],
                        "altitude_ft": ev["altitude_ft"]
                    }
                    batch_ops.append(clean_op)

            batch_ops.sort(key=lambda x: x["offset_sec"])

            # Default fallbacks
            clean_weather = weather or {
                "flight_category": "VFR",
                "temp_f": 68,
                "temp_c": 20,
                "wind": "Calm",
                "pressure": "29.92 inHg",
                "condition": "Clear"
            }
            clean_roles = runway_roles or {
                "summary": "ARR 30R / DEP 30L",
                "landing": "30R",
                "departure": "30L"
            }
            clean_theme = theme or {
                "preset": "classic",
                "brightness": 32,
                "runway_colors": {
                    "landing": [255, 120, 0],
                    "takeoff": [0, 160, 255],
                    "idle": [30, 20, 5]
                }
            }
            clean_network = network or {
                "online": True,
                "rate_limited": False,
                "requests_today": 0,
                "quota_limit": 4000
            }

            return {
                "version": 2,
                "batch_id": int(now),
                "batch_start_epoch": int(start_epoch),
                "batch_end_epoch": int(now),
                "window_seconds": window_sec,
                "weather": clean_weather,
                "runway_roles": clean_roles,
                "theme": clean_theme,
                "network": clean_network,
                "operations_count": len(batch_ops),
                "operations": batch_ops
            }

    def flush(self):
        """Forces an immediate save to disk."""
        with self._lock:
            self._save(force=True)

