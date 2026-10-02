#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"

struct DisplayFlightInfo {
    char runway[8];
    char action[16];
    char flight_label[32];
    char aircraft_type[32];
    char route[32];
    int altitude_ft;
    int speed_kts;
    int tracked_count;
    bool has_active_flight;
};

void initDisplay();
void showBootScreen(const char* status_text);
void showWifiStatus(bool connected, const char* ip_str);
void updateDisplayFlight(const DisplayFlightInfo& info);
