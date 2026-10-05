"""
KMSP Runway LED Tracker - Office PC Companion & BLE Bridge
Transmits live KMSP flight telemetry to the ESP32-S3 Office Board wirelessly over BLE
(Bluetooth Low Energy), with embedded web server, zero-touch auto-pairing, smart standby,
and Windows startup automation.

Usage:
    python office/bridge.py          # Auto-connects via BLE with embedded web dashboard
    python office/bridge.py --test   # Runs hardware test sequence via BLE
    python office/bridge.py --mode serial --port COM3  # Optional: via Header J2 UART programmer
"""

import os
import sys
import time
import json
import signal
import atexit
import asyncio
import argparse
import threading
from http.server import ThreadingHTTPServer

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpi.config import MSP_BBOX, OPENSKY_URL, OPENSKY_POLL_INTERVAL
from rpi.tracker import TelemetryTracker
from rpi.server import make_handler

# BLE Nordic UART Service UUIDs
BLE_DEVICE_NAME = "KMSP-Runway-Office"
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

OFFICE_DIR = os.path.dirname(os.path.abspath(__file__))
BLE_CACHE_FILE = os.path.join(OFFICE_DIR, ".ble_cache.json")
PID_FILE = os.path.join(OFFICE_DIR, ".bridge.pid")


