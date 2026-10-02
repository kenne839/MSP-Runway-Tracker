#pragma once

#include <Arduino.h>

// =============================================================================
// HARDWARE PIN DEFINITIONS (Verified against Schematic & Netlist)
// =============================================================================

// WS2812B Addressable LED Data Line (Level shifted 3.3V -> 5V via U87 SN74LVC1T45)
#define PIN_LED_DATA        4

// I2C OLED Display Header J3
#define PIN_OLED_SDA        1
#define PIN_OLED_SCL        2
#define OLED_I2C_ADDR       0x3C
#define SCREEN_WIDTH        128
#define SCREEN_HEIGHT       64

// Hardware Buttons
#define PIN_USER_BUTTON     0   // SW1 on GPIO0 (Active LOW, pulled up to 3V3)

// =============================================================================
// LED MATRIX & POWER MANAGEMENT
// =============================================================================

#define NUM_LEDS            84

// Current & Thermal safety limit: 64/255 limits draw to ~1.2A worst-case for 84 LEDs
#define MAX_LED_BRIGHTNESS  64  

// Runway LED Index Ranges (Total: 84 LEDs, U2 to U85 in daisy-chain)
// Adjust these index ranges if your physical PCB trace routing differs:
struct RunwayLedRange {
    uint8_t start_idx;
    uint8_t end_idx;
    bool reverse_for_high_rw; // True if landing on high number runway moves end -> start
};

// Runway 12L / 30R: 24 LEDs (indices 0 to 23)
#define RW_12L_30R_START    0
#define RW_12L_30R_END      23

// Runway 12R / 30L: 26 LEDs (indices 24 to 49)
#define RW_12R_30L_START    24
#define RW_12R_30L_END      49

// Runway 4 / 22: 20 LEDs (indices 50 to 69)
#define RW_4_22_START       50
#define RW_4_22_END         69

// Runway 17 / 35: 14 LEDs (indices 70 to 83)
#define RW_17_35_START      70
#define RW_17_35_END        83

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
