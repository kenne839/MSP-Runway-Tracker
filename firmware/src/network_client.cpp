#include "network_client.h"

#if !defined(BOARD_MODE_BLE)

#include "runway_leds.h"

static HTTPClient http;
static WiFiClient client;
static uint32_t last_poll_time = 0;

void initNetwork() {
    Serial.print(F("[WiFi] Connecting to SSID: "));
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(F("."));
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println();
        Serial.print(F("[WiFi] Connected! IP Address: "));
        Serial.println(WiFi.localIP());
        showWifiStatus(true, WiFi.localIP().toString().c_str());
    } else {
        Serial.println();
        Serial.println(F("[WiFi] Initial connection failed. Will retry in background loop."));
        showWifiStatus(false, "");
    }
}

void checkWifiConnection() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println(F("[WiFi] Disconnected. Reconnecting..."));
        WiFi.disconnect();
        WiFi.reconnect();
    }
}

bool pollTelemetryData(DisplayTelemetryData& out_telemetry) {
    uint32_t now = millis();
    if (now - last_poll_time < TELEMETRY_POLL_MS) {
        return false;
    }
    last_poll_time = now;

    if (WiFi.status() != WL_CONNECTED) {
        showConnectionStatusLed(false);
        return false;
    }

    http.begin(client, PI_SERVER_URL);
    http.setTimeout(2500);

    int httpCode = http.GET();
    if (httpCode != HTTP_CODE_OK) {
        Serial.printf("[HTTP] GET failed, code: %d, error: %s\n", httpCode, http.errorToString(httpCode).c_str());
        http.end();
        showConnectionStatusLed(false);
        return false;
    }

    String payload = http.getString();
    http.end();

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, payload);

    if (error) {
        Serial.print(F("[JSON] Deserialization error: "));
        Serial.println(error.f_str());
        return false;
    }

    // 1. Reset runways and apply updated state from runway_summary
    resetAllRunwaysToIdle();

    JsonObject runway_summary = doc["runway_summary"];
    if (!runway_summary.isNull()) {
        for (JsonPair kv : runway_summary) {
            const char* rw_name = kv.key().c_str();
            const char* status = kv.value()["status"] | "IDLE";
            setRunwayState(rw_name, status, 0.5f);
        }
    }

    // 2. Refine runway animations and parse all active flight events for the OLED
    out_telemetry.flight_count = 0;
    out_telemetry.tracked_count = doc["tracked_count"] | 0;

    JsonArray ops = doc["active_operations"];
    if (!ops.isNull()) {
        for (JsonObject op : ops) {
            const char* rw = op["runway"] | "";
            const char* action = op["action"] | "IDLE";
            float progress = op["progress"] | 0.5f;
            setRunwayState(rw, action, progress);

            // Populate multi-flight OLED queue
            if (out_telemetry.flight_count < MAX_TRACKED_FLIGHTS) {
                FlightEvent& f = out_telemetry.flights[out_telemetry.flight_count];
                strncpy(f.runway, rw, sizeof(f.runway) - 1);
                strncpy(f.action, action, sizeof(f.action) - 1);
                strncpy(f.flight_label, op["flight_label"] | (op["callsign"] | "Unknown"), sizeof(f.flight_label) - 1);
                strncpy(f.aircraft_type, op["aircraft_type"] | "Unknown", sizeof(f.aircraft_type) - 1);
                strncpy(f.route, op["route"] | "", sizeof(f.route) - 1);
                out_telemetry.flight_count++;
            }
        }
    }

    out_telemetry.has_had_event = hasHadEventOccurred();

    // Update summary string of recently active runways for the OLED idle display
    getRecentlyActiveRunwaysStr(out_telemetry.active_runways_summary, sizeof(out_telemetry.active_runways_summary));

    return true;
}

#endif
