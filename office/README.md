# KMSP Runway LED Tracker - Office Board Setup

This folder contains the PC companion software for **Board 2 (Office Board)**, designed for office/enterprise environments where a Raspberry Pi cannot be connected to the corporate network.

```
[Office PC]                                              [ESP32-S3 Board 2]
  │                                                              │
  ├── office/bridge.py                                           │
  │   - Polls OpenSky Network via Office Internet                │
  │   - Filters KMSP Terminal Airspace & Matches Runways         │
  │                                                              │
  └── Transmits live telemetry ──────────────────────────────────► FastLED (84 WS2812Bs)
      (via BLE or USB-C Serial)                                    SSD1306 OLED (128x64)
```

---

## 1. Flashing the Office Board (PlatformIO)

In PlatformIO, choose the `office_ble` environment:

### In VS Code PlatformIO Extension:
1. Open the PlatformIO sidebar (alien icon).
2. Under **Project Tasks**, expand **`office_ble`**.
3. Click **Upload** to flash the board.

### Via Command Line:
```bash
cd firmware
pio run -e office_ble -t upload
```

> **Hardware Note:** Both environments are specifically tuned for the **ESP32-S3-WROOM-1-N16R8** with 16MB Flash and 8MB Octal PSRAM (`qio_opi`).

---

## 2. Running the Office PC Bridge

### Prerequisites
Install the required dependencies on your office PC:
```bash
pip install -r office/requirements.txt
```
*(Only requires `bleak`, `requests`, and `pyserial`).*

### Normal Live Polling Mode
To scan for the board over Bluetooth Low Energy (or USB) and stream live flights:
```bash
python office/bridge.py
```
Or as a module:
```bash
python -m office.bridge
```

### Options:
- **Auto Mode (Default):** Tries Bluetooth Low Energy first; falls back to USB Serial if plugged in:
  ```bash
  python office/bridge.py --mode auto
  ```
- **Force Bluetooth Low Energy (BLE):**
  ```bash
  python office/bridge.py --mode ble
  ```
- **Force USB Serial (over USB-C cable):**
  ```bash
  python office/bridge.py --mode serial --port COM3
  ```
- **Test Pattern (Instant hardware verification):**
  To verify LED comets, OLED cycling, and yellow strobing without waiting for live flights:
  ```bash
  python office/bridge.py --test
  ```
