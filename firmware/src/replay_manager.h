#pragma once

#include <Arduino.h>
#include "config.h"
#include "display_oled.h"

#define MAX_REPLAY_EVENTS 64

struct ReplayEvent {
    char     id[32];
    uint16_t offset_sec;
    char     runway[8];
    char     action[16];
    uint16_t duration_ms;
    char     flight_label[32];
    char     aircraft_type[32];
    char     route[32];
    uint16_t speed_kts;
    uint16_t altitude_ft;
    bool     has_fired;
    bool     is_active;
    uint32_t active_until_millis;
};

void initReplayManager();
bool ingestReplayBatchJson(const String& json_str, DisplayTelemetryData& out_telemetry);
bool tickReplay(uint32_t current_millis, DisplayTelemetryData& out_telemetry);
bool isReplayBatchActive();
uint8_t getReplayEventCount();

