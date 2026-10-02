#pragma once

#include <Arduino.h>
#include "config.h"
#include "display_oled.h"

#if !defined(BOARD_MODE_BLE)

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

void initNetwork();
void checkWifiConnection();
bool pollTelemetryData(DisplayTelemetryData& out_telemetry);

#endif
