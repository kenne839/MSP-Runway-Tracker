#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"
#include "display_oled.h"

void initNetwork();
void checkWifiConnection();
bool pollTelemetryData(DisplayFlightInfo& out_flight_info);
