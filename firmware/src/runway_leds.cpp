#include "runway_leds.h"

static CRGB leds[NUM_LEDS];
static bool has_had_event = false;

// Precomputed normalized physical distance along runway centerline (0.0f to 1.0f)
// Derived directly from the Altium Pick and Place CAD coordinates.
// Guarantees constant linear mm/s velocity down every runway regardless of LED pitch or crossing gaps.

static const float norm_12R_30L[26] = {
    0.0000f, 0.0402f, 0.0804f, 0.1206f, 0.1600f, 0.2002f, 0.2404f, 0.2806f,
    0.3199f, 0.3601f, 0.4003f, 0.4405f, 0.4799f, 0.5201f, 0.5603f, 0.6005f,
    0.6399f, 0.6801f, 0.7203f, 0.7605f, 0.7998f, 0.8400f, 0.8802f, 0.9204f,
    0.9598f, 1.0000f
};

static const float norm_30R_12L[20] = {
    0.0000f, 0.0529f, 0.1057f, 0.1586f, 0.2104f, 0.2632f, 0.3161f, 0.3689f,
    0.4207f, 0.4736f, 0.5264f, 0.5793f, 0.6311f, 0.6839f, 0.7368f, 0.7896f,
    0.8414f, 0.8943f, 0.9471f, 1.0000f
};

static const float norm_22_4[18] = {
    0.0000f, 0.0508f, 0.1658f, 0.2167f, 0.2675f, 0.3184f, 0.3704f, 0.4879f,
    0.5387f, 0.5895f, 0.6404f, 0.6924f, 0.7445f, 0.7954f, 0.8462f, 0.8970f,
    0.9491f, 1.0000f
};

static const float norm_35_17[20] = {
    0.0000f, 0.0463f, 0.0927f, 0.1386f, 0.1850f, 0.3524f, 0.3988f, 0.4451f,
    0.4914f, 0.5378f, 0.5837f, 0.6301f, 0.6764f, 0.7227f, 0.7687f, 0.8150f,
    0.8614f, 0.9077f, 0.9537f, 1.0000f
};

// 4 Physical Runway Segments according to PCB Altium Pick and Place:
// 1. Runway 12R - 30L: U2 - U27 (26 LEDs). Crossing at U12 (idx 10)
// 2. Runway 30R - 12L: U28 - U47 (20 LEDs). Crossing at U41 (idx 39)
// 3. Runway 22 - 4:   U48 - U65 (18 LEDs)
// 4. Runway 35 - 17:  U66 - U85 (20 LEDs)
static RunwaySegment runways[4] = {
    {"12R", "30L", RW_12R_30L_START, RW_12R_30L_END, RW_12R_30L_CROSSING, RW_STATE_IDLE, false, 0.0f, 0.006f, false, false, 0.0f, norm_12R_30L},
    {"30R", "12L", RW_30R_12L_START, RW_30R_12L_END, RW_30R_12L_CROSSING, RW_STATE_IDLE, true,  0.0f, 0.006f, false, true,  0.0f, norm_30R_12L},
    {"22",  "4",   RW_22_4_START,    RW_22_4_END,    -1,                   RW_STATE_IDLE, true,  0.0f, 0.006f, false, true,  0.0f, norm_22_4},
    {"35",  "17",  RW_35_17_START,   RW_35_17_END,   -1,                   RW_STATE_IDLE, true,  0.0f, 0.006f, false, true,  0.0f, norm_35_17}
};

static uint32_t last_frame_time = 0;

bool hasHadEventOccurred() {
    return has_had_event;
}

#if defined(BOARD_MODE_DEVKIT)
static void setDevKitRgb(uint8_t r, uint8_t g, uint8_t b) {
    // Drive both GPIO 48 (DevKitC-1 v1.0) and GPIO 38 (DevKitC-1 v1.1 / clones)
    // using native ESP32-S3 hardware WS2812 pulse stream
    neopixelWrite(48, r, g, b);
    neopixelWrite(38, r, g, b);
#ifdef RGB_BUILTIN
    neopixelWrite(RGB_BUILTIN, r, g, b);
#endif

    leds[0] = CRGB(r, g, b);
    FastLED.show();
}
#endif

