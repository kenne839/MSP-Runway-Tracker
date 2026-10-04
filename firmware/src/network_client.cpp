#include "network_client.h"

#if !defined(BOARD_MODE_BLE)

#include "runway_leds.h"

static HTTPClient http;
static WiFiClient client;
static uint32_t last_poll_time = 0;
static uint32_t current_poll_interval = TELEMETRY_POLL_MS;
static uint8_t consecutive_http_errors = 0;

void initNetwork() {
    Serial.print(F("[WiFi] Connecting to SSID: "));
    Serial.println(WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(true);
    // Disable Wi-Fi modem sleep to ensure zero packet drop on 24/7 home mesh routers
    WiFi.setSleep(false);
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
        Serial.println(F("[WiFi] Initial connection failed. Will retry non-blockingly in background loop."));
        showWifiStatus(false, "");
    }
}

void checkWifiConnection() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println(F("[WiFi] Link lost. Attempting non-blocking reconnect..."));
        WiFi.reconnect();
    }
}

bool pollTelemetryData(DisplayTelemetryData& out_telemetry) {
    uint32_t now = millis();
    if (now - last_poll_time < current_poll_interval) {
        return false;
    }
    last_poll_time = now;

    if (WiFi.status() != WL_CONNECTED) {
        showConnectionStatusLed(false);
        out_telemetry.link_online = false;
        // Back off polling when Wi-Fi is lost so CPU stays dedicated to 30+ FPS LED animations
        current_poll_interval = 3000;
        return false;
    }

    http.begin(client, PI_SERVER_URL);
    // 1.2s timeout prevents long stalls on LAN, keeping LED animations smooth
    http.setTimeout(1200);

    int httpCode = http.GET();
    if (httpCode != HTTP_CODE_OK) {
        consecutive_http_errors++;
        // Exponential backoff: 3s -> 6s -> 10s max when Pi is rebooting or unreachable
        if (consecutive_http_errors >= 3) {
            current_poll_interval = 10000;
        } else if (consecutive_http_errors >= 1) {
            current_poll_interval = 3500;
        }

        Serial.printf("[HTTP] GET failed (%d), error: %s (backoff %u ms)\n",
                      httpCode, http.errorToString(httpCode).c_str(), current_poll_interval);
        http.end();
        showConnectionStatusLed(false);
        out_telemetry.link_online = false;
        return false;
    }

    // Success: restore normal 1.5s poll cadence immediately
    consecutive_http_errors = 0;
    current_poll_interval = TELEMETRY_POLL_MS;
    showConnectionStatusLed(true);
    out_telemetry.link_online = true;
    out_telemetry.last_rx_millis = now;

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

    // 3. Parse live METAR weather
    JsonObject weather = doc["weather"];
    if (!weather.isNull()) {
        strncpy(out_telemetry.weather.flight_category, weather["flight_category"] | "VFR", sizeof(out_telemetry.weather.flight_category) - 1);
        out_telemetry.weather.temp_f = weather["temp_f"] | 59;
        strncpy(out_telemetry.weather.wind, weather["wind"] | "Calm", sizeof(out_telemetry.weather.wind) - 1);
        strncpy(out_telemetry.weather.pressure, weather["pressure"] | "30.00 inHg", sizeof(out_telemetry.weather.pressure) - 1);
        strncpy(out_telemetry.weather.condition, weather["condition"] | "Clear", sizeof(out_telemetry.weather.condition) - 1);
        out_telemetry.weather.valid = true;
    }

    // 4. Parse runway roles summary (e.g. "ARR 30R / DEP 30L")
    const char* roles_summary = doc["runway_roles_summary"] | (doc["runway_roles"]["summary"] | "");
    if (roles_summary && strlen(roles_summary) > 0) {
        strncpy(out_telemetry.runway_roles_summary, roles_summary, sizeof(out_telemetry.runway_roles_summary) - 1);
    }

    // Update fallback summary string of recently active runways
    getRecentlyActiveRunwaysStr(out_telemetry.active_runways_summary, sizeof(out_telemetry.active_runways_summary));

    return true;
}

#endif
