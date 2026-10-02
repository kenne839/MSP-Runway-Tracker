#pragma once

#include <Arduino.h>
#include <FastLED.h>
#include "config.h"

enum RunwayOpState {
    RW_STATE_IDLE,
    RW_STATE_LANDING,
    RW_STATE_TAKEOFF
};

struct RunwaySegment {
    const char* name_start;   // Runway designator at start_idx (e.g. "12R", "30R", "22", "35")
    const char* name_end;     // Runway designator at end_idx   (e.g. "30L", "12L", "4",  "17")
    uint8_t start_idx;        // FastLED strip start index
    uint8_t end_idx;          // FastLED strip end index
    int8_t crossing_idx;      // Crossing LED index (-1 if none)
    RunwayOpState state;      // IDLE, LANDING, TAKEOFF
    bool moving_forward;      // true: start_idx -> end_idx, false: end_idx -> start_idx
    float comet_pos;          // Current position along length (0.0 to length - 1)
    float speed;              // Position delta per frame
    bool was_recently_used;   // True if this runway was active in the current airport flow
    bool recent_forward;      // Direction of recent traffic flow
    float idle_strobe_pos;    // Position for slow idle yellow heartbeat strobe
};

void initLeds();
void setRunwayState(const char* runway_name, const char* action_str, float progress = 0.5f);
void resetAllRunwaysToIdle();
void renderRunwayAnimations();
void showConnectionStatusLed(bool connected);
void getRecentlyActiveRunwaysStr(char* out_buf, size_t buf_len);
