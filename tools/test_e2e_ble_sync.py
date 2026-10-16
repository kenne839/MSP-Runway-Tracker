import serial
import time
import threading
import urllib.request
import json
import asyncio
from bleak import BleakScanner, BleakClient

stop_event = threading.Event()
received_ack = []

def serial_reader():
    try:
        s = serial.Serial('COM4', 115200, timeout=0.2)
        while not stop_event.is_set():
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"[COM4 UART] {line}")
        s.close()
    except Exception as e:
        print(f"[UART ERR] {e}")

t = threading.Thread(target=serial_reader, daemon=True)
t.start()
time.sleep(1)

print("[HTTP] Fetching batch from Pi (192.168.0.213:8080)...")
raw = urllib.request.urlopen('http://192.168.0.213:8080/api/v1/office_replay_batch', timeout=5).read().decode('utf-8')
data_dict = json.loads(raw)
minified = json.dumps(data_dict, separators=(',', ':'))
print(f"[HTTP] Fetched {len(raw)} bytes (minified: {len(minified)} bytes, {data_dict.get('operations_count')} ops)")

async def run_ble():
    device = await BleakScanner.find_device_by_name('KMSP-Runway-Office', timeout=6.0)
    if not device:
        print("[BLE] ERROR: KMSP-Runway-Office not found!")
        return
    print(f"[BLE] Found {device.name} ({device.address}). Connecting...")
    async with BleakClient(device) as client:
        print("[BLE] Connected! Subscribing to NUS TX notifications...")
        def on_tx_notify(sender, data):
            msg = data.decode('utf-8', errors='ignore')
            print(f"\n[BLE << APPLICATION ACK RECEIVED] {msg.strip()}\n")
            received_ack.append(msg)

        await client.start_notify('6E400003-B5A3-F393-E0A9-E50E24DCCA9E', on_tx_notify)

        payload_bytes = (minified.strip() + '\n').encode('utf-8')
        print(f"[BLE >> STREAM] Sending {len(payload_bytes)} bytes over NUS RX...")
        for i in range(0, len(payload_bytes), 240):
            chunk = payload_bytes[i:i+240]
            await client.write_gatt_char('6E400002-B5A3-F393-E0A9-E50E24DCCA9E', chunk, response=True)
            print(f"  -> Sent {len(chunk)} bytes (offset {i}/{len(payload_bytes)})")
        
        print("[BLE >> STREAM] Completed all writes! Awaiting Application ACK notification...")
        for _ in range(50):
            if received_ack:
                break
            await asyncio.sleep(0.1)

        print("[BLE] Holding connection 5s to observe scheduled flight firing...")
        await asyncio.sleep(5)

asyncio.run(run_ble())
time.sleep(2)
stop_event.set()
t.join(timeout=2)
print(f"\nTest completed! Total ACKs received: {len(received_ack)}")

