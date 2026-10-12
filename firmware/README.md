# MSP Runway LED Tracker - ESP32-S3 Firmware

This firmware runs on custom **ESP32-S3-WROOM-1-N16R8** hardware (16MB Flash, 8MB Octal PSRAM) to animate 84 WS2812B addressable LEDs depicting real-time KMSP runway activity and display rich flight telemetry on a UCTRONICS 0.96" dual-color (Yellow/Blue) SSD1306 OLED screen.

The firmware supports two distinct deployment environments:
1. **Board 1 (Home Setup - Wi-Fi Mode `env:home_wifi`):**
   Connects to local Wi-Fi and polls telemetry via HTTP REST (`/api/runway_state`) from a dedicated headless **Raspberry Pi 3 Model A+ (512MB RAM)** running the `rpi.main` daemon.
2. **Board 2 (Office Setup - BLE Mode `env:office_ble`):**
   Operates 100% wirelessly over Bluetooth Low Energy (BLE 5.0 Nordic UART Service), receiving real-time telemetry pushed directly from an office PC running `office/bridge.py` without requiring corporate Wi-Fi access or tethering cables.

---

## Hardware Pinout (Verified against Schematic & Netlist)

| Subsystem | Signal Name | ESP32-S3 Pin | Hardware Component |
| :--- | :--- | :--- | :--- |
| **LED Strip Data** | `ESP32_DATA` | **`GPIO4`** (`U1.4`) | Inputs to `U87` (`SN74LVC1T45DBVR` level shifter), through 330Ω `R5` to `U2.3` (WS2812B DIN). |
| **OLED SDA** | `OLED_SDA` | **`GPIO1`** (`U1.38`) | Header `J3.1` (Pulled up to 3.3V via 10kΩ `R7`). |
| **OLED SCL** | `OLED_SCL` | **`GPIO2`** (`U1.39`) | Header `J3.2` (Pulled up to 3.3V via 10kΩ `R6`). |
| **User Button** | `NetR3_1` | **`GPIO0`** (`U1.27`) | `SW1` (Pulled up to 3.3V via 10kΩ `R3`, active LOW). |
| **Hardware Reset** | `NetC10_1` | **`EN`** (`U1.3`) | `SW2` (10kΩ pullup `R4`, 1µF `C10`, 0.1µF `C11`). |
| **UART Flash/Log** | `NetJ2_2 / NetJ2_3`| **`GPIO43 / GPIO44`** | Header `J2` (`TXD0` / `RXD0`). |
| **Power** | `5V / 3V3` | - | `5V` from USB-C `J1`. `3V3` from AMS1117-3.3 `U86`. |

---

## Runway LED Allocation (84 Addressable LEDs: U2 - U85)

The 84 WS2812B LEDs are daisy-chained (`U2` &rarr; `U85`). The hardware layout is mapped as follows:

| Runway | Components | Strip Indices | LED Count | Crossing Point |
| :--- | :--- | :--- | :--- | :--- |
| **Runway 12R – 30L** | `U2` – `U27` | `0` – `25` | 26 LEDs | `U12` (idx `10`) crosses RW 22/4 |
| **Runway 30R – 12L** | `U28` – `U47` | `26` – `45` | 20 LEDs | `U41` (idx `39`) crosses RW 22/4 |
| **Runway 22 – 4** | `U48` – `U65` | `46` – `63` | 18 LEDs | Crosses parallel runways |
| **Runway 35 – 17** | `U66` – `U85` | `64` – `83` | 20 LEDs | Independent N-S runway |

### Comet Animation Behavior
* **Landings (Touchdown Rollout):** The comet head initiates at the physical touchdown threshold of the active runway (e.g. `12R` starts at `U2` moving forward, `30L` starts at `U27` moving in reverse). The comet features a crisp white touchdown strobe followed by a 6-LED fading emerald green / cyan glide rollout tail.
* **Takeoffs (Takeoff Acceleration Roll):** The comet initiates where the aircraft starts its takeoff roll. It accelerates down the runway toward the departure end with a high-energy warm white head and an afterburner amber / gold / orange flame tail.
* **Simultaneous Operations:** Each runway zone tracks independent animation state machines, allowing parallel approaches or simultaneous crosswind departures without visual interference.

---

## OLED Display Geometry (UCTRONICS 0.96" Dual-Color SSD1306)

The firmware is custom-tailored for the **UCTRONICS 0.96" 128x64 Dual-Color Yellow/Blue OLED** (`SDA: GPIO1`, `SCL: GPIO2`):

### Active Flight Operation Mode:
* **Rows 0 – 15 (Yellow Zone):** Active runway designation (`RWY 30L`), operation badge (`LANDING` / `TAKEOFF`), and multi-flight cycle indicator (`[1/2]`).
* **Rows 16 – 63 (Blue Zone):** Flight telemetry details:
  - **Row 16:** Callsign & Airline (e.g., `Delta Air Lines 793` or `SkyWest 3822`)
  - **Row 28:** Airframe Model (e.g., `Boeing 737-900` or `Airbus A321neo`)
  - **Row 40:** Route / Origin / Destination (e.g., `From KDEN` or `To KORD`)
  - **Row 54:** Status footer & tracked airspace counter

