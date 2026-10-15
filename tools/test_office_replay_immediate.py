"""
KMSP Runway Tracker - Fast Immediate Office Replay Test Harness
Fetches the live 30-minute replay batch from the Raspberry Pi, compresses the event
timeline so flights begin firing within 2 SECONDS (instead of waiting 7+ minutes),
and transmits to the ESP32-S3 over BLE or FTDI/Serial while monitoring live board logs.
"""

import sys
import time
import json
import gzip
import argparse
import threading
import urllib.request
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

PI_REPLAY_URL = "http://192.168.0.213:8080/api/v1/office_replay_batch"
BLE_DEVICE_NAME = "KMSP-Runway-Office"
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
DEFAULT_COM_PORT = "COM4"

stop_monitor = threading.Event()

def safe_print(msg):
    try:
        print(msg, flush=True)
    except Exception:
        clean = msg.encode("ascii", errors="replace").decode("ascii")
        print(clean, flush=True)

def serial_monitor_thread(port_name):
    """Monitors live ESP32 UART output from COM port without asserting DTR/RTS reset."""
    try:
        import serial
        ser = serial.Serial()
        ser.port = port_name
        ser.baudrate = 115200
        ser.dtr = False
        ser.rts = False
        ser.timeout = 0.2
        ser.open()
        safe_print(f"[SERIAL MON] Connected to {port_name} (115200 baud). Listening for live ESP32 events...")
        while not stop_monitor.is_set():
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line:
                if "[REPLAY]" in line or "Firing" in line or "[OLED]" in line or "Watchdog" in line or "Standby" in line:
                    safe_print(f"  >>> \033[92m{line}\033[0m")
                else:
                    safe_print(f"  >>> {line}")
        ser.close()
    except Exception as e:
        safe_print(f"[SERIAL MON EXCEPTION] {e}")

def fetch_batch_and_shift(url=PI_REPLAY_URL, token="", start_delay=2, pace_interval=12, preserve_spacing=False, use_demo=False):
    """
    Fetches the live batch and re-anchors offsets so operations start immediately.
    """
    if use_demo:
        print("[DEMO] Using generated 3-flight demo batch (12R Landing -> 12L Takeoff -> 12R Landing).")
        data = {
            "version": 2,
            "batch_id": int(time.time()),
            "weather": {
                "flight_category": "VFR",
                "temp_f": 52,
                "wind": "140@4kt",
                "pressure": "30.15 inHg",
                "condition": "Clear",
                "valid": True
            },
            "runway_roles": {"summary": "ARR 12L / DEP 12L", "landing": "12L", "departure": "12L"},
            "operations_count": 3
        }
        ops = []
    else:
        print(f"[HTTP] Fetching 30-minute batch from {url}...")
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "KMSP-FastTest/1.0"})
        if token:
            req.add_header("X-KMSP-Token", token)

        with urllib.request.urlopen(req, timeout=5) as resp:
            content_encoding = resp.headers.get("Content-Encoding", "")
            raw_bytes = resp.read()
            if "gzip" in content_encoding.lower():
                decompressed = gzip.decompress(raw_bytes)
            else:
                decompressed = raw_bytes
            data = json.loads(decompressed.decode("utf-8"))

        ops = data.get("operations", [])
        print(f"[HTTP] Successfully fetched {len(ops)} operations from Raspberry Pi.")

    if not ops or use_demo:
        if not use_demo:
            print("[WARN] No operations currently in window. Falling back to multi-flight demo batch.")
        ops = [
            {
                "id": "DAL756_12R",
                "offset_sec": 2,
                "runway": "12R",
                "action": "LANDING",
                "duration_ms": 22000,
                "callsign": "DAL756",
                "flight_label": "Delta 756",
                "aircraft_type": "Airbus A321",
                "route": "From SFO",
                "speed_kts": 134,
                "altitude_ft": 1100
            },
            {
                "id": "AAL2820_12L",
                "offset_sec": 14,
                "runway": "12L",
                "action": "TAKEOFF",
                "duration_ms": 18000,
                "callsign": "AAL2820",
                "flight_label": "American 2820",
                "aircraft_type": "Boeing 737 MAX 8",
                "route": "To PHX",
                "speed_kts": 150,
                "altitude_ft": 2200
            },
            {
                "id": "SCX296_12R",
                "offset_sec": 26,
                "runway": "12R",
                "action": "LANDING",
                "duration_ms": 22000,
                "callsign": "SCX296",
                "flight_label": "Sun Country 296",
                "aircraft_type": "Boeing 737-800",
                "route": "From PDX",
                "speed_kts": 130,
                "altitude_ft": 950
            }
        ]
        data["operations"] = ops
        data["operations_count"] = len(ops)
    else:
        # Re-anchor offsets so the earliest operation starts at `start_delay` seconds
        min_offset = min(op.get("offset_sec", 0) for op in ops)
        print(f"[TIMELINE] Original first flight was offset by {min_offset} seconds ({min_offset//60}m {min_offset%60}s).")
        
        for idx, op in enumerate(ops):
            if preserve_spacing:
                shifted = op.get("offset_sec", 0) - min_offset + start_delay
            else:
                # Fast demo pace: 1st flight at 2s, 2nd at 14s, 3rd at 26s, etc.
                shifted = start_delay + (idx * pace_interval)
            op["offset_sec"] = shifted
            print(f"  * Flight {idx+1}/{len(ops)}: {op.get('flight_label')} on {op.get('runway')} ({op.get('action')}) -> starts at T+{shifted}s")

    data["window_seconds"] = max(1800, (len(ops) + 1) * pace_interval)
    payload_str = json.dumps(data)
    print(f"[TIMELINE] Re-anchored batch payload: {len(payload_str)} bytes.")
    return payload_str, ops

