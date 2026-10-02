#include <Arduino.h>
#include "config.h"
#include "runway_leds.h"
#include "display_oled.h"

#if defined(BOARD_MODE_BLE)
#include "ble_client.h"
#else
#include "network_client.h"
static uint32_t last_wifi_check = 0;
#endif

static DisplayTelemetryData telemetry_data;

void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println();
    Serial.println(F("=================================================="));
#if defined(BOARD_MODE_BLE)
    Serial.println(F("  KMSP RUNWAY TRACKER - ESP32-S3 (OFFICE BLE MODE)"));
#else
    Serial.println(F("   KMSP RUNWAY TRACKER - ESP32-S3 (HOME WIFI MODE)"));
#endif
    Serial.println(F("=================================================="));

    // 1. Initialize user button on SW1 (GPIO0)
    pinMode(PIN_USER_BUTTON, INPUT_PULLUP);

    // 2. Initialize OLED Display (I2C on GPIO1 / GPIO2)
    initDisplay();
    showBootScreen("Starting System...");
    delay(400);

    // 3. Initialize WS2812B LED array (84 LEDs on GPIO4)
    showBootScreen("Initializing LEDs...");
    initLeds();
    delay(400);

    // 4. Initialize Communications
#if defined(BOARD_MODE_BLE)
    showBootScreen("Starting BLE Link...");
    initBleClient();
    showBootScreen("BLE: KMSP-Runway-Office");
    delay(600);
#else
    showBootScreen("Connecting Wi-Fi...");
    initNetwork();
    showBootScreen("Ready! Polling Pi...");
    delay(600);
#endif

    memset(&telemetry_data, 0, sizeof(telemetry_data));
    telemetry_data.flight_count = 0;
    telemetry_data.tracked_count = 0;
    telemetry_data.has_had_event = false;
    telemetry_data.active_runways_summary[0] = '\0';
}

void loop() {
    // 1. Continuous LED Animation Rendering (~33 FPS, comet or idle yellow strobe)
    renderRunwayAnimations();

    // 2. Poll for new telemetry (BLE from Office PC or HTTP from Raspberry Pi)
#if defined(BOARD_MODE_BLE)
    if (pollBleTelemetry(telemetry_data)) {
        updateTelemetryData(telemetry_data);
    }
#else
    if (pollTelemetryData(telemetry_data)) {
        updateTelemetryData(telemetry_data);
    }

    // Background Wi-Fi health check (every 10 seconds)
    if (millis() - last_wifi_check > 10000) {
        last_wifi_check = millis();
        checkWifiConnection();
    }
#endif

    // 3. Render OLED display (handles 3-second multi-flight cycling and idle state)
    renderDisplayLoop();

    // 4. User button press detection (SW1 / GPIO0)
    if (digitalRead(PIN_USER_BUTTON) == LOW) {
        delay(50); // Debounce
        if (digitalRead(PIN_USER_BUTTON) == LOW) {
            Serial.println(F("[Button] User SW1 pressed. Refreshing display."));
            renderDisplayLoop();
            while (digitalRead(PIN_USER_BUTTON) == LOW) {
                delay(10);
            }
        }
    }
}
