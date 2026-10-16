#include "replay_manager.h"
#include "runway_leds.h"
#include "theme_manager.h"
#include "ble_client.h"
#include <ArduinoJson.h>

static SemaphoreHandle_t replay_mutex = nullptr;
#define LOCK_REPLAY()   if (replay_mutex) xSemaphoreTake(replay_mutex, portMAX_DELAY)
#define UNLOCK_REPLAY() if (replay_mutex) xSemaphoreGive(replay_mutex)

struct ReplayLockGuard {
    ReplayLockGuard() { LOCK_REPLAY(); }
    ~ReplayLockGuard() { UNLOCK_REPLAY(); }
};

// Static BSS storage: zero heap allocations during execution
static ReplayEvent s_events[MAX_REPLAY_EVENTS];
static uint8_t     s_event_count = 0;
static uint32_t    s_batch_start_millis = 0;
static uint32_t    s_last_sync_millis = 0;
static uint32_t    s_window_seconds = 1800;
static bool        s_batch_active = false;

void initReplayManager() {
    if (replay_mutex == nullptr) {
        replay_mutex = xSemaphoreCreateMutex();
    }
    ReplayLockGuard lock;
    s_event_count = 0;
    s_batch_active = false;
    memset(s_events, 0, sizeof(s_events));
}

bool isReplayBatchActive() {
    return s_batch_active;
}

uint8_t getReplayEventCount() {
    return s_event_count;
}

