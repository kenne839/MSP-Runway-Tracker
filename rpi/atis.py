"""
MSP Runway Tracker - KMSP D-ATIS Advisory Module
=================================================
Fetches and extracts runway usage and closures from the digital ATIS (D-ATIS)
for KMSP via https://atis.info/api/kmsp.

Failsafe Policy:
- Completely advisory and non-blocking.
- Live ADS-B telemetry ALWAYS supersedes D-ATIS.
- If the endpoint cannot be reached, times out, or cannot be parsed, D-ATIS
  is safely ignored with zero effect on the system, falling back to ADS-B
  or "RW: Standby".

Update Frequency:
- Polled every 600 seconds (10 minutes). D-ATIS is issued hourly or upon major
  operational changes, so 10-minute cadence is optimal while minimizing load
  on the community API server.
"""

import time
import json
import re
import threading
import urllib.request
import urllib.error

DATIS_URL = "https://atis.info/api/kmsp"
DATIS_POLL_INTERVAL = 600  # 10 minutes
REQUEST_TIMEOUT = 5.0      # 5-second quick abort to never stall callers

KNOWN_RUNWAYS = {"12L", "12R", "30L", "30R", "4", "22", "17", "35"}


class DatisService:
    """Thread-safe background service fetching and parsing KMSP D-ATIS."""
    def __init__(self, poll_interval: int = DATIS_POLL_INTERVAL):
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._last_poll = 0
        self._datis = None
        # Attempt initial fetch non-blockingly
        threading.Thread(target=self._fetch_datis, daemon=True, name="DatisInitialFetch").start()

    def get_datis(self) -> dict | None:
        """Returns the latest parsed D-ATIS dictionary, or None if unavailable/unparseable."""
        now = time.time()
        if now - self._last_poll >= self.poll_interval:
            threading.Thread(target=self._fetch_datis, daemon=True, name="DatisFetchWorker").start()
        with self._lock:
            return dict(self._datis) if self._datis else None

    def _fetch_datis(self):
        self._last_poll = time.time()
        req = urllib.request.Request(
            DATIS_URL,
            headers={"User-Agent": "KMSP-Runway-Tracker/1.0 (Aviation Telemetry Hub)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status != 200:
                    return
                raw_bytes = resp.read()
                data = json.loads(raw_bytes.decode("utf-8"))
                if not data or not isinstance(data, list):
                    return

                parsed = self._parse_datis_payload(data)
                if parsed and parsed.get("valid"):
                    with self._lock:
                        self._datis = parsed
        except Exception as e:
            # Completely silent / graceful degradation: D-ATIS failure must never disrupt core operations
            pass

    def _parse_datis_payload(self, data: list) -> dict:
        arr_code = None
        dep_code = None
        arr_runways = []
        dep_runways = []
        closed_runways = set()
        raw_reports = []

        for entry in data:
            if not isinstance(entry, dict):
                continue
            ttype = entry.get("type", "").lower()
            code = entry.get("code", "")
            datis_text = entry.get("datis", "")
            raw_reports.append(datis_text)

            if ttype == "arr":
                arr_code = code
            elif ttype == "dep":
                dep_code = code

            # Split into clauses by punctuation
            clauses = [c.strip() for c in re.split(r"[.;]", datis_text) if c.strip()]

            for clause in clauses:
                # 1. Runway Closures: Clause containing CLSD or CLOSED
                if any(k in clause for k in ("CLSD", "CLOSED")):
                    # Match known KMSP runways in the closure clause
                    matches = re.findall(r"\b(12L|12R|30L|30R|4|22|17|35)\b", clause)
                    closed_runways.update(matches)

                # 2. Arrivals / Approaches: Clause containing APCH, APPROACH, LANDING, or IN USE
                # (and not closure)
                elif any(k in clause for k in ("APCH", "APPROACH", "LANDING", "IN USE")):
                    matches = re.findall(r"\b(12L|12R|30L|30R|4|22|17|35)\b", clause)
                    for rw in matches:
                        if rw in KNOWN_RUNWAYS and rw not in arr_runways:
                            arr_runways.append(rw)

                # 3. Departures: Clause containing DEPARTING, DEPARTURES, or DEP
                elif any(k in clause for k in ("DEPARTING", "DEPARTURES", "DEP RWY", "DEP RWYS")):
                    matches = re.findall(r"\b(12L|12R|30L|30R|4|22|17|35)\b", clause)
                    for rw in matches:
                        if rw in KNOWN_RUNWAYS and rw not in dep_runways:
                            dep_runways.append(rw)

        # Build composite ATIS letter code: e.g. "D" or "D/Q"
        code_str = ""
        if arr_code and dep_code and arr_code != dep_code:
            code_str = f"{arr_code}/{dep_code}"
        elif arr_code:
            code_str = arr_code
        elif dep_code:
            code_str = dep_code

        # If we couldn't identify any arrival or departure runways, consider it unparseable
        if not arr_runways and not dep_runways:
            return {"valid": False}

        # Format runway roles summary
        # E.g. "ARR 30R / DEP 30L"
        # Prioritize 30R for arrivals and 30L for departures if both in set (MSP standard parallel ops)
        primary_arr = arr_runways[0] if arr_runways else None
        if "30R" in arr_runways:
            primary_arr = "30R"
        elif "12R" in arr_runways:
            primary_arr = "12R"

        primary_dep = dep_runways[0] if dep_runways else None
        if "30L" in dep_runways:
            primary_dep = "30L"
        elif "12L" in dep_runways:
            primary_dep = "12L"

        if primary_arr and primary_dep:
            summary = f"ARR {primary_arr} / DEP {primary_dep}"
            full_summary = f"LANDING {primary_arr} / DEPARTURES {primary_dep} (ATIS)"
        elif primary_arr:
            summary = f"LANDING: {primary_arr}"
            full_summary = f"LANDING {primary_arr} (ATIS)"
        elif primary_dep:
            summary = f"DEPARTURES: {primary_dep}"
            full_summary = f"DEPARTURES {primary_dep} (ATIS)"
        else:
            summary = "RW: Standby"
            full_summary = "Standby / Waiting for Traffic"

        return {
            "valid": True,
            "code": code_str,
            "arr_code": arr_code,
            "dep_code": dep_code,
            "landing_runways": arr_runways,
            "departure_runways": dep_runways,
            "closed_runways": sorted(list(closed_runways)),
            "primary_arr": primary_arr,
            "primary_dep": primary_dep,
            "summary": summary,
            "full_summary": full_summary,
            "updated_at": int(time.time())
        }


# Global singleton instance
_datis_instance = None

def get_current_datis() -> dict | None:
    """Returns parsed D-ATIS data or None if unavailable/unparseable."""
    global _datis_instance
    if _datis_instance is None:
        _datis_instance = DatisService()
    return _datis_instance.get_datis()
