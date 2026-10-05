#pragma once

#include <Arduino.h>

// =============================================================================
// HARDWARE PIN DEFINITIONS (Verified against Schematic & Netlist)
// =============================================================================

// I2C OLED Display (Connect OLED SDA to GPIO1, SCL to GPIO2)
#define PIN_OLED_SDA        1
#define PIN_OLED_SCL        2
#define OLED_I2C_ADDR       0x3C
#define SCREEN_WIDTH        128
#define SCREEN_HEIGHT       64

// Hardware Buttons & Programming Header J2
#define PIN_USER_BUTTON     0   // SW1 on GPIO0 (Active LOW boot button / user button)
// Header J2: 3-pin UART (Pin 1: GND, Pin 2: GPIO43 TXD0, Pin 3: GPIO44 RXD0)
#define PIN_UART_TX         43  // Header J2 TXD0
#define PIN_UART_RX         44  // Header J2 RXD0

// =============================================================================
// LED MATRIX & POWER MANAGEMENT
// =============================================================================

#if defined(BOARD_MODE_DEVKIT)
// DevKit Test Setup:
// Standard ESP32-S3 DevKitC-1 onboard addressable WS2812 RGB LED is on GPIO 48 (or GPIO 38 on v1.1)
#ifndef PIN_DEVKIT_RGB_LED
#define PIN_DEVKIT_RGB_LED  48
#endif
#define PIN_LED_DATA        PIN_DEVKIT_RGB_LED
#define NUM_LEDS            1
#define MAX_LED_BRIGHTNESS  40   // Comfortable desktop viewing brightness
#else
// Custom PCB Hardware:
// Level shifted 3.3V -> 5V via U87 SN74LVC1T45 driving 84 LEDs
#define PIN_LED_DATA        4
#define NUM_LEDS            84
// Current & Thermal safety limit: 64/255 limits draw to ~1.2A worst-case for 84 LEDs
#define MAX_LED_BRIGHTNESS  64  
#endif  

// =============================================================================
// PHYSICAL RUNWAY LED MAPPING (Verified with PCB Component Designators U2 - U85)
// =============================================================================
// Total: 84 LEDs (U2 through U85)
// Formula: LED Index = Component Number - 2

// 1. Runway 12R - 30L: U2 - U27 (26 LEDs)
//    U12 (index 10) is the runway crossing with RW 22 - RW 4
#define RW_12R_30L_START    0    // U2  (12R Threshold)
#define RW_12R_30L_END      25   // U27 (30L Threshold)
#define RW_12R_30L_CROSSING 10   // U12 (Crossing with RW 22/4)

// 2. Runway 30R - 12L: U28 - U47 (20 LEDs)
//    U41 (index 39) is the runway crossing with RW 22 - RW 4
#define RW_30R_12L_START    26   // U28 (30R Threshold)
#define RW_30R_12L_END      45   // U47 (12L Threshold)
#define RW_30R_12L_CROSSING 39   // U41 (Crossing with RW 22/4)

// 3. Runway 22 - 4: U48 - U65 (18 LEDs)
#define RW_22_4_START       46   // U48 (22 Threshold)
#define RW_22_4_END         63   // U65 (4 Threshold)

// 4. Runway 35 - 17: U66 - U85 (20 LEDs)
#define RW_35_17_START      64   // U66 (35 Threshold)
#define RW_35_17_END        83   // U85 (17 Threshold)

// =============================================================================
// NETWORK & TELEMETRY CONFIGURATION
// =============================================================================

// Wi-Fi Credentials
#define WIFI_SSID           "YOUR_WIFI_SSID"
#define WIFI_PASSWORD       "YOUR_WIFI_PASSWORD"

// Raspberry Pi REST API Endpoint
// Replace with your Raspberry Pi's local network IP address
#define PI_SERVER_URL       "http://192.168.1.150:8080/api/runway_state"

// Polling interval in milliseconds
#define TELEMETRY_POLL_MS   1500

// =============================================================================
// BLE (BLUETOOTH LOW ENERGY) CONFIGURATION (Office Board)
// =============================================================================

#define BLE_DEVICE_NAME         "KMSP-Runway-Office"
#define BLE_NUS_SERVICE_UUID    "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_NUS_RX_UUID         "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_NUS_TX_UUID         "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