void initLeds() {
#if defined(BOARD_MODE_DEVKIT)
    // Enable RGB power-gate pin if present on this board variant
    pinMode(47, OUTPUT);
    digitalWrite(47, HIGH);

    FastLED.addLeds<WS2812B, PIN_LED_DATA, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(MAX_LED_BRIGHTNESS);

    // Immediately clear uninitialized power-on white glow
    setDevKitRgb(0, 0, 0);
    delay(50);

    // Quick POST startup color sequence: Green -> Orange -> Blue -> Off
    setDevKitRgb(0, 180, 0);       // Green
    delay(250);
    setDevKitRgb(255, 120, 0);     // Orange (Landing preview)
    delay(250);
    setDevKitRgb(0, 100, 255);     // Blue (Takeoff preview)
    delay(250);
    setDevKitRgb(0, 0, 0);         // Off
#else
    FastLED.addLeds<WS2812B, PIN_LED_DATA, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(MAX_LED_BRIGHTNESS);
    FastLED.clear();
    FastLED.show();

    // Startup sweep across all 4 runways
    for (int i = 0; i < NUM_LEDS; i++) {
        leds[i] = CRGB(20, 40, 80);
        FastLED.show();
        delay(8);
        leds[i] = CRGB::Black;
    }
    FastLED.show();
#endif
}

void resetAllRunwaysToIdle() {
    for (int i = 0; i < 4; i++) {
        runways[i].state = RW_STATE_IDLE;
    }
}

static bool isSameFlow(const char* rwA, const char* rwB) {
    if (!rwA || !rwB) return false;
    if (strcmp(rwA, rwB) == 0) return true;
    // Parallel NW
    if ((strcmp(rwA, "30R") == 0 || strcmp(rwA, "30L") == 0) &&
        (strcmp(rwB, "30R") == 0 || strcmp(rwB, "30L") == 0)) return true;
    // Parallel SE
    if ((strcmp(rwA, "12R") == 0 || strcmp(rwA, "12L") == 0) &&
        (strcmp(rwB, "12R") == 0 || strcmp(rwB, "12L") == 0)) return true;
    return false;
}

void setRunwayState(const char* runway_name, const char* action_str, float progress) {
    if (!runway_name || !action_str) return;

    RunwayOpState op = RW_STATE_IDLE;
    if (strcmp(action_str, "LANDING") == 0) {
        op = RW_STATE_LANDING;
    } else if (strcmp(action_str, "TAKEOFF") == 0) {
        op = RW_STATE_TAKEOFF;
    }

    if (op == RW_STATE_IDLE) return;

    has_had_event = true;

    // Enforce Airport Operational Flow Constraint:
    // Only 1 single runway OR 1 parallel pair (12L/12R or 30R/30L) is active at once.
    for (int j = 0; j < 4; j++) {
        RunwaySegment& other = runways[j];
        const char* active_other = other.moving_forward ? other.name_start : other.name_end;
        if (other.state != RW_STATE_IDLE && !isSameFlow(runway_name, active_other)) {
            other.state = RW_STATE_IDLE;
            other.comet_norm = 0.0f;
        }
        const char* recent_other = other.recent_forward ? other.name_start : other.name_end;
        if (!isSameFlow(runway_name, recent_other)) {
            other.was_recently_used = false;
        }
    }

    for (int i = 0; i < 4; i++) {
        RunwaySegment& r = runways[i];

        // Aircraft matching start threshold (e.g. 12R, 30R, 22, 35)
        if (strcmp(r.name_start, runway_name) == 0) {
            bool state_changed = (r.state != op || !r.moving_forward);
            r.state = op;
            r.moving_forward = true; // Rollout moves from start_idx -> end_idx
            r.speed_norm = 0.006f;   // 5.0s constant physical linear speed
            r.was_recently_used = true;
            r.recent_forward = true;
            if (state_changed) {
                r.comet_norm = 0.0f; // Start comet at touchdown/takeoff threshold
            }
            return;
        }
        // Aircraft matching end threshold (e.g. 30L, 12L, 4, 17)
        else if (strcmp(r.name_end, runway_name) == 0) {
            bool state_changed = (r.state != op || r.moving_forward);
            r.state = op;
            r.moving_forward = false; // Rollout moves from end_idx -> start_idx
            r.speed_norm = 0.006f;    // 5.0s constant physical linear speed
            r.was_recently_used = true;
            r.recent_forward = false;
            if (state_changed) {
                r.comet_norm = 0.0f; // Start comet at touchdown/takeoff threshold
            }
            return;
        }
    }
}

