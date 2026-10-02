#include <Arduino.h>
#include "config.h"
#include "runway_leds.h"
#include "display_oled.h"
#include "network_client.h"

static DisplayFlightInfo currentFlight;
static uint32_t last_wifi_check = 0;
static uint32_t last_oled_refresh = 0;

void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("   KMSP RUNWAY LED TRACKER - ESP32-S3 FIRMWARE    "));
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

    // 4. Connect to Wi-Fi
    showBootScreen("Connecting Wi-Fi...");
    initNetwork();

    // Reset default flight info
    memset(&currentFlight, 0, sizeof(currentFlight));
    currentFlight.has_active_flight = false;
    currentFlight.tracked_count = 0;

    showBootScreen("Ready! Polling Pi...");
    delay(600);
}

void loop() {
    // 1. Continuous LED Animation Rendering (~30 FPS)
    renderRunwayAnimations();

    // 2. Poll Raspberry Pi API for new telemetry
    if (pollTelemetryData(currentFlight)) {
        updateDisplayFlight(currentFlight);
        last_oled_refresh = millis();
    }

    // 3. Periodic display refresh (every 3 seconds) for idle animations / clock updates
    if (millis() - last_oled_refresh > 3000) {
        updateDisplayFlight(currentFlight);
        last_oled_refresh = millis();
    }

    // 4. Background Wi-Fi health check (every 10 seconds)
    if (millis() - last_wifi_check > 10000) {
        last_wifi_check = millis();
        checkWifiConnection();
    }

    // 5. User button press detection (SW1 / GPIO0)
    if (digitalRead(PIN_USER_BUTTON) == LOW) {
        delay(50); // Debounce
        if (digitalRead(PIN_USER_BUTTON) == LOW) {
            Serial.println(F("[Button] User SW1 pressed. Forcing display refresh."));
            updateDisplayFlight(currentFlight);
            while (digitalRead(PIN_USER_BUTTON) == LOW) {
                delay(10);
            }
        }
    }
}
