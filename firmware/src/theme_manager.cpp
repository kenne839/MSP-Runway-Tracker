#include "theme_manager.h"
#include "config.h"
#include <Preferences.h>
#include <FastLED.h>

#if !defined(BOARD_MODE_BLE)
#include "telnet_server.h"
#endif

#ifndef HARDWARE_MAX_SAFE_BRIGHTNESS
#if defined(BOARD_MODE_DEVKIT)
#define HARDWARE_MAX_SAFE_BRIGHTNESS 40
#else
#define HARDWARE_MAX_SAFE_BRIGHTNESS 48
#endif
#endif

#define HARDWARE_MIN_SAFE_BRIGHTNESS 5

static Preferences prefs;
static LedThemeConfig current_theme = {
    .master_brightness = MAX_LED_BRIGHTNESS,
    .landing_head_rgb = 0xFFFFFF,
    .landing_tail_rgb = 0x00FF88,
    .takeoff_head_rgb = 0xFFE0A0,
    .takeoff_tail_rgb = 0xFF4500,
    .idle_threshold_rgb = 0x002200,
    .conflict_warn_rgb = 0xFF0000,
    .version = 1,
    .solar_sync = false,
    .manual_brightness = MAX_LED_BRIGHTNESS
};

static uint32_t parseHexColor(JsonVariantConst val, uint32_t fallback) {
    if (val.is<uint32_t>()) {
        return val.as<uint32_t>();
    }
    if (val.is<const char*>()) {
        const char* s = val.as<const char*>();
        if (!s || *s == '\0') return fallback;
        if (s[0] == '#') s++;
        else if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
        return (uint32_t)strtoul(s, nullptr, 16);
    }
    return fallback;
}

void initThemeManager() {
    Serial.println(F("[THEME] Initializing NVS Flash LED theme manager..."));
    prefs.begin("led_theme", false);

    if (prefs.isKey("ver")) {
        current_theme.version = prefs.getUShort("ver", 1);
        current_theme.master_brightness = prefs.getUChar("br", MAX_LED_BRIGHTNESS);
        current_theme.manual_brightness = prefs.getUChar("man_br", current_theme.master_brightness);
        current_theme.solar_sync = prefs.getBool("solar", false);
        current_theme.landing_head_rgb = prefs.getUInt("l_head", 0xFFFFFF);
        current_theme.landing_tail_rgb = prefs.getUInt("l_tail", 0x00FF88);
        current_theme.takeoff_head_rgb = prefs.getUInt("t_head", 0xFFE0A0);
        current_theme.takeoff_tail_rgb = prefs.getUInt("t_tail", 0xFF4500);
        current_theme.idle_threshold_rgb = prefs.getUInt("idle", 0x002200);
        current_theme.conflict_warn_rgb = prefs.getUInt("conf", 0xFF0000);

        // Sanitize brightness loaded from flash
        current_theme.master_brightness = constrain(
            current_theme.master_brightness,
            HARDWARE_MIN_SAFE_BRIGHTNESS,
            HARDWARE_MAX_SAFE_BRIGHTNESS
        );
        current_theme.manual_brightness = constrain(
            current_theme.manual_brightness,
            HARDWARE_MIN_SAFE_BRIGHTNESS,
            HARDWARE_MAX_SAFE_BRIGHTNESS
        );

        Serial.printf("[THEME] Loaded from NVS: v%u, Brightness=%u (SolarSync=%d), L_Tail=0x%06X, T_Tail=0x%06X\n",
                      current_theme.version, current_theme.master_brightness, current_theme.solar_sync ? 1 : 0,
                      current_theme.landing_tail_rgb, current_theme.takeoff_tail_rgb);
    } else {
        Serial.println(F("[THEME] No saved theme in NVS; writing factory defaults."));
        prefs.putUShort("ver", current_theme.version);
        prefs.putUChar("br", current_theme.master_brightness);
        prefs.putUChar("man_br", current_theme.manual_brightness);
        prefs.putBool("solar", current_theme.solar_sync);
        prefs.putUInt("l_head", current_theme.landing_head_rgb);
        prefs.putUInt("l_tail", current_theme.landing_tail_rgb);
        prefs.putUInt("t_head", current_theme.takeoff_head_rgb);
        prefs.putUInt("t_tail", current_theme.takeoff_tail_rgb);
        prefs.putUInt("idle", current_theme.idle_threshold_rgb);
        prefs.putUInt("conf", current_theme.conflict_warn_rgb);
    }
}

const LedThemeConfig& getThemeConfig() {
    return current_theme;
}

void setMasterBrightness(uint8_t brightness) {
    current_theme.manual_brightness = constrain(
        brightness,
        HARDWARE_MIN_SAFE_BRIGHTNESS,
        HARDWARE_MAX_SAFE_BRIGHTNESS
    );
    if (!current_theme.solar_sync) {
        current_theme.master_brightness = current_theme.manual_brightness;
        FastLED.setBrightness(current_theme.master_brightness);
        prefs.putUChar("br", current_theme.master_brightness);
    }
    prefs.putUChar("man_br", current_theme.manual_brightness);
}

