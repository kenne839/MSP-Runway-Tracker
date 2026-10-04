"""
KMSP Runway LED Tracker - Office PC Bridge
Transmits live KMSP flight telemetry to the ESP32-S3 Office Board wirelessly over BLE
(Bluetooth Low Energy).

Usage:
    python office/bridge.py          # Auto-connects via BLE
    python office/bridge.py --test   # Runs hardware test sequence via BLE
    python office/bridge.py --mode serial --port COM3  # Optional: via Header J2 UART programmer
"""

import os
import sys
import time
import json
import asyncio
import argparse

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpi.config import MSP_BBOX, OPENSKY_URL, OPENSKY_USERNAME, OPENSKY_PASSWORD
from rpi.tracker import TelemetryTracker

# BLE Nordic UART Service UUIDs
BLE_DEVICE_NAME = "KMSP-Runway-Office"
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"


class OfficeBridge:
    def __init__(self, mode="ble", port=None, poll_interval=15.0):
        self.mode = mode
        self.port = port
        self.poll_interval = poll_interval
        self.tracker = TelemetryTracker()
        self.ble_client = None
        self.serial_conn = None
        self.connected = False

    async def connect_ble(self):
        """Scans for and connects to the ESP32-S3 over Bluetooth Low Energy."""
        try:
            from bleak import BleakScanner, BleakClient
        except ImportError:
            print("[\033[91mERROR\033[0m] Python 'bleak' library not installed.")
            print("Please run: pip install bleak")
            return False

        print(f"[\033[94mBLE\033[0m] Scanning for '{BLE_DEVICE_NAME}'...")
        device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)

        if not device:
            # Fallback scan by service UUID
            devices = await BleakScanner.discover(timeout=5.0)
            for d in devices:
                if d.name and BLE_DEVICE_NAME.lower() in d.name.lower():
                    device = d
                    break

        if not device:
            print(f"[\033[93mWARNING\033[0m] Device '{BLE_DEVICE_NAME}' not found.")
            return False

        print(f"[\033[92mBLE\033[0m] Found board at {device.address}. Connecting...")
        client = BleakClient(device)
        try:
            await client.connect()
            self.ble_client = client
            self.connected = True
            print(f"[\033[92mSUCCESS\033[0m] Connected to {BLE_DEVICE_NAME} via BLE!")
            return True
        except Exception as e:
            print(f"[\033[91mERROR\033[0m] BLE connection failed: {e}")
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
                # Look for ESP32-S3 USB CDC or Silicon Labs / CH340 / USB Serial
                desc = (p.description or "").lower()
                if "esp32" in desc or "usb jtag" in desc or "usb serial" in desc or "cdc" in desc:
                    target_port = p.device
                    break
            if not target_port and len(ports) > 0:
                target_port = ports[0].device

        if not target_port:
            print("[\033[93mWARNING\033[0m] No USB Serial port found.")
            return False

        print(f"[\033[94mSERIAL\033[0m] Connecting to {target_port} at 115200 baud...")
        try:
            self.serial_conn = serial.Serial(target_port, 115200, timeout=1.0)
            self.connected = True
            print(f"[\033[92mSUCCESS\033[0m] Connected to {target_port} via USB Serial!")
            return True
        except Exception as e:
            print(f"[\033[91mERROR\033[0m] Serial connection failed: {e}")
            return False

    async def ensure_connection(self):
        """Ensures active communication link (auto-reconnecting if dropped)."""
        if self.mode == "ble" or self.mode == "auto":
            if self.ble_client and self.ble_client.is_connected:
                return True
            print("[\033[94mSTATUS\033[0m] Attempting BLE connection...")
            if await self.connect_ble():
                return True
            if self.mode == "ble":
                return False

        if self.mode == "serial" or self.mode == "auto":
            if self.serial_conn and self.serial_conn.is_open:
                return True
            print("[\033[94mSTATUS\033[0m] Attempting USB Serial connection...")
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
                # Send with MTU chunking if needed
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
                "label": "Test 3: Crosswind Rollout on RW 4/22 (Constant Speed Check)",
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
        """Main loop: connects, polls OpenSky, matches runways, and streams telemetry."""
        print("==================================================")
        print("    KMSP RUNWAY TRACKER - OFFICE PC BRIDGE       ")
        print("==================================================")
        print(f"Transport Mode : {self.mode.upper()}")
        print(f"Poll Interval  : {self.poll_interval}s")
        print(f"Target Board   : {BLE_DEVICE_NAME}")
        print("--------------------------------------------------")

        while True:
            if not await self.ensure_connection():
                print("[\033[93mRETRY\033[0m] Waiting 5 seconds before retrying connection...")
                await asyncio.sleep(5.0)
                continue

            if test_mode:
                await self.run_test_pattern()
                break

            # 1. Fetch & process live OpenSky telemetry
            try:
                raw_states = self.tracker.fetch_opensky()
                self.tracker.process_telemetry(raw_states, source_label="OpenSky (Office)")
                state_dict = self.tracker.get_state_dict()

                # Print console summary
                ops = state_dict.get("active_operations", [])
                tracked = state_dict.get("tracked_count", 0)
                roles = state_dict.get("runway_roles_summary", "Standby")
                weather = state_dict.get("weather", {})
                wx_str = f"{weather.get('flight_category', 'VFR')} {weather.get('temp_f', '')}F {weather.get('wind', '')}"
                now_str = time.strftime("%H:%M:%S")

                print(f"[{now_str}] 📡 Airspace: {tracked} aircraft | Roles: {roles} | Wx: {wx_str} | Active Ops: {len(ops)}")
                for op in ops:
                    print(f"  ✈ {op['action']} on RW {op['runway']} | {op.get('flight_label', op['callsign'])} ({op.get('aircraft_type', 'N/A')})")

                # 2. Transmit to ESP32
                success = await self.transmit_payload(state_dict)
                if success:
                    print(f"  ✓ Transmitted to ESP32 ({len(ops)} ops)")
                else:
                    print("  ✗ Transmission failed. Will reconnect on next cycle.")

            except Exception as e:
                print(f"[\033[91mPOLL ERROR\033[0m] {e}")

            await asyncio.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="KMSP Runway Tracker Office PC Bridge")
    parser.add_argument("--mode", choices=["ble", "serial"], default="ble",
                        help="Transport mode: 'ble' (default, Bluetooth Low Energy wireless) or 'serial' (via Header J2 UART programmer)")
    parser.add_argument("--port", type=str, default=None,
                        help="Serial COM port (e.g. COM3 or /dev/ttyUSB0) if using Header J2 programmer")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="OpenSky polling interval in seconds (default: 15.0)")
    parser.add_argument("--test", action="store_true",
                        help="Run test animation pattern to verify board link")

    args = parser.parse_args()

    bridge = OfficeBridge(mode=args.mode, port=args.port, poll_interval=args.interval)

    try:
        asyncio.run(bridge.run(test_mode=args.test))
    except KeyboardInterrupt:
        print("\n[Office Bridge] Stopped by user.")


if __name__ == "__main__":
    main()
