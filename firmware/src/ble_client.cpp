#include "config.h"

#if defined(BOARD_MODE_BLE)

#include "ble_client.h"
#include "runway_leds.h"
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

static String incoming_buffer = "";
static bool new_payload_ready = false;
static String latest_payload = "";

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* server) override {
        device_connected = true;
        Serial.println(F("[BLE] Office PC client connected."));
    }

    void onDisconnect(BLEServer* server) override {
        device_connected = false;
        Serial.println(F("[BLE] Office PC client disconnected."));
    }
};

class RxCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pCharacteristic) override {
        std::string rxValue = pCharacteristic->getValue();
        if (rxValue.length() > 0) {
            for (size_t i = 0; i < rxValue.length(); i++) {
                char c = rxValue[i];
                if (c == '\n' || c == '\r') {
                    if (incoming_buffer.length() > 0) {
                        latest_payload = incoming_buffer;
                        new_payload_ready = true;
                        incoming_buffer = "";
                    }
                } else {
                    incoming_buffer += c;
                }
            }
        }
    }
};

void initBleClient() {
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

static bool processPayloadJson(const String& json_str, DisplayTelemetryData& out_telemetry) {
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, json_str);
    if (error) {
        Serial.print(F("[BLE] JSON Parse error: "));
        Serial.println(error.f_str());
        return false;
    }

    // 1. Reset runways and apply updated state
    resetAllRunwaysToIdle();

    JsonObject runway_summary = doc["runway_summary"];
    if (!runway_summary.isNull()) {
        for (JsonPair kv : runway_summary) {
            const char* rw_name = kv.key().c_str();
            const char* status = kv.value()["status"] | "IDLE";
            setRunwayState(rw_name, status, 0.5f);
        }
    }

    // 2. Refine runway animations and parse active operations
    out_telemetry.flight_count = 0;
    out_telemetry.tracked_count = doc["tracked_count"] | 0;

    JsonArray ops = doc["active_operations"];
    if (!ops.isNull()) {
        for (JsonObject op : ops) {
            const char* rw = op["runway"] | "";
            const char* action = op["action"] | "IDLE";
            float progress = op["progress"] | 0.5f;
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

    // Also support fallback USB Serial input
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (incoming_buffer.length() > 0) {
                latest_payload = incoming_buffer;
                new_payload_ready = true;
                incoming_buffer = "";
            }
        } else {
            incoming_buffer += c;
        }
    }

    if (new_payload_ready) {
        new_payload_ready = false;
        return processPayloadJson(latest_payload, out_telemetry);
    }

    return false;
}

#endif
