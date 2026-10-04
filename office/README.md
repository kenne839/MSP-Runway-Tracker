# KMSP Runway LED Tracker - Office Board Setup

This folder contains the PC companion software for **Board 2 (Office Board)**, designed for office and corporate environments where an external Raspberry Pi cannot be connected to the office network.

```
[Office PC]                                              [ESP32-S3 Office Board]
  │                                                      (Powered by 5V supply)
  ├── office/bridge.py                                           │
  │   - Polls OpenSky Network via Office Internet                │
  │   - Filters KMSP Terminal Airspace & Matches Runways         │
  │                                                              │
  └── Transmits live telemetry wirelessly ───────────────────────► FastLED (84 WS2812Bs)
      via BLE (Bluetooth Low Energy 5.0)                           SSD1306 OLED (128x64)
```

---

## 1. Hardware Architecture & Pinout

- **ESP32 Module:** ESP32-S3-WROOM-1-N16R8 (16MB Flash, 8MB Octal PSRAM)
- **Power:** 5V DC power supply on board
- **Wireless Link:** Built-in Bluetooth Low Energy (BLE 5.0) advertising as `KMSP-Runway-Office`
- **UART Header J2 (3-pin):**
  - **Pin 1:** GND
  - **Pin 2:** `GPIO43` (U0TXD)
  - **Pin 3:** `GPIO44` (U0RXD)
  *(Used with an external USB-to-UART programmer for flashing firmware and viewing serial logs).*

---

## 2. Flashing the Office Board (PlatformIO)

Connect your external USB-to-UART programmer to **Header J2**:
- Programmer TX &rarr; Board RX (`GPIO44` / J2-Pin 3)
- Programmer RX &rarr; Board TX (`GPIO43` / J2-Pin 2)
- Programmer GND &rarr; Board GND (J2-Pin 1)

Hold down **SW1 (GPIO0)** while tapping **SW2 (EN / Reset)** to enter ROM bootloader mode, then flash:

### In VS Code PlatformIO Extension:
1. Open the PlatformIO sidebar (alien icon).
2. Under **Project Tasks**, expand **`office_ble`**.
3. Click **Upload** to flash the board.

### Via Command Line:
```bash
cd firmware
pio run -e office_ble -t upload
```

> **Build Note:** `ARDUINO_USB_CDC_ON_BOOT=0` is configured so that `Serial` is directly routed to hardware UART0 on Header J2 (`GPIO43/GPIO44`), allowing proper programming and debug logging over the 3-pin header.

---

## 3. Running the Office PC Bridge

Once the board is flashed and powered, it runs completely **wirelessly** at your desk.

### Prerequisites
Install the required dependencies on your office PC:
```bash
pip install -r office/requirements.txt
```
*(Only requires `bleak` for BLE, `requests`, and `urllib3`).*

### Live Mode (Default: Bluetooth Low Energy)
To scan for the board wirelessly over Bluetooth Low Energy and stream live flights:
```bash
python office/bridge.py
```
Or as a module:
```bash
python -m office.bridge
```

The script will automatically:
1. Scan for and connect to `KMSP-Runway-Office` (with fast MAC caching for <1s reconnect).
2. Start an embedded background web dashboard at `http://localhost:8080/`.
3. Transmit a Morning Sync packet immediately (current KMSP METAR weather and D-ATIS runway configuration).
4. Poll OpenSky Network for MSP terminal arrivals/departures every 15 seconds.
5. Transmit active runway operations to the board over BLE.

---

## 4. Turnkey Office Automation (Windows Startup & Standby)

In an office environment where you arrive in the morning and turn off your gear in the evening, the system is designed to be **100% zero-touch**:

### A. Automatic Windows Startup (Silent Background Daemon)
To have the bridge and web server launch automatically when you log into Windows:
- Double-click **`office/install_startup.bat`** (or run `powershell -File office/install_startup.ps1`).
- This adds a shortcut in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` pointing to `office/run_silent.vbs`.
- Whenever your laptop powers on or you log in, the bridge starts silently in the background with `pythonw.exe` (no console window clutter).
- To stop the background bridge at any time: double-click **`office/stop_bridge.bat`**.
- To disable auto-startup: double-click **`office/uninstall_startup.bat`**.

### B. Zero-Touch Auto-Pairing (Fast Reconnect)
- BLE GATT Nordic UART does **not** require manual Windows Settings PIN pairing.
- The bridge caches the ESP32's Bluetooth MAC address in `office/.ble_cache.json`.
- When you flip on the ESP32's power switch in the morning, the bridge connects in **<1 second** via direct address lookup.

### C. Smart Standby Mode (Evening Shutdown)
- When you turn off the ESP32 power switch at the end of the day:
  - The bridge detects the BLE disconnection and enters **Low-Power Standby Mode**.
  - **OpenSky queries are suspended**, saving your OpenSky rate limits and preventing office network traffic overnight.
  - The bridge enters a low-frequency 10-second check waiting for the board, using near-zero CPU.
  - **ESP32 Power Safety:** The ESP32 firmware resides in read-only flash memory with no operating system or SD card, so flipping off the 5V power switch is 100% safe and will never corrupt anything.

### D. Morning Sync (Instant Weather & Runway Roles)
- When you flip on the ESP32 the next morning:
  - The bridge detects the board and reconnects immediately.
  - It pushes a **Morning Sync Packet** with fresh KMSP METAR weather and D-ATIS runway roles.
  - The OLED transitions from the boot screen directly to live weather in **~1 second**, without waiting for the first OpenSky polling cycle.

---

## 5. Embedded Web Dashboard & Simulator

While the ESP32 runs on your desk, you can also view the tracker on your laptop:
- **Web Dashboard:** `http://localhost:8080/` (live flight table, weather, active runways)
- **Web Simulator:**  `http://localhost:8080/simulator` (interactive 2D runway layout & LED comet preview)

*(To disable the embedded web server, pass `--no-web` to `bridge.py`).*

---

## 6. Options & Troubleshooting

- **Test Pattern (Instant hardware verification):**
  To verify LED comets, OLED cycling, and yellow strobing without waiting for live flights:
  ```bash
  python office/bridge.py --test
  ```
- **Optional Serial Mode:**
  If you have your USB-to-UART programmer connected to Header J2 on your desk and want to stream telemetry over the wire:
  ```bash
  python office/bridge.py --mode serial --port COM3
  ```