static void addLedColor(uint8_t index, CRGB color) {
    if (index >= NUM_LEDS) return;
    leds[index].r = qadd8(leds[index].r, color.r);
    leds[index].g = qadd8(leds[index].g, color.g);
    leds[index].b = qadd8(leds[index].b, color.b);
}

void getRecentlyActiveRunwaysStr(char* out_buf, size_t buf_len) {
    if (!out_buf || buf_len == 0) return;
    out_buf[0] = '\0';
    if (!has_had_event) {
        return;
    }
    bool first = true;
    for (int i = 0; i < 4; i++) {
        if (runways[i].was_recently_used) {
            const char* rw = runways[i].recent_forward ? runways[i].name_start : runways[i].name_end;
            if (!first) {
                strncat(out_buf, ", ", buf_len - strlen(out_buf) - 1);
            }
            strncat(out_buf, rw, buf_len - strlen(out_buf) - 1);
            first = false;
        }
    }
    if (first) {
        strncpy(out_buf, "None", buf_len - 1);
    }
}

void renderRunwayAnimations() {
    uint32_t now = millis();
    if (now - last_frame_time < 30) { // ~33 FPS animation loop
        return;
    }
    last_frame_time = now;

#if defined(BOARD_MODE_DEVKIT)
    // DevKit Test Mode:
    // User requested: Single onboard RGB LED indicates landing (Orange) or takeoff (Blue)
    RunwayOpState active_op = RW_STATE_IDLE;
    for (int r = 0; r < 4; r++) {
        if (runways[r].state == RW_STATE_LANDING) {
            active_op = RW_STATE_LANDING;
            break;
        } else if (runways[r].state == RW_STATE_TAKEOFF) {
            active_op = RW_STATE_TAKEOFF;
        }
    }

    if (active_op == RW_STATE_LANDING) {
        // Landing event: Orange / Amber
        setDevKitRgb(255, 120, 0);
    } else if (active_op == RW_STATE_TAKEOFF) {
        // Takeoff event: Blue
        setDevKitRgb(0, 100, 255);
    } else {
        // Idle mode: gentle amber breathing pulse if traffic occurred, else dim standby
        if (has_had_event) {
            float breath = (sin(now / 500.0f) + 1.0f) * 0.5f; // 0.0 to 1.0
            uint8_t br = (uint8_t)(breath * 20.0f) + 2;
            setDevKitRgb(br, (br * 6) / 10, 0); // Warm idle amber breathing
        } else {
            setDevKitRgb(0, 2, 8); // Dim standby indicator
        }
    }
    return;
#else
    FastLED.clear();

    // Check if any flight is actively operating
    bool has_active_flight = false;
    for (int r = 0; r < 4; r++) {
        if (runways[r].state != RW_STATE_IDLE) {
            has_active_flight = true;
            break;
        }
    }

    const float TAIL_NORM = 0.25f; // Comet tail covers 25% of runway physical length

    for (int r = 0; r < 4; r++) {
        RunwaySegment& seg = runways[r];
        int count = (seg.end_idx - seg.start_idx) + 1;

        if (seg.state == RW_STATE_IDLE) {
            // Ambient idle runway: dim boundary threshold lights at each end
            leds[seg.start_idx] = CRGB(6, 9, 14);
            leds[seg.end_idx]   = CRGB(6, 9, 14);

            if (seg.crossing_idx >= 0) {
                leds[seg.crossing_idx] = CRGB(4, 5, 8);
            }

            // IDLE YELLOW STROBE:
            // Runs ONLY AFTER at least one flight event has occurred since startup
            if (has_had_event && !has_active_flight && seg.was_recently_used) {
                const float IDLE_TAIL = 0.20f;
                seg.idle_strobe_norm += 0.003f; // ~10s slow relaxing glide
                if (seg.idle_strobe_norm >= 1.35f) {
                    seg.idle_strobe_norm = 0.0f; // Loop with pause
                }

                for (int i = 0; i < count; i++) {
                    uint8_t strip_idx = seg.start_idx + i;
                    float x = seg.recent_forward ? seg.led_norm_pos[i] : (1.0f - seg.led_norm_pos[i]);
                    float dist_behind = seg.idle_strobe_norm - x;

                    if (dist_behind >= 0.0f && dist_behind <= IDLE_TAIL) {
                        float u = dist_behind / IDLE_TAIL; // 0.0 at head -> 1.0 at tail end
                        CRGB y_color;
                        if (u < 0.15f) {
                            y_color = CRGB(255, 190, 0); // Warm amber head
                        } else if (u < 0.50f) {
                            float f = (u - 0.15f) / 0.35f;
                            y_color = blend(CRGB(255, 190, 0), CRGB(160, 90, 0), (uint8_t)(f * 255));
                        } else {
                            float f = (u - 0.50f) / 0.50f;
                            y_color = blend(CRGB(160, 90, 0), CRGB::Black, (uint8_t)(f * 255));
                        }
                        addLedColor(strip_idx, y_color);
                    }
                }
            }
        }
        else {
            // ACTIVE FLIGHT COMET (Landing or Takeoff at constant 5.0s physical pace)
            seg.comet_norm += seg.speed_norm;
            if (seg.comet_norm >= 1.35f) {
                seg.comet_norm = 0.0f;
            }

            for (int i = 0; i < count; i++) {
                uint8_t strip_idx = seg.start_idx + i;
                float x = seg.moving_forward ? seg.led_norm_pos[i] : (1.0f - seg.led_norm_pos[i]);
                float dist_behind = seg.comet_norm - x;

                if (dist_behind >= 0.0f && dist_behind <= TAIL_NORM) {
                    float u = dist_behind / TAIL_NORM; // 0.0 at head -> 1.0 at tail end
                    CRGB led_color;

                    if (seg.state == RW_STATE_LANDING) {
                        // Landing Comet: Crisp White Head + Emerald / Cyan Rollout Tail
                        CRGB head_col = CRGB(255, 255, 255);
                        CRGB mid_col  = CRGB(60, 240, 140);
                        if (u < 0.15f) {
                            led_color = head_col;
                        } else if (u < 0.50f) {
                            float f = (u - 0.15f) / 0.35f;
                            led_color = blend(head_col, mid_col, (uint8_t)(f * 255));
                        } else {
                            float f = (u - 0.50f) / 0.50f;
                            led_color = blend(mid_col, CRGB::Black, (uint8_t)(f * 255));
                        }
                    } else {
                        // Takeoff Comet: Warm White Head + Amber / Gold Flame Tail
                        CRGB head_col = CRGB(255, 255, 220);
                        CRGB mid_col  = CRGB(255, 140, 0);
                        if (u < 0.15f) {
                            led_color = head_col;
                        } else if (u < 0.50f) {
                            float f = (u - 0.15f) / 0.35f;
                            led_color = blend(head_col, mid_col, (uint8_t)(f * 255));
                        } else {
                            float f = (u - 0.50f) / 0.50f;
                            led_color = blend(mid_col, CRGB::Black, (uint8_t)(f * 255));
                        }
                    }

                    addLedColor(strip_idx, led_color);
                }
            }

            // Illuminate runway threshold base
            if (seg.moving_forward) {
                addLedColor(seg.start_idx, (seg.state == RW_STATE_LANDING) ? CRGB(0, 40, 20) : CRGB(40, 20, 0));
            } else {
                addLedColor(seg.end_idx,   (seg.state == RW_STATE_LANDING) ? CRGB(0, 40, 20) : CRGB(40, 20, 0));
            }

            if (seg.crossing_idx >= 0) {
                addLedColor(seg.crossing_idx, CRGB(10, 8, 4));
            }
        }
    }

    FastLED.show();
#endif
}

void showConnectionStatusLed(bool connected) {
#if defined(BOARD_MODE_DEVKIT)
    if (!connected) {
        bool blink = (millis() % 1000 < 500);
        setDevKitRgb(blink ? 120 : 0, 0, 0);
    }
#else
    if (!connected) {
        leds[0] = (millis() % 1000 < 500) ? CRGB(180, 0, 0) : CRGB::Black;
        FastLED.show();
    }
#endif
}