bool ingestReplayBatchJson(const char* json_str, size_t json_len, DisplayTelemetryData& out_telemetry) {
    if (json_str == nullptr || json_len == 0) {
        return false;
    }

    Serial.printf("[REPLAY] Ingesting batch. Free heap before JSON parse: %lu bytes (payload: %u bytes)\n",
                  (unsigned long)esp_get_free_heap_size(), (unsigned int)json_len);

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json_str, json_len);
    if (error) {
        Serial.printf("[REPLAY] JSON Parse error: %s (input size: %u bytes)\n", error.c_str(), (unsigned int)json_len);
#if defined(BOARD_MODE_BLE)
        sendBleApplicationAck(false, 0, 0, error.c_str());
#endif
        return false;
    }

    // 1. Ingest Dynamic Theme if present
    if (!doc["theme"].isNull()) {
        applyThemeIfPresent(doc["theme"]);
    }

    // 2. Ingest METAR weather
    JsonObject weather = doc["weather"];
    if (!weather.isNull()) {
        strncpy(out_telemetry.weather.flight_category, weather["flight_category"] | "VFR", sizeof(out_telemetry.weather.flight_category) - 1);
        out_telemetry.weather.temp_f = weather["temp_f"] | 59;
        strncpy(out_telemetry.weather.wind, weather["wind"] | "Calm", sizeof(out_telemetry.weather.wind) - 1);
        strncpy(out_telemetry.weather.pressure, weather["pressure"] | "30.00 inHg", sizeof(out_telemetry.weather.pressure) - 1);
        strncpy(out_telemetry.weather.condition, weather["condition"] | "Clear", sizeof(out_telemetry.weather.condition) - 1);
        out_telemetry.weather.valid = true;
    }

    // 3. Ingest runway roles summary
    const char* roles_summary = doc["runway_roles_summary"] | (doc["runway_roles"]["summary"] | "");
    if (roles_summary && strlen(roles_summary) > 0) {
        strncpy(out_telemetry.runway_roles_summary, roles_summary, sizeof(out_telemetry.runway_roles_summary) - 1);
    }

    // 4. Ingest network quota
    JsonObject net = doc["network"];
    if (!net.isNull()) {
        out_telemetry.rate_limited = net["rate_limited"] | false;
        out_telemetry.api_online = net["online"] | true;
        out_telemetry.requests_today = net["requests_today"] | 0;
        out_telemetry.quota_limit = net["quota_limit"] | 4000;
    }

    // 5. Ingest and merge operations queue
    JsonArray ops = doc["operations"];
    if (ops.isNull()) {
#if defined(BOARD_MODE_BLE)
        sendBleApplicationAck(false, 0, 0, "MISSING_OPERATIONS");
#endif
        return false;
    }

    uint32_t now = millis();
    uint32_t batch_id = doc["batch_id"] | 0;

    {
        ReplayLockGuard lock;
        s_window_seconds = doc["window_seconds"] | 1800;

        // Temporary tracking of currently active runway animations to prevent clobbering
        bool runway_currently_active[4] = {false, false, false, false};
        for (uint8_t i = 0; i < s_event_count; i++) {
            if (s_events[i].is_active && (now < s_events[i].active_until_millis)) {
                int r_idx = getRunwayIndex(s_events[i].runway);
                if (r_idx >= 0 && r_idx < 4) {
                    runway_currently_active[r_idx] = true;
                }
            }
        }

        s_event_count = 0;
        for (JsonObject op : ops) {
            if (s_event_count >= MAX_REPLAY_EVENTS) {
                break;
            }
            ReplayEvent& ev = s_events[s_event_count];
            strncpy(ev.id, op["id"] | "", sizeof(ev.id) - 1);
            ev.offset_sec = op["offset_sec"] | 0;
            strncpy(ev.runway, op["runway"] | "", sizeof(ev.runway) - 1);
            strncpy(ev.action, op["action"] | "LANDING", sizeof(ev.action) - 1);
            ev.duration_ms = op["duration_ms"] | 24000;
            strncpy(ev.flight_label, op["flight_label"] | (op["callsign"] | "Unknown"), sizeof(ev.flight_label) - 1);
            strncpy(ev.aircraft_type, op["aircraft_type"] | "Unknown", sizeof(ev.aircraft_type) - 1);
            strncpy(ev.route, op["route"] | "", sizeof(ev.route) - 1);
            ev.speed_kts = op["speed_kts"] | 140;
            ev.altitude_ft = op["altitude_ft"] | 1000;
            ev.has_fired = false;
            ev.is_active = false;
            ev.active_until_millis = 0;

            s_event_count++;
        }

        // Bench verification / Immediate Physical Feedback:
        // If the first event offset is > 2 seconds, shift timeline so the first event starts at offset 2s!
        if (s_event_count > 0 && s_events[0].offset_sec > 2) {
            uint32_t shift = s_events[0].offset_sec - 2;
            Serial.printf("[REPLAY] Bench alignment: shifted timeline by -%us (first event will fire in 2s!)\n", shift);
            for (uint8_t i = 0; i < s_event_count; i++) {
                if (s_events[i].offset_sec >= shift) {
                    s_events[i].offset_sec -= shift;
                } else {
                    s_events[i].offset_sec = 0;
                }
            }
        }

        s_batch_start_millis = now;
        s_last_sync_millis = now;
        s_batch_active = (s_event_count > 0);
    }

    out_telemetry.link_online = true;
    out_telemetry.last_rx_millis = now;
    out_telemetry.tracked_count = doc["operations_count"] | s_event_count;

    // Display temporary 3.5s confirmation splash on OLED
    showSyncIngestionCard(s_event_count, s_window_seconds);

    // Immediate physical feedback: Double green flash on LEDs
    triggerBatchIngestedFeedback();

#if defined(BOARD_MODE_BLE)
    // Transmit Application-Level ACK over NUS TX
    sendBleApplicationAck(true, batch_id, s_event_count);
#endif

    Serial.printf("[REPLAY] Ingested new batch: %u operations over %u sec window | Free Heap: %lu bytes\n",
                  s_event_count, s_window_seconds, (unsigned long)esp_get_free_heap_size());
    return true;
}

