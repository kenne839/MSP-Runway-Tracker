# MSP Runway Tracker — Dynamic LED Configuration Architecture
## System Design for On-the-Fly Brightness & Color Palettes across Wi-Fi and BLE

---

### Executive Summary

Currently, LED animation parameters (colors, comets, brightness) are defined as compile-time constants in `firmware/src/config.h`. To enable modifying brightness, landing comets, takeoff flames, and runway threshold colors dynamically without re-flashing the firmware, this document establishes a unified, cross-transport architectural specification that works identically over **Wi-Fi** (Home deployment) and **Bluetooth Low Energy** (Office deployment).

---

## 1. Design Requirements & Constraints

1. **Dual-Transport Agnostic:** The configuration protocol must function identically over HTTP REST (polled from Raspberry Pi) and BLE Nordic UART (streamed from Office PC).
2. **Zero Polling Overhead:** Avoid redundant parsing overhead on the ESP32's dual-core Xtensa processor while maintaining 30+ FPS FastLED animations.
3. **Hardware & Thermal Safety:** Strict physical current clamping to prevent exceeding USB power budgets or overheating 84 WS2812B LEDs.
4. **Offline / Cold Boot Persistence:** Custom color and brightness choices must persist across power cycles using ESP32 Non-Volatile Storage (NVS).

---

## 2. Core Data Structures

### A. Memory Representation (`LedThemeConfig`)
A compact Plain-Old-Data (POD) struct stored in ESP32 RAM (~26 bytes):

```cpp
struct LedThemeConfig {
    uint8_t  master_brightness;    // 0–255 (User preference, clamped by hardware safety)
    
    // 24-bit RGB values (0xRRGGBB)
    uint32_t landing_head_rgb;     // Touchdown strobe color (Default: 0xFFFFFF)
    uint32_t landing_tail_rgb;     // Rollout glidepath color (Default: 0x00FF88 Emerald)
    
    uint32_t takeoff_head_rgb;     // Takeoff roll head color (Default: 0xFFE0A0 Warm White)
    uint32_t takeoff_tail_rgb;     // Afterburner flame color (Default: 0xFF4500 Orange/Amber)
    
    uint32_t idle_threshold_rgb;   // Inactive runway threshold markers (Default: 0x002200)
    uint32_t conflict_warn_rgb;    // Crossing conflict alert (Default: 0xFF0000 Flashing Red)
    
    uint16_t version;              // Monotonically increasing version counter
};
```

### B. Serialization Wire Format (JSON)
The configuration is serialized as an optional `theme` object embedded within the existing JSON telemetry stream:

```json
{
  "timestamp": 1727845600,
  "status": "ACTIVE",
  "theme": {
    "version": 4,
    "brightness": 28,
    "landing_head": "0xFFFFFF",
    "landing_tail": "0x00FF88",
    "takeoff_head": "0xFFE0A0",
    "takeoff_tail": "0xFF4500",
    "idle_threshold": "0x002200"
  }
}
```

---

## 3. Unified In-Band Transport Architecture

Rather than opening separate HTTP listening ports or custom BLE GATT services on the ESP32, configuration updates are delivered **in-band** inside the existing telemetry stream:

```text
       ┌────────────────────────┐         ┌────────────────────────┐
       │   Pi Web Dashboard     │         │   Office PC Bridge     │
       │  (Web UI / Mobile App) │         │   (CLI Flags / GUI)    │
       └───────────┬────────────┘         └───────────┬────────────┘
                   │                                  │
                   ▼                                  ▼
      HTTP REST: /api/runway_state           BLE Nordic UART Stream
                   │                                  │
                   └─────────────────┬────────────────┘
                                     │ (JSON with 'theme' block)
                                     ▼
                      ┌─────────────────────────────┐
                      │  Unified Payload Deserializer│
                      └──────────────┬──────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         ▼ (version == cached)                                   ▼ (version > cached)
  [ Skip Theme Parse ]                               [ Apply & Sanitize Theme ]
  Maintain 30+ FPS Loop                                          │
                                                     ┌───────────┴───────────┐
                                                     ▼                       ▼
                                             [ Update FastLED ]     [ Save to NVS Flash ]
```

### Efficiency & Change Detection:
1. When a telemetry packet arrives via Wi-Fi or BLE, the ESP32 checks:
   `doc["theme"]["version"] == active_theme.version`.
2. If the version matches, the theme block is skipped with near-zero CPU cycles.
3. If the version is higher, the new values are sanitized, applied to the FastLED animation engine, and committed to NVS.

---

## 4. Hardware Safety & Electrical Protection