### Idle Runway Mode (Live Surface Weather & Runway Roles):
When no aircraft are in the active touchdown or departure corridors:
* **Rows 0 – 15 (Yellow Zone):** Airport banner (`KMSP AIRPORT`) with live flight condition category (`[VFR]`, `[MVFR]`, `[IFR]`, or `[LIFR]`).
* **Rows 16 – 63 (Blue Zone):**
  - **Row 18:** Runway Roles based on recent movements (e.g. `ARR 30R / DEP 30L` or `LANDING: 30R`)
  - **Row 29:** Wind & Temperature (e.g. `Wind 270@11kt  59F`)
  - **Row 40:** Barometric Altimeter & Precipitation/Sky (e.g. `Baro 30.06" Broken` or `Baro 30.06" Rain`)
  - **Row 54:** Airspace traffic count & telemetry health

---

## Configuration Before Flashing

To keep your personal Wi-Fi credentials out of git commits, create a `firmware/src/secrets.h` file (which is git-ignored):

```cpp
#pragma once
#define WIFI_SSID       "YourWiFiNetwork"
#define WIFI_PASSWORD   "YourWiFiPassword"
#define PI_SERVER_URL   "http://192.168.1.150:8080/api/runway_state"
```

If `secrets.h` is not present, [`src/config.h`](src/config.h) falls back to default placeholder values.

---

## Building and Flashing with PlatformIO

### Prerequisites
* [VS Code](https://code.visualstudio.com/) with the **PlatformIO IDE** extension installed, or PlatformIO Core CLI (`pio`).

### Available Environments
* **`home_wifi`**: Production custom ESP32-S3 PCB (84 LEDs on GPIO4, dual-color OLED, Wi-Fi HTTP polling).
* **`devkit_wifi`**: ESP32-S3 DevKit test board (onboard WS2812 RGB on GPIO 48/38, dual-color OLED on GPIO 1/2, Wi-Fi HTTP polling).
* **`office_ble`**: Production custom PCB (84 LEDs, dual-color OLED, Pure BLE 5.0 Nordic UART Service).
* **`devkit_ble`**: ESP32-S3 DevKit test board (onboard WS2812 RGB, OLED, Pure BLE 5.0).

### 1. Build Firmware
```bash
cd firmware
pio run -e devkit_wifi
```

### 2. Upload to ESP32-S3
Connect your USB cable or UART programmer (Header `J2`):
```bash
# Upload for DevKit Wi-Fi test board:
pio run -e devkit_wifi -t upload --upload-port COM4

# Upload for Board 1 (Custom PCB Home Wi-Fi mode):
pio run -e home_wifi -t upload

# Upload for Board 2 (Custom PCB Office BLE mode):
pio run -e office_ble -t upload
```

---

## Remote Diagnostics & Wireless Telnet Console

In Wi-Fi modes (`home_wifi` and `devkit_wifi`), the ESP32 hosts a lightweight **Wireless Telnet Server** on port **23**:

```bash
# Connect wirelessly from any terminal on the local LAN:
telnet 192.168.0.214 23
# or using Netcat:
ncat 192.168.0.214 23
```

The console streams live telemetry, including:
- Wi-Fi RSSI signal strength & free heap memory.
- Rendering frame rate (~71.4 FPS).
- Real-time Theme changes & circadian brightness RAM adjustments.
- Runway comet transitions and arrival/departure classifications.

---

## Optical Anti-Stepping & Perceptual Gamma (Options B, C, D)

To eliminate discrete stepping jumps at low master brightness levels (such as night circadian floor $B=8$) and ensure physical 5050 RGB dies remain optically blended:
1. **16-Bit Temporal Error-Diffusion Dithering (Option B):** Accumulates fractional PWM remainders across frames at 71.4 Hz to simulate 16-bit dimming precision without forcing unlit dies to 1 LSB. Full PCB mode enables `FastLED.setDither(BINARY_DITHER)`.
2. **CIE 1931 Perceptual Lightness Table (Option C):** Maps linear PWM levels to human logarithmic perception ($L \approx Y^{2.8}$) via a 256-byte PROGMEM lookup table. Eliminates flat-bottomed breathing and comet tail "cliffing" into blackness.
3. **71.4 FPS Engine with Delta-Time Physics (Option D):** Throttles animation updates to $14\text{ ms}$ intervals with $\Delta t$ scaling, guaranteeing physically constant 4.5-second comet rolls regardless of network polling delays or I2C OLED refreshes.

---

## Dynamic Themes & Circadian Flash Endurance

The firmware features an integrated [`theme_manager`](src/theme_manager.h):
* **Dirty-Checking Version Gate:** Incoming telemetry only triggers parsing if `theme.version` has advanced, eliminating bus lag.
* **Persistent Preferences in NVS Flash:** User color selections and manual brightness levels are preserved across reboots in ESP32 Preferences flash memory.
* **Circadian Flash Protection:** When Solar-Sync is active, continuous smooth brightness adjustments sent in-band are applied directly to `FastLED.setBrightness()` in **volatile RAM only**, preserving the 100,000-cycle NVS flash memory endurance indefinitely.

---

## Thermal and Power Safety

84 WS2812B LEDs at full white can draw up to $4.2\text{A}$, which exceeds standard USB port capabilities and would create significant thermal load. 

This firmware includes three safeguards:
1. **Brightness Clamping:** `HARDWARE_MAX_SAFE_BRIGHTNESS` is capped at `48` (out of 255), keeping the maximum full-load current below $0.8\text{A}$ and preventing optical glare.
2. **FastLED Power Limiter:** `FastLED.setMaxPowerInVoltsAndMilliamps(5, 1200)` enforces a hard $1.2\text{A}$ software envelope.
3. **Selective Animation:** At any given time, only active runways are illuminated with approach chases or rollout animations, while idle runways show subtle threshold indicators. Typical power consumption is $< 250\text{mA}$.