bool applyThemeIfPresent(JsonVariantConst theme_var) {
    if (theme_var.isNull() || !theme_var.is<JsonObjectConst>()) {
        return false;
    }

    JsonObjectConst theme = theme_var.as<JsonObjectConst>();
    uint16_t incoming_ver = theme["version"] | 0;
    bool incoming_solar_sync = !theme["solar_sync"].isNull() ? theme["solar_sync"].as<bool>() : current_theme.solar_sync;

    // 1. In-band volatile Circadian Solar-Sync RAM update:
    // When solar sync is active, smooth circadian brightness adjustments sent in-band
    // are applied directly to FastLED in volatile RAM WITHOUT writing to NVS flash memory.
    // This preserves the 100,000-cycle NVS flash memory endurance indefinitely.
    if (incoming_solar_sync) {
        uint8_t eff_br = theme["effective_brightness"] | (theme["brightness"] | current_theme.master_brightness);
        eff_br = constrain(eff_br, HARDWARE_MIN_SAFE_BRIGHTNESS, HARDWARE_MAX_SAFE_BRIGHTNESS);
        if (eff_br != current_theme.master_brightness) {
            current_theme.master_brightness = eff_br;
            FastLED.setBrightness(current_theme.master_brightness);
            Serial.printf("[THEME] Solar circadian brightness RAM update: %u\n", current_theme.master_brightness);
#if !defined(BOARD_MODE_BLE)
            telnetPrintf("[THEME] Solar circadian brightness RAM update: %u\r\n", current_theme.master_brightness);
#endif
        }
    }

    // 2. Zero-lag dirty checking: if version is older or same as cached version, exit immediately!
    // Flash commits only happen when user modifies preferences on the dashboard.
    if (incoming_ver != 0 && incoming_ver <= current_theme.version) {
        return false;
    }

    // 3. New version arrived (User changed configuration via Web Dashboard or CLI):
    current_theme.solar_sync = incoming_solar_sync;

    if (!theme["brightness"].isNull()) {
        uint8_t raw_br = theme["brightness"].as<uint8_t>();
        current_theme.manual_brightness = constrain(
            raw_br,
            HARDWARE_MIN_SAFE_BRIGHTNESS,
            HARDWARE_MAX_SAFE_BRIGHTNESS
        );
    }

    if (current_theme.solar_sync) {
        uint8_t eff_br = theme["effective_brightness"] | current_theme.manual_brightness;
        current_theme.master_brightness = constrain(
            eff_br,
            HARDWARE_MIN_SAFE_BRIGHTNESS,
            HARDWARE_MAX_SAFE_BRIGHTNESS
        );
    } else {
        current_theme.master_brightness = current_theme.manual_brightness;
    }
    FastLED.setBrightness(current_theme.master_brightness);

    if (!theme["landing_head"].isNull()) {
        current_theme.landing_head_rgb = parseHexColor(theme["landing_head"], current_theme.landing_head_rgb);
    }
    if (!theme["landing_tail"].isNull()) {
        current_theme.landing_tail_rgb = parseHexColor(theme["landing_tail"], current_theme.landing_tail_rgb);
    }
    if (!theme["takeoff_head"].isNull()) {
        current_theme.takeoff_head_rgb = parseHexColor(theme["takeoff_head"], current_theme.takeoff_head_rgb);
    }
    if (!theme["takeoff_tail"].isNull()) {
        current_theme.takeoff_tail_rgb = parseHexColor(theme["takeoff_tail"], current_theme.takeoff_tail_rgb);
    }
    if (!theme["idle_threshold"].isNull()) {
        current_theme.idle_threshold_rgb = parseHexColor(theme["idle_threshold"], current_theme.idle_threshold_rgb);
    }
    if (!theme["conflict_warn"].isNull()) {
        current_theme.conflict_warn_rgb = parseHexColor(theme["conflict_warn"], current_theme.conflict_warn_rgb);
    }

    current_theme.version = incoming_ver > 0 ? incoming_ver : (current_theme.version + 1);

    // Commit to NVS Flash
    prefs.putUShort("ver", current_theme.version);
    prefs.putUChar("br", current_theme.master_brightness);
    prefs.putUChar("man_br", current_theme.manual_brightness);
    prefs.putBool("solar", current_theme.solar_sync);
    prefs.putUInt("l_head", current_theme.landing_head_rgb);
    prefs.putUInt("l_tail", current_theme.landing_tail_rgb);
    prefs.putUInt("t_head", current_theme.takeoff_head_rgb);
    prefs.putUInt("t_tail", current_theme.takeoff_tail_rgb);
    prefs.putUInt("idle", current_theme.idle_threshold_rgb);
    prefs.putUInt("conf", current_theme.conflict_warn_rgb);

    Serial.printf("[THEME] Applied & Saved v%u: Brightness=%u (SolarSync=%d), L_Tail=0x%06X, T_Tail=0x%06X\n",
                  current_theme.version, current_theme.master_brightness, current_theme.solar_sync ? 1 : 0,
                  current_theme.landing_tail_rgb, current_theme.takeoff_tail_rgb);

#if !defined(BOARD_MODE_BLE)
    telnetPrintf("[THEME] Applied & Saved v%u: Brightness=%u (SolarSync=%d), L_Tail=0x%06X, T_Tail=0x%06X\r\n",
                 current_theme.version, current_theme.master_brightness, current_theme.solar_sync ? 1 : 0,
                 current_theme.landing_tail_rgb, current_theme.takeoff_tail_rgb);
#endif

    return true;
}

