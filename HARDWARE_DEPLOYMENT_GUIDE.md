# MSP Runway Tracker — Production Hardware Deployment Guide

This guide details the complete, step-by-step procedure for assembling, programming, testing, and deploying the custom **MSP Runway LED Tracker** production hardware when your PCB and components arrive.

---

## 1. System Architecture & Hardware Specs

### Production Board 1 (Home Setup)
* **Microcontroller:** ESP32-S3-WROOM-1-N16R8 (16MB Flash, 8MB Octal PSRAM)
* **LED Array:** 84 addressable WS2812B RGB LEDs (`U2` – `U85`) driven via `GPIO4` through `U87` (`SN74LVC1T45` 5V level shifter) and 330Ω `R5`
* **Display:** 0.96" Dual-Color Yellow/Blue I2C SSD1306 OLED via Header `J3` (`SDA`: `GPIO1`, `SCL`: `GPIO2`)
* **Power Supply:** 5V / 2.5A+ via USB-C `J1` (Stepped down to 3.3V via AMS1117-3.3 `U86`)
* **Programming Interface:** 3-pin UART Header `J2` (`GND`, `GPIO43/TXD0`, `GPIO44/RXD0`)
* **Telemetry Server:** Headless Raspberry Pi 3 Model A+ (512MB RAM) running `msp-tracker.service` at `http://192.168.0.213:8080/api/runway_state`

---

## 2. Pre-Power Physical Inspection

Before plugging in any power cable or programmer:

1. **Solder Bridge Inspection:**
   * Inspect the ESP32-S3 castellated pads with magnification. Ensure no bridges between adjacent pins, especially around `GPIO43`, `GPIO44`, `GPIO0`, `EN`, `3V3`, and `GND`.
   * Check the level shifter `U87` (`SN74LVC1T45DBVR`) and the AMS1117 3.3V regulator `U86`.
2. **OLED Pinout Verification (Header `J3`):**
   * Double-check your OLED display module pinout order against `J3`:
     * `J3.1`: `GPIO1` (SDA)
     * `J3.2`: `GPIO2` (SCL)
     * Power: `3V3` and `GND`
   * *Caution:* Some 0.96" SSD1306 modules have `VCC / GND / SCL / SDA`, while others have `GND / VCC / SCL / SDA`. Ensure your display module aligns with the PCB pin markings.
3. **Cold Resistance / Short Check:**
   * Measure resistance across the 5V power rails (`5V` to `GND`) using a multimeter. Ensure no short circuit exists before applying power.
   * Measure resistance across `3V3` and `GND`.

---

## 3. Firmware Configuration

