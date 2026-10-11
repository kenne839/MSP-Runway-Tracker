#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

// POD struct for LED theme configuration
struct LedThemeConfig {
    uint8_t master_brightness;   // 5–48 (Clamped to safe ceiling, current active brightness)
    uint32_t landing_head_rgb;   // Touchdown strobe head (Default: 0xFFFFFF Crisp White)
    uint32_t landing_tail_rgb;   // Rollout glidepath tail (Default: 0x00FF88 Emerald Green)
    uint32_t takeoff_head_rgb;   // Acceleration roll head (Default: 0xFFE0A0 Warm White)
    uint32_t takeoff_tail_rgb;   // Afterburner flame tail (Default: 0xFF4500 Amber/Orange)
    uint32_t idle_threshold_rgb; // Inactive runway threshold (Default: 0x002200 Dim Green)
    uint32_t conflict_warn_rgb;  // Intersecting runway alert (Default: 0xFF0000 Flashing Red)
    uint16_t version;            // Monotonic version counter for dirty-checking
    bool solar_sync;             // Circadian solar-adaptive brightness toggle
    uint8_t manual_brightness;   // Stored manual brightness preference
};

void initThemeManager();
const LedThemeConfig& getThemeConfig();
bool applyThemeIfPresent(JsonVariantConst theme_variant);
void setMasterBrightness(uint8_t brightness);

