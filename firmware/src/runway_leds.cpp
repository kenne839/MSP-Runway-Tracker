#include "runway_leds.h"

static CRGB leds[NUM_LEDS];

// 4 Physical Runway Segments according to PCB netlist and layout:
// 1. Runway 12R - 30L: U2 - U27 (26 LEDs). Crossing at U12 (idx 10)
// 2. Runway 30R - 12L: U28 - U47 (20 LEDs). Crossing at U41 (idx 39)
// 3. Runway 22 - 4:   U48 - U65 (18 LEDs)
// 4. Runway 35 - 17:  U66 - U85 (20 LEDs)
static RunwaySegment runways[4] = {
    {"12R", "30L", RW_12R_30L_START, RW_12R_30L_END, RW_12R_30L_CROSSING, RW_STATE_IDLE, true,  0.0f, 0.11f},
    {"30R", "12L", RW_30R_12L_START, RW_30R_12L_END, RW_30R_12L_CROSSING, RW_STATE_IDLE, true,  0.0f, 0.11f},
    {"22",  "4",   RW_22_4_START,    RW_22_4_END,    -1,                   RW_STATE_IDLE, true,  0.0f, 0.11f},
    {"35",  "17",  RW_35_17_START,   RW_35_17_END,   -1,                   RW_STATE_IDLE, true,  0.0f, 0.11f}
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

    for (int i = 0; i < 4; i++) {
        RunwaySegment& r = runways[i];

        // Aircraft matching start threshold (e.g. 12R, 30R, 22, 35)
        if (strcmp(r.name_start, runway_name) == 0) {
            bool state_changed = (r.state != op || !r.moving_forward);
            r.state = op;
            r.moving_forward = true; // Rollout moves from start_idx -> end_idx
            // Slower realistic rollout: ~7s for landing, ~4.4s for takeoff
            r.speed = (op == RW_STATE_TAKEOFF) ? 0.18f : 0.11f;
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
            // Slower realistic rollout: ~7s for landing, ~4.4s for takeoff
            r.speed = (op == RW_STATE_TAKEOFF) ? 0.18f : 0.11f;
            if (state_changed) {
                r.comet_pos = 0.0f; // Start comet at touchdown/takeoff roll threshold
            }
            return;
        }
    }
}

// Blends color onto LED with additive saturation clamp
static void addLedColor(uint8_t index, CRGB color) {
    if (index >= NUM_LEDS) return;
    leds[index].r = qadd8(leds[index].r, color.r);
    leds[index].g = qadd8(leds[index].g, color.g);
    leds[index].b = qadd8(leds[index].b, color.b);
}

void renderRunwayAnimations() {
    uint32_t now = millis();
    if (now - last_frame_time < 30) { // Target ~33 FPS animation loop
        return;
    }
    last_frame_time = now;

    FastLED.clear();

    for (int r = 0; r < 4; r++) {
        RunwaySegment& seg = runways[r];
        int count = (seg.end_idx - seg.start_idx) + 1;

        if (seg.state == RW_STATE_IDLE) {
            // Ambient idle runway: dim boundary threshold lights at each end
            leds[seg.start_idx] = CRGB(6, 9, 14);
            leds[seg.end_idx]   = CRGB(6, 9, 14);

            // Subtle indicator at runway crossings (U12 / U41)
            if (seg.crossing_idx >= 0) {
                leds[seg.crossing_idx] = CRGB(4, 5, 8);
            }
        }
        else {
            // Advance Comet Position
            seg.comet_pos += seg.speed;
            if (seg.comet_pos >= (float)(count + 6)) { // Allow tail to clear runway before loop
                seg.comet_pos = 0.0f; // Loop comet back to touchdown/takeoff threshold
            }

            int head_step = (int)seg.comet_pos;
            const int TAIL_LENGTH = 6;

            for (int k = 0; k <= TAIL_LENGTH; k++) {
                int pos = head_step - k;
                if (pos < 0 || pos >= count) continue;

                // Map relative position to physical LED strip index based on runway direction:
                // moving_forward (start -> end) or reverse (end -> start)
                uint8_t strip_idx = seg.moving_forward 
                    ? (seg.start_idx + pos) 
                    : (seg.end_idx - pos);

                CRGB led_color;

                if (seg.state == RW_STATE_LANDING) {
                    // Landing Comet:
                    // Head: Strobe White.
                    // Tail: Fading Emerald Green / Cyan glide-path rollout
                    if (k == 0) {
                        led_color = CRGB(255, 255, 255); // Crisp White Touchdown / Lead Strobe
                    } else if (k == 1) {
                        led_color = CRGB(80, 255, 180);  // Bright Mint / Cyan
                    } else if (k == 2) {
                        led_color = CRGB(0, 200, 100);   // Emerald Green
                    } else if (k == 3) {
                        led_color = CRGB(0, 120, 50);    // Medium Green
                    } else if (k == 4) {
                        led_color = CRGB(0, 60, 25);     // Dark Green
                    } else {
                        led_color = CRGB(0, 20, 10);     // Dim tail fade
                    }
                }
                else { // RW_STATE_TAKEOFF
                    // Takeoff Comet:
                    // Head: Piercing White / High-energy Strobe.
                    // Tail: Accelerated Amber / Gold / Orange afterburner roll
                    if (k == 0) {
                        led_color = CRGB(255, 255, 220); // Warm White Strobe Head
                    } else if (k == 1) {
                        led_color = CRGB(255, 190, 0);   // Bright Gold
                    } else if (k == 2) {
                        led_color = CRGB(255, 120, 0);   // Amber
                    } else if (k == 3) {
                        led_color = CRGB(200, 60, 0);    // Warm Orange
                    } else if (k == 4) {
                        led_color = CRGB(120, 25, 0);    // Deep Orange
                    } else {
                        led_color = CRGB(40, 8, 0);      // Dim red-orange tail fade
                    }
                }

                addLedColor(strip_idx, led_color);
            }

            // Always illuminate runway threshold indicators so runway orientation remains clear
            if (seg.moving_forward) {
                // Threshold where roll originated
                addLedColor(seg.start_idx, (seg.state == RW_STATE_LANDING) ? CRGB(0, 40, 20) : CRGB(40, 20, 0));
            } else {
                addLedColor(seg.end_idx,   (seg.state == RW_STATE_LANDING) ? CRGB(0, 40, 20) : CRGB(40, 20, 0));
            }

            // Crossing warning highlight: if crossing index exists and comet passes through
            if (seg.crossing_idx >= 0) {
                addLedColor(seg.crossing_idx, CRGB(10, 8, 4));
            }
        }
    }

    FastLED.show();
}

void showConnectionStatusLed(bool connected) {
    if (!connected) {
        // Blink first LED red if network disconnected
        leds[0] = (millis() % 1000 < 500) ? CRGB(180, 0, 0) : CRGB::Black;
        FastLED.show();
    }
}