Open [`firmware/src/config.h`](file:///c:/Users/Michael/Documents/Python/MSP%20RUNWAY%20TRACKER/firmware/src/config.h) and verify the production network settings:

```cpp
// Wi-Fi Credentials (2.4 GHz network)
#define WIFI_SSID           "CenturyLinkD8F1"
#define WIFI_PASSWORD       "a7b8edf85e3f2c"

// Raspberry Pi REST API Endpoint (Live Pi address)
#define PI_SERVER_URL       "http://192.168.0.213:8080/api/runway_state"
```

### Environment Selection: `home_wifi`
In [`firmware/platformio.ini`](file:///c:/Users/Michael/Documents/Python/MSP%20RUNWAY%20TRACKER/firmware/platformio.ini), the production build environment is **`home_wifi`**:
* **`BOARD_MODE_WIFI=1`**: Enables Wi-Fi REST polling from the Raspberry Pi.
* **`BOARD_MODE_DEVKIT=0` (Disabled)**: Reconfigures the firmware for all **84 LEDs** on `GPIO4` with safe current-limiting (`MAX_LED_BRIGHTNESS = 32`).

---

## 4. Wiring the TTL-to-USB Programmer to Header `J2`

Because the PCB uses dedicated UART pins for flashing rather than native USB-CDC, connect your external USB-to-TTL adapter to 3-pin Header **`J2`**:

| Header J2 Pin | PCB Signal | TTL-to-USB Adapter Pin | Notes |
| :--- | :--- | :--- | :--- |
| **Pin 1** | `GND` | **GND** | Common ground reference |
| **Pin 2** | `GPIO43` (`TXD0`) | **RXD** | ESP32 TX connects to Adapter RX |
| **Pin 3** | `GPIO44` (`RXD0`) | **TXD** | ESP32 RX connects to Adapter TX |

### Voltage & Power Rules:
1. **TTL Adapter Voltage:** Set your adapter's logic level jumper to **`3.3V`**.
2. **Board Power:** Plug a 5V USB-C cable into **`J1`** to power the board. **Do not** attempt to power 84 WS2812B LEDs from the 3.3V or 5V pin of a basic USB-to-TTL dongle.

---

## 5. Flashing the Production Firmware

1. Plug the 5V USB-C cable into the PCB (`J1`).
2. Plug the USB-to-TTL adapter into your PC.
3. Determine the COM port in Windows Device Manager (e.g. `COM3`).
4. **Enter Bootloader Mode:**
   * Press and hold **`SW1`** (BOOT / GPIO0).
   * While holding `SW1`, press and release **`SW2`** (RESET / EN).
   * Release **`SW1`**.
5. **Upload the Firmware via PowerShell:**
   ```powershell
   & "$HOME\.platformio\penv\Scripts\pio.exe" run -d firmware -e home_wifi -t upload --upload-port COM3
   ```
6. **Boot into Normal Operation:**
   * Once the terminal displays `[SUCCESS]`, press **`SW2`** (RESET) once to boot the firmware.

---

## 6. Verification and First Run

### 1. Serial Monitor Check
Open the serial console to monitor system startup:
```powershell
& "$HOME\.platformio\penv\Scripts\pio.exe" device monitor -d firmware -e home_wifi -b 115200 --port COM3
```

Expected log output:
```text
[Init] MSP Runway LED Tracker (Home Wi-Fi Mode)
[Init] OLED SSD1306 initialized (128x64)
[Init] FastLED initialized: 84 WS2812B LEDs on GPIO4 (Brightness: 32)
[WiFi] Connecting to CenturyLinkD8F1...
[WiFi] Connected! IP Address: 192.168.0.xxx
[HTTP] Polling telemetry: http://192.168.0.213:8080/api/runway_state
[HTTP] 200 OK - Active ops: 1
```

### 2. Display Verification
* **Idle Mode:** Yellow zone displays `KMSP AIRPORT [VFR]` with active runway roles (e.g. `ARR 30R / DEP 30L`), surface winds, and barometric altimeter.
* **Flight Event Mode:** Yellow zone displays active runway (`RWY 30R`) and operation badge (`LANDING` or `TAKEOFF`). Blue zone displays callsign, airline, airframe, and origin/destination route.

### 3. LED Animation Verification
* **Runway 12R–30L (`U2`–`U27`, 26 LEDs):** Landing comet starts at touchdown threshold; takeoff roll accelerates toward rollout end.
* **Runway 30R–12L (`U28`–`U47`, 20 LEDs):** Parallel approach/departure.
* **Runway 22–4 (`U48`–`U65`, 18 LEDs):** Crosswind runway comets cross the parallel centerlines correctly.
* **Runway 35–17 (`U66`–`U85`, 20 LEDs):** Independent North-South runway comets.

---

## 7. Troubleshooting Matrix

| Symptom | Probable Cause | Corrective Action |
| :--- | :--- | :--- |
| `A fatal error occurred: Failed to connect to ESP32-S3` | Not in download mode or TX/RX swapped | Swap TX and RX on Header `J2`. Hold `SW1`, tap `SW2`, release `SW1`, then retry upload. |
| `Reason: 201 - NO_AP_FOUND` | Connecting to 5 GHz network | Ensure SSID is the 2.4 GHz network (`CenturyLinkD8F1`, not `-5G`). |
| OLED screen remains blank | Wrong I2C pins or swapped SDA/SCL | Verify SDA is on `GPIO1` and SCL is on `GPIO2`. Check OLED VCC/GND polarity. |
| LEDs flicker or show random colors | Power supply droop or loose ground | Ensure USB-C power supply provides $\ge 2.0\text{A}$. Verify common ground between level shifter and LEDs. |
| Telemetry shows `DISCONNECTED` | Raspberry Pi offline or IP changed | Verify Raspberry Pi is powered on at `192.168.0.213` and `msp-tracker.service` is active. |

---

## 8. Permanent 24/7 Deployment

1. Unplug the USB-to-TTL programmer from Header `J2`.
2. Install the PCB and OLED into the enclosure/frame.
3. Power the unit continuously from a standard 5V USB-C wall adapter.
4. The system will automatically reconnect to Wi-Fi, poll the Raspberry Pi daemon, and run 24/7 with zero maintenance.

