#include "runway_leds.h"

static CRGB leds[NUM_LEDS];

// 4 Physical Runway Segments according to PCB Altium Pick and Place:
// 1. Runway 12R - 30L: U2 - U27 (26 LEDs). Crossing at U12 (idx 10)
// 2. Runway 30R - 12L: U28 - U47 (20 LEDs). Crossing at U41 (idx 39)
// 3. Runway 22 - 4:   U48 - U65 (18 LEDs)
// 4. Runway 35 - 17:  U66 - U85 (20 LEDs)
// Default configuration: 30R & 30L active (primary MSP flow)
static RunwaySegment runways[4] = {
    {"12R", "30L", RW_12R_30L_START, RW_12R_30L_END, RW_12R_30L_CROSSING, RW_STATE_IDLE, false, 0.0f, 0.15f, true,  false, 0.0f},
    {"30R", "12L", RW_30R_12L_START, RW_30R_12L_END, RW_30R_12L_CROSSING, RW_STATE_IDLE, true,  0.0f, 0.15f, true,  true,  0.0f},
    {"22",  "4",   RW_22_4_START,    RW_22_4_END,    -1,                   RW_STATE_IDLE, true,  0.0f, 0.15f, false, true,  0.0f},
    {"35",  "17",  RW_35_17_START,   RW_35_17_END,   -1,                   RW_STATE_IDLE, true,  0.0f, 0.15f, false, true,  0.0f}
};

static uint32_t last_frame_time = 0;

void initLeds() {
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
}

void resetAllRunwaysToIdle() {
    for (int i = 0; i < 4; i++) {
        runways[i].state = RW_STATE_IDLE;
    }
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

    for (int i = 0; i < 4; i++) {
        RunwaySegment& r = runways[i];

        // Aircraft matching start threshold (e.g. 12R, 30R, 22, 35)
        if (strcmp(r.name_start, runway_name) == 0) {
            bool state_changed = (r.state != op || !r.moving_forward);
            r.state = op;
            r.moving_forward = true; // Rollout moves from start_idx -> end_idx
            r.speed = 0.15f;         // 5.0s traverse speed for both landing and takeoff
            r.was_recently_used = true;
            r.recent_forward = true;
            if (state_changed) {
                r.comet_pos = 0.0f; // Start comet at touchdown/takeoff roll threshold
            }
            return;
        }
        // Aircraft matching end threshold (e.g. 30L, 12L, 4, 17)
        else if (strcmp(r.name_end, runway_name) == 0) {
            bool state_changed = (r.state != op || r.moving_forward);
            r.state = op;
            r.moving_forward = false; // Rollout moves from end_idx -> start_idx
            r.speed = 0.15f;          // 5.0s traverse speed for both landing and takeoff
            r.was_recently_used = true;
            r.recent_forward = false;
            if (state_changed) {
                r.comet_pos = 0.0f; // Start comet at touchdown/takeoff roll threshold
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
        strncpy(out_buf, "30R, 30L", buf_len - 1);
    }
}

void renderRunwayAnimations() {
    uint32_t now = millis();
    if (now - last_frame_time < 30) { // ~33 FPS animation loop
        return;
    }
    last_frame_time = now;

    FastLED.clear();

    // Check if any flight is actively operating
    bool has_active_flight = false;
    for (int r = 0; r < 4; r++) {
        if (runways[r].state != RW_STATE_IDLE) {
            has_active_flight = true;
            break;
        }
    }

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
            // When airport has no active flights, run a slow yellow strobe down recently active runways
            // as visual heartbeat indicating system is polling and showing current flow configuration
            if (!has_active_flight && seg.was_recently_used) {
                seg.idle_strobe_pos += 0.08f; // Slow relaxing strobe (~9s cycle with pause)
                if (seg.idle_strobe_pos >= (float)(count + 12)) {
                    seg.idle_strobe_pos = 0.0f; // Loop with pause
                }

                int s_pos = (int)seg.idle_strobe_pos;
                for (int k = 0; k <= 4; k++) {
                    int p = s_pos - k;
                    if (p < 0 || p >= count) continue;

                    uint8_t strip_idx = seg.recent_forward ? (seg.start_idx + p) : (seg.end_idx - p);
                    CRGB y_color;
                    if (k == 0)      y_color = CRGB(255, 190, 0); // Warm Amber Head
                    else if (k == 1) y_color = CRGB(160, 90, 0);  // Gold Tail
                    else if (k == 2) y_color = CRGB(70, 30, 0);   // Dim Amber
                    else             y_color = CRGB(20, 8, 0);    // Fade

                    addLedColor(strip_idx, y_color);
                }
            }
        }
        else {
            // ACTIVE FLIGHT COMET (Landing or Takeoff at 5.0s pace)
            seg.comet_pos += seg.speed;
            if (seg.comet_pos >= (float)(count + 6)) {
                seg.comet_pos = 0.0f;
            }

            int head_step = (int)seg.comet_pos;
            const int TAIL_LENGTH = 6;

            for (int k = 0; k <= TAIL_LENGTH; k++) {
                int pos = head_step - k;
                if (pos < 0 || pos >= count) continue;

                uint8_t strip_idx = seg.moving_forward 
                    ? (seg.start_idx + pos) 
                    : (seg.end_idx - pos);

                CRGB led_color;

                if (seg.state == RW_STATE_LANDING) {
                    // Landing Comet (5.0s traverse): Crisp White Head + Emerald / Cyan Tail
                    if (k == 0)      led_color = CRGB(255, 255, 255);
                    else if (k == 1) led_color = CRGB(80, 255, 180);
                    else if (k == 2) led_color = CRGB(0, 200, 100);
                    else if (k == 3) led_color = CRGB(0, 120, 50);
                    else if (k == 4) led_color = CRGB(0, 60, 25);
                    else             led_color = CRGB(0, 20, 10);
                }
                else {
                    // Takeoff Comet (5.0s traverse): Warm White Head + Amber / Gold Flame Tail
                    if (k == 0)      led_color = CRGB(255, 255, 220);
                    else if (k == 1) led_color = CRGB(255, 190, 0);
                    else if (k == 2) led_color = CRGB(255, 120, 0);
                    else if (k == 3) led_color = CRGB(200, 60, 0);
                    else if (k == 4) led_color = CRGB(120, 25, 0);
                    else             led_color = CRGB(40, 8, 0);
                }

                addLedColor(strip_idx, led_color);
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
}

void showConnectionStatusLed(bool connected) {
    if (!connected) {
        leds[0] = (millis() % 1000 < 500) ? CRGB(180, 0, 0) : CRGB::Black;
        FastLED.show();
    }
}