class OfficeBridge:
    def __init__(self, mode="ble", port=None, poll_interval=None, web_server=True, web_port=None, credentials=None):
        self.mode = mode
        self.port = port
        self.poll_interval = poll_interval if poll_interval is not None else OPENSKY_POLL_INTERVAL
        self.web_server = web_server
        self.web_port = web_port if web_port is not None else int(os.environ.get("OFFICE_WEB_PORT", 18080))

        from rpi.opensky_auth import OpenSkyAuth
        auth = OpenSkyAuth(credentials_path=credentials, profile="office")
        self.tracker = TelemetryTracker(auth=auth)
        self.ble_client = None
        self.serial_conn = None
        self.connected = False
        self.in_standby = False
        self.cached_ble_address = self._load_ble_cache()

        self.httpd = None
        self.http_thread = None

        self._write_pid()
        atexit.register(self.cleanup)

        # Register signal handlers for clean shutdown
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except Exception:
            pass

    def _write_pid(self):
        try:
            with open(PID_FILE, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

    def _remove_pid(self):
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass

    def _handle_signal(self, signum, frame):
        print("\n[\033[93mSHUTDOWN\033[0m] Received shutdown signal. Cleaning up...")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        """Flushes caches to disk and closes network connections cleanly."""
        print("[\033[94mCLEANUP\033[0m] Saving metadata caches and closing link...")
        try:
            if hasattr(self.tracker, "meta"):
                self.tracker.meta.flush_caches()
        except Exception:
            pass

        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass

        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass

        self._remove_pid()

    def _load_ble_cache(self):
        if os.path.exists(BLE_CACHE_FILE):
            try:
                with open(BLE_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("address")
            except Exception:
                pass
        return None

    def _save_ble_cache(self, address):
        self.cached_ble_address = address
        try:
            with open(BLE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"address": address, "name": BLE_DEVICE_NAME, "updated": time.time()}, f, indent=2)
        except Exception:
            pass

    def start_web_server(self):
        """Starts an embedded background web server for live browser monitoring."""
        if not self.web_server:
            return

        try:
            handler_class = make_handler(self.tracker.state)
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.web_port), handler_class)
            self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.http_thread.start()
            print(f"[\033[92mWEB\033[0m] Embedded Dashboard live at http://127.0.0.1:{self.web_port}/")
            print(f"[\033[92mWEB\033[0m] Web Simulator live at  http://127.0.0.1:{self.web_port}/simulator")
        except OSError as e:
            if e.errno == 10048 or "Address already in use" in str(e):
                print(f"[\033[93mWEB NOTICE\033[0m] Port {self.web_port} in use. Web dashboard disabled (BLE bridge active).")
            else:
                print(f"[\033[93mWEB NOTICE\033[0m] Could not start HTTP dashboard: {e}")
        except Exception as e:
            print(f"[\033[93mWEB NOTICE\033[0m] Web server error: {e}")

    def _on_ble_disconnect(self, client):
        """Callback invoked by bleak when ESP32 board is powered off or drops link."""
        print(f"[\033[93mBLE DISCONNECT\033[0m] ESP32 board turned off or disconnected.")
        self.connected = False

    async def connect_ble(self):
        """Scans for and connects to the ESP32-S3 over Bluetooth Low Energy with address caching."""
        try:
            from bleak import BleakScanner, BleakClient
        except ImportError:
            print("[\033[91mERROR\033[0m] Python 'bleak' library not installed.")
            print("Please run: pip install bleak")
            return False

        device = None

        # 1. Fast-path: Try connecting directly to cached MAC address if available
        if self.cached_ble_address:
            try:
                device = await BleakScanner.find_device_by_address(self.cached_ble_address, timeout=2.5)
            except Exception:
                device = None

        # 2. Discovery fallback: Scan by device name
        if not device:
            print(f"[\033[94mBLE\033[0m] Scanning for '{BLE_DEVICE_NAME}'...")
            try:
                device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=6.0)
            except Exception as e:
                print(f"[\033[93mBLE SCAN ERROR\033[0m] {e}")
                return False

        # 3. Discovery fallback 2: Search advertised local names
        if not device:
            try:
                devices = await BleakScanner.discover(timeout=4.0)
                for d in devices:
                    if d.name and BLE_DEVICE_NAME.lower() in d.name.lower():
                        device = d
                        break
            except Exception:
                pass

        if not device:
            return False

        print(f"[\033[92mBLE\033[0m] Found board at {device.address}. Connecting...")
        client = BleakClient(device, disconnected_callback=self._on_ble_disconnect)
        try:
            await client.connect(timeout=8.0)
            self.ble_client = client
            self.connected = True
            self._save_ble_cache(device.address)
            print(f"[\033[92mSUCCESS\033[0m] Connected to {BLE_DEVICE_NAME} ({device.address}) via BLE!")
            return True
        except Exception as e:
            print(f"[\033[91mERROR\033[0m] BLE connection failed: {e}")
            self.connected = False
            return False

    def connect_serial(self):
        """Connects to the ESP32-S3 over USB Serial."""
        try:
            import serial
            import serial.tools.list_ports
        except ImportError:
            print("[\033[91mERROR\033[0m] Python 'pyserial' library not installed.")
            print("Please run: pip install pyserial")
            return False

        target_port = self.port
        if not target_port:
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                desc = (p.description or "").lower()
                if "esp32" in desc or "usb jtag" in desc or "usb serial" in desc or "cdc" in desc:
                    target_port = p.device
                    break
            if not target_port and len(ports) > 0:
                target_port = ports[0].device

        if not target_port:
            return False

        print(f"[\033[94mSERIAL\033[0m] Connecting to {target_port} at 115200 baud...")
        try:
            self.serial_conn = serial.Serial(target_port, 115200, timeout=1.0)
            self.connected = True
            print(f"[\033[92mSUCCESS\033[0m] Connected to {target_port} via USB Serial!")
            return True
        except Exception as e:
            print(f"[\033[91mERROR\033[0m] Serial connection failed: {e}")
            self.connected = False
            return False

    async def ensure_connection(self):
        """Ensures active communication link (auto-reconnecting if dropped)."""
        if self.mode == "ble" or self.mode == "auto":
            if self.ble_client and self.ble_client.is_connected:
                return True
            if await self.connect_ble():
                return True
            if self.mode == "ble":
                return False

        if self.mode == "serial" or self.mode == "auto":
            if self.serial_conn and self.serial_conn.is_open:
                return True
            if self.connect_serial():
                return True

        return False

    async def transmit_payload(self, state_dict):
        """Transmits JSON telemetry packet to the ESP32."""
        payload_str = json.dumps(state_dict) + "\n"
        payload_bytes = payload_str.encode("utf-8")

        # 1. Send via BLE
        if self.ble_client and self.ble_client.is_connected:
            try:
                CHUNK_SIZE = 240
                for i in range(0, len(payload_bytes), CHUNK_SIZE):
                    chunk = payload_bytes[i:i + CHUNK_SIZE]
                    await self.ble_client.write_gatt_char(NUS_RX_UUID, chunk, response=False)
                return True
            except Exception as e:
                print(f"[\033[91mBLE TX ERROR\033[0m] {e}")
                self.connected = False

        # 2. Send via USB Serial
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.write(payload_bytes)
                self.serial_conn.flush()
                return True
            except Exception as e:
                print(f"[\033[91mSERIAL TX ERROR\033[0m] {e}")
                self.connected = False

        return False

    async def sync_initial_state(self):
        """
        Immediately fetches current METAR weather and D-ATIS runway configurations,
        and transmits a clean sync packet to the board on connect.
        Ensures the OLED displays live information immediately upon power-up.
        """
        try:
            # Seed idle state with real-time METAR and D-ATIS
            self.tracker.process_telemetry([], source_label="Office Sync")
            state_dict = self.tracker.get_state_dict()
            success = await self.transmit_payload(state_dict)
            if success:
                wx = state_dict.get("weather", {})
                roles = state_dict.get("runway_roles_summary", "Standby")
                print(f"[\033[92mSYNC\033[0m] Morning Sync complete -> Wx: {wx.get('flight_category', 'VFR')} {wx.get('temp_f', '')}F | Runway: {roles}")
        except Exception as e:
            print(f"[\033[93mSYNC NOTICE\033[0m] Initial state sync skipped: {e}")

    async def run_test_pattern(self):
        """Transmits a live simulation test sequence to verify hardware link."""
        print("[\033[95mTEST\033[0m] Sending test sequence to ESP32 board...")

        test_events = [
            {
                "label": "Test 1: Arrival on RW 30R (Delta 793)",
                "payload": {
                    "active_operations": [{
                        "runway": "30R",
                        "action": "LANDING",
                        "callsign": "DAL793",
                        "flight_label": "Delta Air Lines 793",
                        "aircraft_type": "Boeing 737-900 (B738)",
                        "route": "From KDEN"
                    }],
                    "tracked_count": 14,
                    "runway_summary": {"30R": {"status": "LANDING"}}
                }
            },
            {
                "label": "Test 2: Parallel Ops: Land 30R + Takeoff 30L",
                "payload": {
                    "active_operations": [
                        {
                            "runway": "30R",
                            "action": "LANDING",
                            "callsign": "DAL793",
                            "flight_label": "Delta Air Lines 793",
                            "aircraft_type": "Boeing 737-900 (B738)",
                            "route": "From KDEN"
                        },
                        {
                            "runway": "30L",
                            "action": "TAKEOFF",
                            "callsign": "SKW3822",
                            "flight_label": "SkyWest 3822",
                            "aircraft_type": "CRJ-900 (CRJ9)",
                            "route": "To KORD"
                        }
                    ],
                    "tracked_count": 18,
                    "runway_summary": {"30R": {"status": "LANDING"}, "30L": {"status": "TAKEOFF"}}
                }
            },
            {
                "label": "Test 3: Crosswind Rollout on RW 4/22",
                "payload": {
                    "active_operations": [{
                        "runway": "4",
                        "action": "TAKEOFF",
                        "callsign": "DAL2901",
                        "flight_label": "Delta Air Lines 2901",
                        "aircraft_type": "Airbus A220-300 (BCS3)",
                        "route": "To KBOS"
                    }],
                    "tracked_count": 12,
                    "runway_summary": {"4": {"status": "TAKEOFF"}}
                }
            },
            {
                "label": "Test 4: Idle Mode with Runway Roles & Live METAR Weather",
                "payload": {
                    "active_operations": [],
                    "tracked_count": 10,
                    "runway_summary": {},
                    "weather": {
                        "flight_category": "VFR",
                        "temp_f": 59,
                        "wind": "270@11kt",
                        "pressure": "30.06 inHg",
                        "condition": "Broken"
                    },
                    "runway_roles": {
                        "landing": "30R",
                        "departure": "30L",
                        "summary": "ARR 30R / DEP 30L",
                        "full_summary": "LANDING 30R / DEPARTURES 30L"
                    },
                    "runway_roles_summary": "ARR 30R / DEP 30L"
                }
            }
        ]

        for step in test_events:
            print(f" -> {step['label']}")
            await self.transmit_payload(step["payload"])
            await asyncio.sleep(8.0)

        print("[\033[92mSUCCESS\033[0m] Test pattern completed.")

    async def run(self, test_mode=False):
        """Main loop: manages connection, smart standby, OpenSky polling, and telemetry streaming."""
        print("==================================================")
        print("    KMSP RUNWAY TRACKER - OFFICE PC BRIDGE       ")
        print("==================================================")
        print(f"Transport Mode : {self.mode.upper()}")
        print(f"Poll Interval  : {self.poll_interval}s")
        print(f"Target Board   : {BLE_DEVICE_NAME}")
        if self.cached_ble_address:
            print(f"Cached MAC     : {self.cached_ble_address}")
        print("--------------------------------------------------")

        # Launch embedded web server in background
        self.start_web_server()

        first_connection = True

        while True:
            # 1. Connection management
            if not await self.ensure_connection():
                if not self.in_standby:
                    print("[\033[93mSTANDBY\033[0m] Board offline / powered off. Pausing OpenSky polling to conserve API quota.")
                    self.in_standby = True

                # Standby low-duty cycle: check every 10s without hammering the CPU or network
                await asyncio.sleep(10.0)
                continue

            # 2. Wake-up / Initial Connect Sequence
            if self.in_standby or first_connection:
                print("[\033[92mWAKE\033[0m] ESP32 board active! Resuming live tracking...")
                self.in_standby = False
                first_connection = False
                await self.sync_initial_state()

            if test_mode:
                await self.run_test_pattern()
                break

            # 3. Fetch & process live OpenSky telemetry
            try:
                raw_states = self.tracker.fetch_opensky()
                self.tracker.process_telemetry(raw_states, source_label="OpenSky (Office)")
                state_dict = self.tracker.get_state_dict()

                ops = state_dict.get("active_operations", [])
                tracked = state_dict.get("tracked_count", 0)
                roles = state_dict.get("runway_roles_summary", "Standby")
                weather = state_dict.get("weather", {})
                wx_str = f"{weather.get('flight_category', 'VFR')} {weather.get('temp_f', '')}F {weather.get('wind', '')}"
                now_str = time.strftime("%H:%M:%S")

                if not getattr(self.tracker, "last_fetch_success", True):
                    print(f"[{now_str}] ⚠️ Internet connection lost | Board in Standby | Roles: {roles} | Wx: {wx_str}")
                else:
                    print(f"[{now_str}] 📡 Airspace: {tracked} aircraft | Roles: {roles} | Wx: {wx_str} | Active Ops: {len(ops)}")
                    for op in ops:
                        print(f"  ✈ {op['action']} on RW {op['runway']} | {op.get('flight_label', op['callsign'])} ({op.get('aircraft_type', 'N/A')})")

                # 4. Transmit to ESP32
                success = await self.transmit_payload(state_dict)
                if not success:
                    print("  ✗ Transmission failed. Will re-verify link on next cycle.")

            except Exception as e:
                print(f"[\033[91mPOLL ERROR\033[0m] {e}")

            await asyncio.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="KMSP Runway Tracker Office PC Bridge")
    parser.add_argument("--mode", choices=["ble", "serial"], default="ble",
                        help="Transport mode: 'ble' (default, Bluetooth Low Energy wireless) or 'serial' (via Header J2 UART programmer)")
    parser.add_argument("--port", type=str, default=None,
                        help="Serial COM port (e.g. COM3 or /dev/ttyUSB0) if using Header J2 programmer")
    parser.add_argument("--interval", type=float, default=OPENSKY_POLL_INTERVAL,
                        help=f"OpenSky polling interval in seconds (default: {OPENSKY_POLL_INTERVAL})")
    parser.add_argument("--credentials", type=str, default=None,
                        help="Path to OpenSky credentials JSON file (default: auto-detects credentials_office.json)")
    parser.add_argument("--test", action="store_true",
                        help="Run test animation pattern to verify board link")
    default_port = int(os.environ.get("OFFICE_WEB_PORT", 18080))
    parser.add_argument("--web-port", type=int, default=default_port,
                        help=f"Embedded web server dashboard port (default: {default_port})")
    parser.add_argument("--no-web", action="store_true",
                        help="Disable the embedded web dashboard")

    args = parser.parse_args()

    bridge = OfficeBridge(
        mode=args.mode,
        port=args.port,
        poll_interval=args.interval,
        web_server=not args.no_web,
        web_port=args.web_port,
        credentials=args.credentials
    )

    try:
        asyncio.run(bridge.run(test_mode=args.test))
    except KeyboardInterrupt:
        print("\n[Office Bridge] Stopped by user.")
    finally:
        bridge.cleanup()


if __name__ == "__main__":
    main()
