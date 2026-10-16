#include "config.h"

#if defined(BOARD_MODE_BLE)

#include "ble_client.h"
#include "runway_leds.h"
#include "theme_manager.h"
#include "replay_manager.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <ArduinoJson.h>

static BLEServer* pServer = nullptr;
static BLECharacteristic* pTxCharacteristic = nullptr;
static BLECharacteristic* pRxCharacteristic = nullptr;
static bool device_connected = false;
static bool old_device_connected = false;

static SemaphoreHandle_t ble_mutex = nullptr;
#define LOCK_BLE()   if (ble_mutex) xSemaphoreTake(ble_mutex, portMAX_DELAY)
#define UNLOCK_BLE() if (ble_mutex) xSemaphoreGive(ble_mutex)

struct BleLockGuard {
    BleLockGuard() { LOCK_BLE(); }
    ~BleLockGuard() { UNLOCK_BLE(); }
};

// Bounded static BSS reception buffers (Zero heap fragmentation)
#define BLE_RX_BUFFER_SIZE  16384

static char s_rx_buffer[BLE_RX_BUFFER_SIZE];
static size_t s_rx_head = 0;
static char s_ready_buffer[BLE_RX_BUFFER_SIZE];
static size_t s_ready_len = 0;
static volatile bool s_payload_available = false;

static int s_brace_depth = 0;
static bool s_in_string = false;
static bool s_escape = false;

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* server) override {
        BleLockGuard lock;
        device_connected = true;
        s_rx_head = 0;
        s_brace_depth = 0;
        s_in_string = false;
        s_escape = false;
        s_payload_available = false;
        Serial.println(F("[BLE] Client connected. Stream parser reset to clean state."));
    }

    void onDisconnect(BLEServer* server) override {
        BleLockGuard lock;
        device_connected = false;
        s_rx_head = 0;
        s_brace_depth = 0;
        s_in_string = false;
        s_escape = false;
        s_payload_available = false;
        Serial.println(F("[BLE] Client disconnected. Stream parser reset."));
    }
};

static void feedRxChar(char c) {
    if (s_rx_head < (BLE_RX_BUFFER_SIZE - 1)) {
        s_rx_buffer[s_rx_head++] = c;
    } else {
        Serial.println(F("[BLE] Warning: RX buffer overflow! Dropping corrupted frame."));
        s_rx_head = 0;
        s_brace_depth = 0;
        s_in_string = false;
        s_escape = false;
        return;
    }

    // Bracket & string tracking to determine JSON completeness
    if (s_escape) {
        s_escape = false;
    } else if (c == '\\') {
        if (s_in_string) s_escape = true;
    } else if (c == '"') {
        s_in_string = !s_in_string;
    } else if (!s_in_string) {
        if (c == '{') {
            s_brace_depth++;
        } else if (c == '}') {
            s_brace_depth--;
            if (s_brace_depth == 0 && s_rx_head > 1) {
                // Complete JSON object finished!
                s_rx_buffer[s_rx_head] = '\0';
                memcpy(s_ready_buffer, s_rx_buffer, s_rx_head + 1);
                s_ready_len = s_rx_head;
                s_payload_available = true;
                s_rx_head = 0;
                s_brace_depth = 0;
                s_in_string = false;
                s_escape = false;
                Serial.printf("[BLE] Complete frame detected on brace depth 0: %u bytes!\n", (unsigned int)s_ready_len);
                return;
            }
        }
    }

    // Delimitation for newline when outside of JSON string or braces, or newline fallback
    if (c == '\n' || c == '\r') {
        if (!s_payload_available && s_rx_head > 1) {
            // Trim leading/trailing whitespace and check for valid JSON wrapper
            size_t start = 0;
            while (start < s_rx_head && (s_rx_buffer[start] == ' ' || s_rx_buffer[start] == '\n' || s_rx_buffer[start] == '\r')) start++;
            size_t end = s_rx_head;
            while (end > start && (s_rx_buffer[end - 1] == ' ' || s_rx_buffer[end - 1] == '\n' || s_rx_buffer[end - 1] == '\r')) end--;
            if ((end - start) > 1 && s_rx_buffer[start] == '{' && s_rx_buffer[end - 1] == '}') {
                s_rx_buffer[end] = '\0';
                memcpy(s_ready_buffer, &s_rx_buffer[start], (end - start) + 1);
                s_ready_len = end - start;
                s_payload_available = true;
                Serial.printf("[BLE] Complete frame detected on newline delimiter: %u bytes!\n", (unsigned int)s_ready_len);
            }
        }
        s_rx_head = 0;
        s_brace_depth = 0;
        s_in_string = false;
        s_escape = false;
    }
}

static void handleIncomingBytes(const uint8_t* data, size_t len) {
    if (data == nullptr || len == 0) return;
    BleLockGuard lock;
    for (size_t i = 0; i < len; i++) {
        feedRxChar((char)data[i]);
    }
    Serial.printf("[BLE RX] Chunk: %u bytes (accum=%u, depth=%d)\n", (unsigned int)len, (unsigned int)s_rx_head, s_brace_depth);
}

class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pCharacteristic, esp_ble_gatts_cb_param_t* param) override {
        if (param != nullptr && param->write.len > 0) {
            handleIncomingBytes(param->write.value, param->write.len);
        } else {
            std::string rxValue = pCharacteristic->getValue();
            handleIncomingBytes((const uint8_t*)rxValue.data(), rxValue.length());
        }
    }

    void onWrite(BLECharacteristic* pCharacteristic) override {
        std::string rxValue = pCharacteristic->getValue();
        handleIncomingBytes((const uint8_t*)rxValue.data(), rxValue.length());
    }
};

