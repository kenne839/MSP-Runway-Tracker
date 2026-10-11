"""
Command-line utility for configuring LED themes and brightness on the MSP Runway Tracker.
Allows instant updates via SSH or command prompt without touching code.

Usage examples:
    python -m rpi.theme_cli --show
    python -m rpi.theme_cli --brightness 32
    python -m rpi.theme_cli --preset cyberpunk
    python -m rpi.theme_cli --preset classic --brightness 24
    python -m rpi.theme_cli --landing-tail "#00E5FF" --takeoff-tail "#FF3300"
"""

import sys
import json
import argparse
import urllib.request
import urllib.error

def main():
    parser = argparse.ArgumentParser(description="MSP Runway Tracker - LED Theme & Brightness Customizer")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--brightness", "-b", type=int, help="Master brightness ceiling (5 to 48)")
    parser.add_argument("--preset", "-p", choices=["classic", "cyberpunk", "sapphire", "sunset", "night"],
                        help="Select a color preset")
    parser.add_argument("--landing-head", help="Landing strobe head color (e.g. #FFFFFF or 0xFFFFFF)")
    parser.add_argument("--landing-tail", help="Landing rollout glidepath tail color (e.g. #00FF88)")
    parser.add_argument("--takeoff-head", help="Takeoff roll head color (e.g. #FFE0A0)")
    parser.add_argument("--takeoff-tail", help="Takeoff afterburner flame tail color (e.g. #FF4500)")
    parser.add_argument("--idle-threshold", help="Idle runway threshold color (e.g. #002200)")
    parser.add_argument("--solar-sync", action="store_true", help="Enable KMSP solar-adaptive circadian brightness")
    parser.add_argument("--no-solar-sync", action="store_true", help="Disable solar sync and return to manual brightness")
    parser.add_argument("--show", "-s", action="store_true", help="Display current active theme")

    args = parser.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    # Build update payload
    payload = {}
    if args.preset:
        payload["preset"] = args.preset
    if args.brightness is not None:
        payload["brightness"] = args.brightness
    if args.landing_head:
        payload["landing_head"] = args.landing_head
    if args.landing_tail:
        payload["landing_tail"] = args.landing_tail
    if args.takeoff_head:
        payload["takeoff_head"] = args.takeoff_head
    if args.takeoff_tail:
        payload["takeoff_tail"] = args.takeoff_tail
    if args.idle_threshold:
        payload["idle_threshold"] = args.idle_threshold
    if args.solar_sync:
        payload["solar_sync"] = True
    elif args.no_solar_sync:
        payload["solar_sync"] = False

    if not payload and not args.show:
        parser.print_help()
        print("\nUse --show to view current theme or specify settings to apply.")
        return

    # If updating theme
    if payload:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/theme",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                print("\033[92m[SUCCESS] LED Theme updated!\033[0m")
                print(f"  Active Version:       v{result.get('version')}")
                print(f"  Solar-Sync (Auto):    {'Enabled (Active)' if result.get('solar_sync') else 'Disabled'}")
                if result.get("solar_sync"):
                    print(f"  Solar Elevation:      {result.get('solar_elevation'):+0.2f} deg ({result.get('solar_phase')})")
                    print(f"  Effective Brightness: {result.get('effective_brightness')} / 48")
                else:
                    print(f"  Master Brightness:    {result.get('brightness')} / 48")
                print(f"  Landing Colors:       Head={result.get('landing_head')} | Tail={result.get('landing_tail')}")
                print(f"  Takeoff Colors:       Head={result.get('takeoff_head')} | Tail={result.get('takeoff_tail')}")
                print(f"  Idle Threshold:       {result.get('idle_threshold')}")
                print("\nBroadcasting to ESP32 on next 1.5s poll cycle.")
                return
        except urllib.error.URLError as e:
            print(f"\033[91m[ERROR] Failed to connect to server at {base_url}: {e}\033[0m")
            print("Make sure msp-tracker service or server.py is running.")
            sys.exit(1)

    # If viewing theme (--show)
    if args.show:
        try:
            with urllib.request.urlopen(f"{base_url}/api/theme", timeout=5) as resp:
                theme = json.loads(resp.read().decode("utf-8"))
                print("\033[94m=== KMSP RUNWAY TRACKER - CURRENT LED THEME ===\033[0m")
                print(f"  Theme Version:        v{theme.get('version')}")
                print(f"  Solar-Sync (Auto):    {'Enabled (Active)' if theme.get('solar_sync') else 'Disabled'}")
                if theme.get("solar_sync"):
                    print(f"  Solar Elevation:      {theme.get('solar_elevation'):+0.2f} deg ({theme.get('solar_phase')})")
                    print(f"  Effective Brightness: {theme.get('effective_brightness')} / 48")
                    print(f"  Manual Preference:    {theme.get('brightness')} / 48")
                else:
                    print(f"  Master Brightness:    {theme.get('brightness')} / 48 ({int(theme.get('brightness', 24)/48*100)}%)")
                print(f"  Landing Strobe:       Head: {theme.get('landing_head')} | Tail: {theme.get('landing_tail')}")
                print(f"  Takeoff Roll:         Head: {theme.get('takeoff_head')} | Tail: {theme.get('takeoff_tail')}")
                print(f"  Idle Threshold:       {theme.get('idle_threshold')}")
        except urllib.error.URLError as e:
            print(f"\033[91m[ERROR] Failed to fetch theme from {base_url}: {e}\033[0m")
            sys.exit(1)

if __name__ == "__main__":
    main()

