"""
KMSP Office Replay - Desktop BLE Test Harness
Fetches the live 30-minute replay batch from the Raspberry Pi and pushes it over BLE
to the ESP32-S3 running the 'office_ble' firmware target, verifying the replay engine.
"""

import sys
import time
import json
import gzip
import urllib.request
import asyncio

PI_REPLAY_URL = "http://192.168.0.213:8080/api/v1/office_replay_batch"
BLE_DEVICE_NAME = "KMSP-Runway-Office"
NUS_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

def fetch_live_batch(url=PI_REPLAY_URL, token=""):
    print(f"[HTTP] Fetching 30-minute batch from {url}...")
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "KMSP-TestHarness/1.0"})
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
        print(f"[HTTP] Successfully fetched {len(raw_bytes)} bytes (uncompressed: {len(decompressed)} bytes)")
        print(f"       Batch ID: {data.get('batch_id')} | Operations: {data.get('operations_count')}")
        return json.dumps(data)

async def transmit_ble(payload_str):
    try:
        from bleak import BleakScanner, BleakClient
    except ImportError:
        print("[ERROR] 'bleak' library is required. Run: pip install bleak")
        return False

    print(f"[BLE] Scanning for peripheral '{BLE_DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=5.0)
    if not device:
        print(f"[BLE] Could not find '{BLE_DEVICE_NAME}'. Ensure the ESP32 is powered on and running 'office_ble'.")
        return False

    print(f"[BLE] Connecting to {device.address}...")
    async with BleakClient(device) as client:
        print(f"[BLE] Connected! Streaming {len(payload_str)} bytes over NUS RX...")
        payload_bytes = (payload_str.strip() + "\n").encode("utf-8")
        chunk_size = 240
        for i in range(0, len(payload_bytes), chunk_size):
            chunk = payload_bytes[i:i + chunk_size]
            await client.write_gatt_char(NUS_RX_UUID, chunk, response=True)
            print(f"  -> Sent {len(chunk)} bytes (offset {i}/{len(payload_bytes)})")
        print("[BLE] Finished transmission! ESP32 replay engine is now playing back operations.")
        return True

def main():
    try:
        payload = fetch_live_batch()
    except Exception as e:
        print(f"[ERROR] Could not fetch batch: {e}")
        return

    try:
        asyncio.run(transmit_ble(payload))
    except Exception as e:
        print(f"[BLE ERROR] {e}")

if __name__ == "__main__":
    main()