void sendBleApplicationAck(bool success, uint32_t batch_id, uint8_t ops_count, const char* error_msg) {
    if (pTxCharacteristic != nullptr && device_connected) {
        char ack_json[160];
        if (success) {
            snprintf(ack_json, sizeof(ack_json), 
                "{\"status\":\"ACK\",\"batch_id\":%lu,\"ops\":%u,\"free_heap\":%lu}\n",
                (unsigned long)batch_id, ops_count, (unsigned long)esp_get_free_heap_size());
        } else {
            snprintf(ack_json, sizeof(ack_json), 
                "{\"status\":\"NACK\",\"error\":\"%s\",\"free_heap\":%lu}\n",
                error_msg ? error_msg : "UNKNOWN", (unsigned long)esp_get_free_heap_size());
        }
        pTxCharacteristic->setValue((uint8_t*)ack_json, strlen(ack_json));
        pTxCharacteristic->notify();
        Serial.printf("[BLE] >> Transmitted Application ACK: %s", ack_json);
    }
}

void initBleClient() {
    if (ble_mutex == nullptr) {
        ble_mutex = xSemaphoreCreateMutex();
    }
    initReplayManager();
    Serial.println(F("[BLE] Initializing Bluetooth Low Energy on ESP32-S3..."));
    BLEDevice::init(BLE_DEVICE_NAME);
    BLEDevice::setMTU(512);

    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService* pService = pServer->createService(BLE_NUS_SERVICE_UUID);

    pTxCharacteristic = pService->createCharacteristic(
        BLE_NUS_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pTxCharacteristic->addDescriptor(new BLE2902());

    pRxCharacteristic = pService->createCharacteristic(
        BLE_NUS_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    pRxCharacteristic->setCallbacks(new RxCallbacks());

    pService->start();

    BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(BLE_NUS_SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.printf("[BLE] Advertising started as: %s\n", BLE_DEVICE_NAME);
}

bool isBleConnected() {
    return device_connected;
}

static bool processPayloadJson(const char* json_str, DisplayTelemetryData& out_telemetry) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json_str);
    if (error) {
        Serial.print(F("[BLE] JSON Parse error: "));
        Serial.println(error.f_str());
        return false;
    }

    // Apply Dynamic LED Theme if present (zero-lag version dirty check)
    if (!doc["theme"].isNull()) {
        applyThemeIfPresent(doc["theme"]);
    }

    // 1. Process active operations and maintain continuous comet glides
    bool runway_has_active_op[4] = {false, false, false, false};
    out_telemetry.flight_count = 0;
    out_telemetry.tracked_count = doc["tracked_count"] | 0;

    JsonArray ops = doc["active_operations"];
    if (!ops.isNull() && ops.size() > 0) {
        for (JsonObject op : ops) {
            const char* rw = op["runway"] | "";
            const char* action = op["action"] | "IDLE";
            float progress = op["progress"] | 0.5f;

            int rw_idx = getRunwayIndex(rw);
            if (rw_idx >= 0) {
                runway_has_active_op[rw_idx] = true;
            }

            setRunwayState(rw, action, progress);

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

    // Set any runway that does not have an active flight operation to IDLE
    // without clobbering active gliding comets.
    for (int i = 0; i < 4; i++) {
        if (!runway_has_active_op[i]) {
            setRunwayIdle(i);
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

    // 5. Parse updated time
    const char* ut = doc["updated_time"] | "";
    if (ut && strlen(ut) > 0) {
        strncpy(out_telemetry.updated_time, ut, sizeof(out_telemetry.updated_time) - 1);
        out_telemetry.updated_time[sizeof(out_telemetry.updated_time) - 1] = '\0';
    }

    getRecentlyActiveRunwaysStr(out_telemetry.active_runways_summary, sizeof(out_telemetry.active_runways_summary));

    out_telemetry.link_online = true;
    out_telemetry.last_rx_millis = millis();

    return true;
}

bool pollBleTelemetry(DisplayTelemetryData& out_telemetry) {
    // Restart advertising if disconnected
    if (!device_connected && old_device_connected) {
        delay(500);
        pServer->startAdvertising();
        Serial.println(F("[BLE] Restarted advertising."));
        old_device_connected = device_connected;
    }
    if (device_connected && !old_device_connected) {
        old_device_connected = device_connected;
    }

    bool has_new_payload = false;
    static char local_parse_buffer[BLE_RX_BUFFER_SIZE];
    size_t local_parse_len = 0;

    {
        BleLockGuard lock;
        // Only accept fallback USB Serial input when no BLE client is active
        if (!device_connected) {
            while (Serial.available()) {
                feedRxChar((char)Serial.read());
            }
        }

        if (s_payload_available) {
            memcpy(local_parse_buffer, s_ready_buffer, s_ready_len + 1);
            local_parse_len = s_ready_len;
            s_payload_available = false;
            has_new_payload = true;
        }
    }

    if (has_new_payload) {
        Serial.printf("[BLE] Ingestion processing: %u bytes received.\n", (unsigned int)local_parse_len);
        if (strstr(local_parse_buffer, "\"batch_start_epoch\"") != nullptr || strstr(local_parse_buffer, "\"operations\"") != nullptr) {
            return ingestReplayBatchJson(local_parse_buffer, local_parse_len, out_telemetry);
        }
        return processPayloadJson(local_parse_buffer, out_telemetry);
    }

    if (isReplayBatchActive()) {
        return tickReplay(millis(), out_telemetry);
    }

    return false;
}

#endif
