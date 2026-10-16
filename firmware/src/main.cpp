#include <Arduino.h>
#include "config.h"
#include "runway_leds.h"
#include "display_oled.h"
#include "theme_manager.h"

#if defined(BOARD_MODE_BLE)
#include "ble_client.h"
#include "replay_manager.h"
#else
#include "network_client.h"
#include "telnet_server.h"
static uint32_t last_wifi_check = 0;
#endif

static DisplayTelemetryData telemetry_data;

void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println();
    Serial.println(F("=================================================="));
#if defined(BOARD_MODE_DEVKIT) && defined(BOARD_MODE_BLE)
    Serial.printf("   KMSP RUNWAY TRACKER - %s\n", FIRMWARE_VERSION);
    Serial.println(F("   MODE: DEVKIT BLE (WS2812 on GPIO48, SDA=1, SCL=2)"));
    Serial.printf("   BLE Peripheral: %s\n", BLE_DEVICE_NAME);
    Serial.println(F("   Ready for Android / Companion App sync...       "));
#elif defined(BOARD_MODE_BLE)
    Serial.printf("   KMSP RUNWAY TRACKER - %s\n", FIRMWARE_VERSION);
    Serial.println(F("   MODE: OFFICE BLE (84 WS2812 LEDs, Level Shifted)"));
    Serial.printf("   BLE Peripheral: %s\n", BLE_DEVICE_NAME);
#elif defined(BOARD_MODE_DEVKIT)
    Serial.printf("   KMSP RUNWAY TRACKER - %s\n", FIRMWARE_VERSION);
    Serial.println(F("   MODE: DEVKIT WI-FI (WS2812 on GPIO48)           "));
#else
    Serial.printf("   KMSP RUNWAY TRACKER - %s\n", FIRMWARE_VERSION);
    Serial.println(F("   MODE: HOME WI-FI (84 WS2812 LEDs)               "));
#endif
    Serial.printf("   Build Timestamp: %s %s\n", __DATE__, __TIME__);
    Serial.println(F("=================================================="));

    // 1. Initialize user button on SW1 (GPIO0)
    pinMode(PIN_USER_BUTTON, INPUT_PULLUP);

    // 2. Initialize OLED Display (I2C on GPIO1 / GPIO2)
    initDisplay();
#if defined(BOARD_MODE_DEVKIT) && defined(BOARD_MODE_BLE)
    showDetailedBootScreen(FIRMWARE_VERSION, "DevKit BLE", BLE_DEVICE_NAME, 2500);
#elif defined(BOARD_MODE_BLE)
    showDetailedBootScreen(FIRMWARE_VERSION, "Office BLE", BLE_DEVICE_NAME, 2500);
#elif defined(BOARD_MODE_DEVKIT)
    showDetailedBootScreen(FIRMWARE_VERSION, "DevKit Wi-Fi", "Wi-Fi Mode", 2500);
#else
    showDetailedBootScreen(FIRMWARE_VERSION, "Home Wi-Fi", "Wi-Fi Mode", 2500);
#endif

    // 3. Initialize Theme Manager & WS2812B LEDs
    initThemeManager();
    initLeds();

    // 4. Initialize Communications
#if defined(BOARD_MODE_BLE)
    initBleClient();
#else
    showBootScreen("Connecting Wi-Fi...");
    initNetwork();
    initTelnetServer();
#endif

    // 5. Start Background LED Animation Task
    startLedAnimationTask();

    memset(&telemetry_data, 0, sizeof(telemetry_data));
    telemetry_data.flight_count = 0;
    telemetry_data.tracked_count = 0;
    telemetry_data.has_had_event = false;
    telemetry_data.active_runways_summary[0] = '\0';
}

void loop() {
    // 1. Core 0 background Telnet console handling (LED animations run continuously on Core 1)
#if !defined(BOARD_MODE_BLE)
    handleTelnetServer();
#endif

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

    // 3. Link loss watchdog: If link is lost for >45s, remove active operations and return to IDLE
#if defined(BOARD_MODE_BLE)
    // In BLE Replay mode, the phone connects ephemerally (pushes 30m batch and disconnects).
    // As long as a replay batch is active in RAM, the device is fully functional and NOT in link loss.
    if (!isReplayBatchActive() && telemetry_data.last_rx_millis > 0 && (millis() - telemetry_data.last_rx_millis > LINK_LOSS_IDLE_TIMEOUT_MS)) {
        if (telemetry_data.flight_count > 0 || telemetry_data.link_online) {
            Serial.println(F("[Watchdog] BLE replay idle & no link > 45s. Transitioning to Standby."));
            telemetry_data.flight_count = 0;
            telemetry_data.link_online = false;
            resetAllRunwaysToIdle();
            updateTelemetryData(telemetry_data);
        }
    }
#else
    if (telemetry_data.last_rx_millis > 0 && (millis() - telemetry_data.last_rx_millis > LINK_LOSS_IDLE_TIMEOUT_MS)) {
        if (telemetry_data.flight_count > 0 || telemetry_data.link_online) {
            Serial.println(F("[Watchdog] Telemetry link offline > 45s. Removing active runway events to IDLE."));
            telemetry_data.flight_count = 0;
            telemetry_data.link_online = false;
            resetAllRunwaysToIdle();
            updateTelemetryData(telemetry_data);
        }
    }
#endif

    // 4. Render OLED display (handles 3-second multi-flight cycling and idle state)
    renderDisplayLoop();

    // 4. Non-blocking user button press detection (SW1 / GPIO0)
    static bool button_last_state = HIGH;
    static uint32_t last_button_check = 0;
    bool current_button_state = digitalRead(PIN_USER_BUTTON);

    if (current_button_state != button_last_state && (millis() - last_button_check > 50)) {
        last_button_check = millis();
        button_last_state = current_button_state;

        if (current_button_state == LOW) { // Falling edge (button pressed)
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
        }
    }

    // Yield CPU to FreeRTOS IDLE task on Core 1 to prevent Task Watchdog (TG1WDT) timeouts
    delay(5);
}
