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
#if defined(BOARD_MODE_DEVKIT)
    Serial.println(F("   KMSP RUNWAY TRACKER - ESP32-S3 (DEVKIT TEST)   "));
    Serial.println(F("   OLED: I2C on SDA=GPIO1, SCL=GPIO2              "));
    Serial.println(F("   LED:  Onboard WS2812 (Landing=Orange, Dep=Blue)"));
    Serial.println(F("   TIP:  Tap BOOT (GPIO0) to cycle Demo Flights!  "));
#elif defined(BOARD_MODE_BLE)
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
            wakeDisplay(); // Instantly wake OLED if asleep and reset 5-minute timer
#if defined(BOARD_MODE_DEVKIT)
            static uint8_t demo_cycle = 0;
            demo_cycle = (demo_cycle + 1) % 3;
            if (demo_cycle == 1) {
                // Demo 1: Landing on 30R (Orange LED + Delta 793 arriving from KDEN)
                Serial.println(F("[DevKit Demo] Step 1/3: Simulating LANDING on 30R (Orange LED + OLED)"));
                resetAllRunwaysToIdle();
                setRunwayState("30R", "LANDING", 0.5f);
                memset(&telemetry_data, 0, sizeof(telemetry_data));
                telemetry_data.flight_count = 1;
                telemetry_data.tracked_count = 1;
                telemetry_data.has_had_event = true;
                strncpy(telemetry_data.flights[0].runway, "30R", sizeof(telemetry_data.flights[0].runway));
                strncpy(telemetry_data.flights[0].action, "LANDING", sizeof(telemetry_data.flights[0].action));
                strncpy(telemetry_data.flights[0].flight_label, "DAL793", sizeof(telemetry_data.flights[0].flight_label));
                strncpy(telemetry_data.flights[0].aircraft_type, "A321", sizeof(telemetry_data.flights[0].aircraft_type));
                strncpy(telemetry_data.flights[0].route, "DEN->MSP", sizeof(telemetry_data.flights[0].route));
                strncpy(telemetry_data.runway_roles_summary, "ARR: 30R | DEP: 30L", sizeof(telemetry_data.runway_roles_summary));
                strncpy(telemetry_data.updated_time, "11:01:05PM", sizeof(telemetry_data.updated_time));
                updateTelemetryData(telemetry_data);
            } else if (demo_cycle == 2) {
                // Demo 2: Takeoff on 30L (Blue LED + SkyWest 3822 departing to KORD)
                Serial.println(F("[DevKit Demo] Step 2/3: Simulating TAKEOFF on 30L (Blue LED + OLED)"));
                resetAllRunwaysToIdle();
                setRunwayState("30L", "TAKEOFF", 0.5f);
                memset(&telemetry_data, 0, sizeof(telemetry_data));
                telemetry_data.flight_count = 1;
                telemetry_data.tracked_count = 1;
                telemetry_data.has_had_event = true;
                strncpy(telemetry_data.flights[0].runway, "30L", sizeof(telemetry_data.flights[0].runway));
                strncpy(telemetry_data.flights[0].action, "TAKEOFF", sizeof(telemetry_data.flights[0].action));
                strncpy(telemetry_data.flights[0].flight_label, "SKW3822", sizeof(telemetry_data.flights[0].flight_label));
                strncpy(telemetry_data.flights[0].aircraft_type, "E75L", sizeof(telemetry_data.flights[0].aircraft_type));
                strncpy(telemetry_data.flights[0].route, "MSP->ORD", sizeof(telemetry_data.flights[0].route));
                strncpy(telemetry_data.runway_roles_summary, "ARR: 30R | DEP: 30L", sizeof(telemetry_data.runway_roles_summary));
                strncpy(telemetry_data.updated_time, "11:01:05PM", sizeof(telemetry_data.updated_time));
                updateTelemetryData(telemetry_data);
            } else {
                // Demo 0: Idle state (Amber breathing LED + KMSP METAR)
                Serial.println(F("[DevKit Demo] Step 3/3: Simulating IDLE / METAR (Standby LED + OLED)"));
                resetAllRunwaysToIdle();
                memset(&telemetry_data, 0, sizeof(telemetry_data));
                telemetry_data.flight_count = 0;
                telemetry_data.tracked_count = 0;
                telemetry_data.has_had_event = true;
                telemetry_data.weather.valid = true;
                strncpy(telemetry_data.weather.flight_category, "VFR", sizeof(telemetry_data.weather.flight_category));
                telemetry_data.weather.temp_f = 68;
                strncpy(telemetry_data.weather.wind, "300@12kt", sizeof(telemetry_data.weather.wind));
                strncpy(telemetry_data.weather.pressure, "30.06 inHg", sizeof(telemetry_data.weather.pressure));
                strncpy(telemetry_data.weather.condition, "Scattered", sizeof(telemetry_data.weather.condition));
                strncpy(telemetry_data.runway_roles_summary, "ARR: 30R | DEP: 30L", sizeof(telemetry_data.runway_roles_summary));
                strncpy(telemetry_data.updated_time, "11:01:05PM", sizeof(telemetry_data.updated_time));
                updateTelemetryData(telemetry_data);
            }
#else
            Serial.println(F("[Button] User SW1 pressed. Refreshing display."));
            renderDisplayLoop();
#endif
            while (digitalRead(PIN_USER_BUTTON) == LOW) {
                delay(10);
            }
        }
    }
}
