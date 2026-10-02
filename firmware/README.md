# MSP Runway LED Tracker - ESP32-S3 Firmware

This firmware runs on the custom **ESP32-S3-WROOM-1-N16R8** PCB. It connects to your local Wi-Fi network, polls the Raspberry Pi's REST endpoint (`/api/runway_state`), renders real-time approach/roll animations across the 84 WS2812B LEDs, and displays flight telemetry on the SSD1306 OLED screen.

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

## Runway LED Allocation (84 Addressable LEDs)

The 84 WS2812B LEDs are daisy-chained (`U2` &rarr; `U85`). By default, they are mapped as follows in [`src/config.h`](src/config.h):

* **Runway 12L / 30R:** 24 LEDs (`indices 0 to 23`)
* **Runway 12R / 30L:** 26 LEDs (`indices 24 to 49`)
* **Runway 4 / 22:** 20 LEDs (`indices 50 to 69`)
* **Runway 17 / 35:** 14 LEDs (`indices 70 to 83`)

> **Note:** If your physical PCB trace routing follows a different runway order, simply edit the `#define RW_*_START` and `_END` indices in [`src/config.h`](src/config.h).

---

## Configuration Before Flashing

Open [`src/config.h`](src/config.h) and update your Wi-Fi credentials and Raspberry Pi IP:

```cpp
#define WIFI_SSID       "YourWiFiNetwork"
#define WIFI_PASSWORD   "YourWiFiPassword"
#define PI_SERVER_URL   "http://192.168.1.150:8080/api/runway_state"
```

---

## Building and Flashing with PlatformIO

### Prerequisites
* [VS Code](https://code.visualstudio.com/) with the **PlatformIO IDE** extension installed, or PlatformIO Core CLI (`pio`).

### 1. Build Firmware
```bash
cd firmware
pio run
```

### 2. Upload to ESP32-S3
Connect your USB-to-UART programmer to header `J2` (or the USB-C port if using native USB CDC):
```bash
pio run --target upload
```

### 3. Open Serial Monitor (115200 Baud)
```bash
pio device monitor -b 115200
```

---

## Thermal and Power Safety

84 WS2812B LEDs at full white can draw up to $4.2\text{A}$, which exceeds standard USB port capabilities and would create significant thermal load. 

This firmware includes two safeguards:
1. **Brightness Clamping:** `MAX_LED_BRIGHTNESS` is set to `64` (out of 255), keeping the maximum full-load current below $1.2\text{A}$.
2. **Selective Animation:** At any given time, only active runways are illuminated with approach chases or rollout animations, while idle runways show subtle threshold indicators. Typical power consumption is $< 250\text{mA}$.
