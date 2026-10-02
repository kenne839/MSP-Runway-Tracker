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
1. Scan for and connect to `KMSP-Runway-Office`.
2. Poll OpenSky Network for MSP terminal arrivals/departures every 15 seconds.
3. Transmit active runway operations to the board over BLE.

### Options:
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
