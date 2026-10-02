#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"

#define MAX_TRACKED_FLIGHTS 4

struct FlightEvent {
    char runway[8];
    char action[16];
    char flight_label[32];
    char aircraft_type[32];
    char route[32];
    int altitude_ft;
    int speed_kts;
};

struct DisplayTelemetryData {
    FlightEvent flights[MAX_TRACKED_FLIGHTS];
    uint8_t flight_count;
    int tracked_count;
    char active_runways_summary[32];
};

void initDisplay();
void showBootScreen(const char* status_text);
void showWifiStatus(bool connected, const char* ip_str);
void updateTelemetryData(const DisplayTelemetryData& data);
void renderDisplayLoop();