bool ingestReplayBatchJson(const String& json_str, DisplayTelemetryData& out_telemetry) {
    return ingestReplayBatchJson(json_str.c_str(), json_str.length(), out_telemetry);
}

bool tickReplay(uint32_t current_millis, DisplayTelemetryData& out_telemetry) {
    if (!s_batch_active) {
        return false;
    }

    // Check buffer underrun timeout: 32 minutes (1,920,000 ms) without a new batch
    if (s_last_sync_millis > 0 && (current_millis - s_last_sync_millis > 32 * 60 * 1000UL)) {
        ReplayLockGuard lock;
        Serial.println(F("[REPLAY] Buffer underrun: >32m without sync packet. Transitioning to Standby."));
        s_batch_active = false;
        resetAllRunwaysToIdle();
        out_telemetry.flight_count = 0;
        out_telemetry.link_online = false;
        return true;
    }

    uint32_t elapsed_sec = (current_millis - s_batch_start_millis) / 1000;
    bool state_changed = false;

    {
        ReplayLockGuard lock;
        for (uint8_t i = 0; i < s_event_count; i++) {
            ReplayEvent& ev = s_events[i];

            // 1. Trigger event when virtual elapsed time reaches event offset
            if (!ev.has_fired && (elapsed_sec >= ev.offset_sec)) {
                ev.has_fired = true;
                ev.is_active = true;
                ev.active_until_millis = current_millis + ev.duration_ms;

                Serial.printf("[REPLAY] [FLIGHT] Firing %s on RW %s: %s (%s) [dur=%ums]\n",
                              ev.action, ev.runway, ev.flight_label, ev.aircraft_type, ev.duration_ms);
                Serial.flush();

                setRunwayState(ev.runway, ev.action, 0.0f);
                wakeDisplay(); // Instantly wake OLED display when an operation fires
                state_changed = true;
            }

            // 2. Check for completion of active animations
            if (ev.is_active && (current_millis >= ev.active_until_millis)) {
                ev.is_active = false;
                Serial.printf("[REPLAY] ✓ Completed %s on RW %s: %s\n", ev.action, ev.runway, ev.flight_label);
                Serial.flush();
                int rw_idx = getRunwayIndex(ev.runway);
                if (rw_idx >= 0) {
                    // Only set runway idle if no other overlapping operation is running on it
                    bool another_active = false;
                    for (uint8_t j = 0; j < s_event_count; j++) {
                        if (j != i && s_events[j].is_active && strcmp(s_events[j].runway, ev.runway) == 0) {
                            another_active = true;
                            break;
                        }
                    }
                    if (!another_active) {
                        setRunwayIdle(rw_idx);
                    }
                }
                state_changed = true;
            }
        }

        // Dynamically rebuild the active flights banner from all currently active operations
        if (state_changed) {
            uint8_t active_count = 0;
            for (uint8_t j = 0; j < s_event_count; j++) {
                if (s_events[j].is_active && active_count < MAX_TRACKED_FLIGHTS) {
                    FlightEvent& f = out_telemetry.flights[active_count];
                    strncpy(f.runway, s_events[j].runway, sizeof(f.runway) - 1);
                    strncpy(f.action, s_events[j].action, sizeof(f.action) - 1);
                    strncpy(f.flight_label, s_events[j].flight_label, sizeof(f.flight_label) - 1);
                    strncpy(f.aircraft_type, s_events[j].aircraft_type, sizeof(f.aircraft_type) - 1);
                    strncpy(f.route, s_events[j].route, sizeof(f.route) - 1);
                    active_count++;
                }
            }
            out_telemetry.flight_count = active_count;
            out_telemetry.has_had_event = true;
            out_telemetry.link_online = true;
            out_telemetry.last_rx_millis = current_millis;
        }
    }

    if (state_changed) {
        getRecentlyActiveRunwaysStr(out_telemetry.active_runways_summary, sizeof(out_telemetry.active_runways_summary));
    }

    return state_changed;
}

