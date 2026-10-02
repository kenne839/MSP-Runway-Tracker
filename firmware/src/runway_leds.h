#pragma once

#include <Arduino.h>
#include <FastLED.h>
#include "config.h"

enum RunwayOpState {
    RW_STATE_IDLE,
    RW_STATE_LANDING,
    RW_STATE_TAKEOFF
};

struct RunwayControl {
    const char* name_low;    // e.g. "12L"
    const char* name_high;   // e.g. "30R"
    uint8_t start_idx;
    uint8_t end_idx;
    RunwayOpState current_state;
    bool active_on_high;     // True if operation is on 30R rather than 12L
    float progress;          // 0.0 to 1.0 aircraft position
};

void initLeds();
void setRunwayState(const char* runway_name, const char* action_str, float progress = 0.5f);
void resetAllRunwaysToIdle();
void renderRunwayAnimations();
void showConnectionStatusLed(bool connected);