Dynamic user settings introduce the risk of overcurrent if a user sets full 100% white on all 84 LEDs ($84 \times 50\text{mA} = 4.2\text{A}$). The firmware must enforce a dual-layer hardware safety guardrail:

### Layer 1: Brightness Clamping
```cpp
#define HARDWARE_MAX_BRIGHTNESS  48  // Absolute maximum safe ceiling (~0.8A)
uint8_t safe_brightness = constrain(incoming_brightness, 5, HARDWARE_MAX_BRIGHTNESS);
FastLED.setBrightness(safe_brightness);
```

### Layer 2: FastLED Dynamic Power Management
FastLED includes an integrated power manager that monitors total milliwatts:
```cpp
// Set maximum power consumption: 5 Volts, 1200 Milliamps (1.2A)
FastLED.setMaxPowerInVoltsAndMilliamps(5, 1200);
```
If multiple simultaneous approaches and takeoffs occur, FastLED automatically calculates the cumulative current draw and attenuates intensity to stay safely within the 1.2A budget.

---

---

## 5. Non-Volatile Storage (NVS) Lifecycle & Flash Endurance Protection

To ensure custom settings survive reboots while preserving 100,000-cycle flash memory lifespan:
1. **At Boot (`setup()`):**
   * The ESP32 opens the `Preferences` namespace `led_theme`.
   * If stored keys exist, it loads `master_brightness`, `manual_brightness`, `solar_sync`, colors, and `version`.
   * FastLED immediately renders with the saved configuration before Wi-Fi or BLE finishes connecting.
2. **On Runtime User Configuration Change:**
   * When user modifies colors or toggles Solar-Sync via the dashboard, `version` increments on the Pi.
   * The ESP32 detects `incoming_version > current_theme.version`, updates values, and commits to NVS flash (`prefs.put...`).
3. **During Autonomous Circadian Solar-Sync (Infinite Flash Lifespan):**
   * As the sun moves throughout the day, the daemon sends updated `effective_brightness` values in-band without bumping `version`.
   * The ESP32 detects `solar_sync: true` and applies `effective_brightness` directly to `FastLED.setBrightness()` and the WS2812 output in **volatile RAM only**.
   * Zero writes are made to NVS flash, giving the system an **infinite flash lifespan**.

---

## 6. Solar-Adaptive Circadian Transfer Function

The system features an autonomous astronomical circadian curve that tracks the live sun elevation $\alpha(t)$ at KMSP ($44.8819^\circ\text{ N}, -93.2217^\circ\text{ W}$):

### Mathematical Formulation
1. **NOAA Astronomical Ephemeris:** Computes true solar elevation angle $\alpha(t)$ using offline Jean Meeus celestial mechanics equations.
2. **Normalized Twilight Progress ($t$):**
   $$t = \text{clamp}\left(\frac{\alpha(t) - \alpha_{\text{twilight}}}{\alpha_{\text{daylight}} - \alpha_{\text{twilight}}}, 0.0, 1.0\right)$$
   where $\alpha_{\text{twilight}} = -6.0^\circ$ (Civil Twilight horizon threshold) and $\alpha_{\text{daylight}} = +25.0^\circ$ (Full daylight saturation angle).
3. **Cubic Hermite Smoothstep Function ($S(t)$):**
   $$S(t) = 3t^2 - 2t^3$$
   With continuous first derivatives ($S'(0) = 0$ and $S'(1) = 0$), this curve eliminates abrupt lighting shifts at dawn and dusk, mirroring human retinal adaptation.
4. **Active Brightness Output:**
   $$B(t) = \text{round}\left(B_{\text{night}} + (B_{\text{day}} - B_{\text{night}}) \cdot S(t)\right)$$
   Operating between safe bounds: Night Floor $B_{\text{night}} = 8$ and Day Ceiling $B_{\text{day}} = 40$.

---

## 7. Implementation Status

- [x] **ESP32 Firmware:**
  - Implemented `theme_manager.h` and `theme_manager.cpp`.
  - Added volatile RAM circadian updates and NVS flash persistence.
  - Full support across `network_client.cpp`, `ble_client.cpp`, and `runway_leds.cpp`.
- [x] **Raspberry Pi Daemon:**
  - Implemented `rpi/solar.py` with pure offline NOAA ephemeris.
  - Implemented `rpi/theme.py` with 5 presets, solar elevation, and smoothstep brightness.
  - Implemented `rpi/server.py` dashboard controls with ☀️ Solar-Sync toggle and live elevation badges.
  - Implemented `rpi/theme_cli.py` for terminal management over SSH.
- [x] **Office PC Bridge:**
  - Added `--theme` and `--brightness` CLI arguments to `office/bridge.py`.

