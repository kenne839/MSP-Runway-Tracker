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
};

struct WeatherData {
    char flight_category[8]; // e.g. "VFR", "MVFR", "IFR", "LIFR"
    int temp_f;              // e.g. 59
    char wind[16];           // e.g. "270@11kt" or "270@11G18kt"
    char pressure[16];       // e.g. "30.06 inHg"
    char condition[20];      // e.g. "Broken", "Rain", "Clear"
    bool valid;
};

struct DisplayTelemetryData {
    FlightEvent flights[MAX_TRACKED_FLIGHTS];
    uint8_t flight_count;
    int tracked_count;
    char active_runways_summary[32];
    char runway_roles_summary[32]; // e.g. "ARR 30R / DEP 30L"
    WeatherData weather;
    bool has_had_event;
    bool link_online;
    uint32_t last_rx_millis;
};

void initDisplay();
void showBootScreen(const char* status_text);
void showWifiStatus(bool connected, const char* ip_str);
void updateTelemetryData(const DisplayTelemetryData& data);
void renderDisplayLoop();