async def transmit_ble(payload_str):
    try:
        from bleak import BleakScanner, BleakClient
    except ImportError:
        print("[ERROR] 'bleak' library is required for BLE. Run: pip install bleak")
        return False

    print(f"[BLE] Scanning for '{BLE_DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=6.0)
    if not device:
        print(f"[BLE] Could not find '{BLE_DEVICE_NAME}'. Ensure ESP32 is powered and running 'office_ble'.")
        return False

    print(f"[BLE] Found {device.name} [{device.address}]. Connecting...")
    async with BleakClient(device) as client:
        print(f"[BLE] Connected! Streaming payload over Nordic UART RX ({len(payload_str)} bytes)...")
        payload_bytes = (payload_str.strip() + "\n").encode("utf-8")
        chunk_size = 240
        for i in range(0, len(payload_bytes), chunk_size):
            chunk = payload_bytes[i:i + chunk_size]
            await client.write_gatt_char(NUS_RX_UUID, chunk, response=True)
            print(f"  -> Sent chunk {i//chunk_size + 1}: {len(chunk)} bytes (offset {i}/{len(payload_bytes)})")
        print("[BLE] Transfer complete! Disconnecting to simulate companion phone lifecycle.")
    return True

def transmit_serial(payload_str, port_name=DEFAULT_COM_PORT):
    import serial
    print(f"[SERIAL] Connecting to {port_name} at 115200 baud...")
    with serial.Serial(port_name, 115200, timeout=2.0) as ser:
        payload_bytes = (payload_str.strip() + "\n").encode("utf-8")
        ser.write(payload_bytes)
        ser.flush()
        print(f"[SERIAL] Pushed {len(payload_bytes)} bytes over UART to ESP32.")

def main():
    parser = argparse.ArgumentParser(description="KMSP Office Replay - Fast Immediate Test Harness")
    parser.add_argument("--transport", choices=["ble", "serial"], default="ble", help="Transport protocol (default: ble)")
    parser.add_argument("--port", default=DEFAULT_COM_PORT, help="COM port for Serial or live log monitoring (default: COM4)")
    parser.add_argument("--start-delay", type=int, default=2, help="Seconds before first flight begins (default: 2)")
    parser.add_argument("--interval", type=int, default=12, help="Seconds between subsequent flights (default: 12)")
    parser.add_argument("--demo", action="store_true", help="Transmit simulated 3-flight demo batch (12R landing -> 12L takeoff -> 12R landing)")
    parser.add_argument("--realtime", action="store_true", help="Preserve uncompressed flight spacing instead of fast demo pace")
    parser.add_argument("--monitor-sec", type=int, default=60, help="Seconds to monitor live replay output (default: 60)")
    args = parser.parse_args()

    payload_str, ops = fetch_batch_and_shift(
        start_delay=args.start_delay,
        pace_interval=args.interval,
        preserve_spacing=args.realtime,
        use_demo=args.demo
    )

    # Start background serial monitor if COM port is specified and using BLE
    mon_thread = None
    if args.transport == "ble":
        mon_thread = threading.Thread(target=serial_monitor_thread, args=(args.port,), daemon=True)
        mon_thread.start()
        time.sleep(0.5)

    if args.transport == "ble":
        success = asyncio.run(transmit_ble(payload_str))
        if not success:
            print("[ERROR] BLE transmission failed.")
            stop_monitor.set()
            return
    else:
        transmit_serial(payload_str, args.port)

    print(f"\n==================================================================")
    print(f" REPLAY IN PROGRESS: First event fires in ~{args.start_delay}s!")
    print(f" Watch your ESP32 Runway LEDs and OLED Display right now.")
    print(f" Monitoring live ESP32 serial logs for {args.monitor_sec}s (Ctrl+C to exit)...")
    print(f"==================================================================")

    # If serial transport was used, start monitor after writing
    if args.transport == "serial":
        mon_thread = threading.Thread(target=serial_monitor_thread, args=(args.port,), daemon=True)
        mon_thread.start()

    try:
        t_end = time.time() + args.monitor_sec
        while time.time() < t_end:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[USER] Monitoring interrupted.")
    finally:
        stop_monitor.set()
        print("[TEST] Done.")

if __name__ == "__main__":
    main()

