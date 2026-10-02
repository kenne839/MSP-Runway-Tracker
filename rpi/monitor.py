"""
Live HDMI / Terminal Action Monitor for Raspberry Pi.
Renders an auto-updating ANSI terminal dashboard to stdout for quick verification
on an HDMI monitor or SSH session.

Usage:
    python3 -m rpi.monitor
"""

import sys
import time
import json
import urllib.request
import urllib.error
import os

# ANSI escape codes for terminal coloring
CLEAR_SCREEN = "\033[2J\033[H"
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RED = "\033[31m"
GRAY = "\033[90m"
WHITE = "\033[97m"

def fetch_state(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MSP-HDMI-Monitor/1.0"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def render_dashboard(data: dict | None, url: str):
    output = []
    output.append(CLEAR_SCREEN)
    output.append(f"{BOLD}{CYAN}================================================================================")
    output.append(f"  KMSP RUNWAY TELEMETRY MONITOR (HDMI / Console Output)")
    output.append(f"================================================================================{RESET}")

    if not data:
        output.append(f"\n{RED}{BOLD}[DISCONNECTED]{RESET} Unable to connect to telemetry server at {url}")
        output.append(f"{GRAY}Ensure the service is running: python3 -m rpi.main{RESET}\n")
        print("\n".join(output))
        return

    ts = data.get("timestamp", 0)
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "N/A"
    active_count = data.get("active_count", 0)
    tracked_count = data.get("tracked_count", 0)
    source = data.get("source", "UNKNOWN")

    status_color = GREEN if active_count > 0 else GRAY
    output.append(f"Status: {status_color}{BOLD}{data.get('status', 'IDLE')}{RESET} | "
                  f"Active Operations: {BOLD}{active_count}{RESET} | "
                  f"Airspace Tracked: {tracked_count} | "
                  f"Source: {YELLOW}{source}{RESET} | "
                  f"Time: {time_str}")
    output.append(f"{GRAY}{'-' * 80}{RESET}")

    # Runway Grid Overview
    output.append(f"\n{BOLD}{WHITE}RUNWAY STATUS SUMMARY:{RESET}")
    rw_summary = data.get("runway_summary", {})
    rw_items = []
    for rw, info in rw_summary.items():
        st = info.get("status", "IDLE")
        if st == "LANDING":
            col = GREEN
        elif st == "TAKEOFF":
            col = YELLOW
        else:
            col = GRAY
        callsign = f"({info.get('callsign')})" if info.get("callsign") else ""
        rw_items.append(f"[{BOLD}{rw}{RESET}: {col}{st}{RESET} {callsign}]")

    # Format into 2 columns
    for i in range(0, len(rw_items), 2):
        row = rw_items[i:i+2]
        output.append("  " + "   ".join(f"{item:<40}" for item in row))

    # Active Operations Details
    output.append(f"\n{BOLD}{WHITE}ACTIVE MOVEMENTS (PAYLOAD SENT TO ESP32):{RESET}")
    ops = data.get("active_operations", [])
    if not ops:
        output.append(f"  {GRAY}(No flights currently in KMSP arrival/departure corridors){RESET}")
    else:
        for idx, op in enumerate(ops, 1):
            act = op.get("action")
            act_col = GREEN if act == "LANDING" else YELLOW
            output.append(
                f"\n  {BOLD}#{idx} Runway {CYAN}{op.get('runway')}{RESET} &bull; "
                f"{act_col}{BOLD}{act}{RESET} &bull; "
                f"{WHITE}{op.get('flight_label')}{RESET} "
                f"{GRAY}[{op.get('aircraft_type')} / {op.get('type_code')}]{RESET}"
            )
            output.append(
                f"     Route: {MAGENTA}{op.get('route')}{RESET} | "
                f"Alt: {WHITE}{op.get('altitude_ft')} ft{RESET} | "
                f"Speed: {WHITE}{op.get('speed_kts')} kts{RESET} | "
                f"V-Speed: {op.get('vertical_rate_fpm')} fpm | "
                f"Offset: {op.get('cross_track_m')}m | "
                f"Progress: {int(op.get('progress', 0) * 100)}%"
            )

    # Primary Operation (OLED target)
    output.append(f"\n{GRAY}{'-' * 80}{RESET}")
    output.append(f"{BOLD}ESP32 OLED Target:{RESET}")
    prim = data.get("primary_operation")
    if prim:
        output.append(f"  Line 1: {prim.get('runway')} - {prim.get('action')}")
        output.append(f"  Line 2: {prim.get('flight_label')}")
        output.append(f"  Line 3: {prim.get('aircraft_type')}")
        output.append(f"  Line 4: {prim.get('route')}")
    else:
        output.append(f"  {GRAY}[OLED in IDLE Standby]{RESET}")

    output.append(f"\n{GRAY}Polling {url} every 1.5s &bull; Press Ctrl+C to exit{RESET}")
    print("\n".join(output))

def main():
    port = os.environ.get("MSP_SERVER_PORT", "8080")
    url = f"http://127.0.0.1:{port}/api/runway_state"
    print(f"Connecting to {url}...")
    try:
        while True:
            data = fetch_state(url)
            render_dashboard(data, url)
            time.sleep(1.5)
    except KeyboardInterrupt:
        print(f"\n{RESET}Monitor stopped.")

if __name__ == "__main__":
    main()
