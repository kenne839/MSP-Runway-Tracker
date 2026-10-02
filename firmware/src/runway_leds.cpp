#include "runway_leds.h"

static CRGB leds[NUM_LEDS];

static RunwayControl runway_zones[4] = {
    {"12L", "30R", RW_12L_30R_START, RW_12L_30R_END, RW_STATE_IDLE, false, 0.0f},
    {"12R", "30L", RW_12R_30L_START, RW_12R_30L_END, RW_STATE_IDLE, false, 0.0f},
    {"4",   "22",  RW_4_22_START,    RW_4_22_END,    RW_STATE_IDLE, false, 0.0f},
    {"17",  "35",  RW_17_35_START,   RW_17_35_END,   RW_STATE_IDLE, false, 0.0f}
};

static uint32_t last_anim_tick = 0;
static uint8_t anim_step = 0;

void initLeds() {
    FastLED.addLeds<WS2812B, PIN_LED_DATA, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(MAX_LED_BRIGHTNESS);
    FastLED.clear();
    FastLED.show();

    // Power-on self test sweep
    for (int i = 0; i < NUM_LEDS; i++) {
        leds[i] = CRGB::DarkBlue;
        FastLED.show();
        delay(10);
        leds[i] = CRGB::Black;
    }
    FastLED.show();
}

void resetAllRunwaysToIdle() {
    for (int i = 0; i < 4; i++) {
        runway_zones[i].current_state = RW_STATE_IDLE;
        runway_zones[i].progress = 0.0f;
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
        if (strcmp(runway_zones[i].name_low, runway_name) == 0) {
            runway_zones[i].current_state = op;
            runway_zones[i].active_on_high = false;
            runway_zones[i].progress = progress;
            return;
        } else if (strcmp(runway_zones[i].name_high, runway_name) == 0) {
            runway_zones[i].current_state = op;
            runway_zones[i].active_on_high = true;
            runway_zones[i].progress = progress;
            return;
        }
    }
}

void renderRunwayAnimations() {
    uint32_t now = millis();
    if (now - last_anim_tick < 35) { // ~30 FPS animation update rate
        return;
    }
    last_anim_tick = now;
    anim_step++;

    FastLED.clear();

    for (int z = 0; z < 4; z++) {
        RunwayControl& rc = runway_zones[z];
        uint8_t count = (rc.end_idx - rc.start_idx) + 1;

        if (rc.current_state == RW_STATE_IDLE) {
            // Ambient idle runway: dim runway boundary threshold lights
            leds[rc.start_idx] = CRGB(8, 12, 18);
            leds[rc.end_idx]   = CRGB(8, 12, 18);
        }
        else if (rc.current_state == RW_STATE_LANDING) {
            // Landing Sequence: Running approach light bar sweeping towards touchdown point
            // Approach / Centerline color: Emerald Green / Cyan
            for (uint8_t i = 0; i < count; i++) {
                uint8_t led_idx = rc.active_on_high ? (rc.end_idx - i) : (rc.start_idx + i);
                
                // Dim centerline base illumination
                leds[led_idx] = CRGB(0, 15, 8);

                // Sweeping approach light chaser (Rabbit lights)
                uint8_t chase_pos = (anim_step / 2) % count;
                if (i == chase_pos) {
                    leds[led_idx] = CRGB(30, 255, 120); // Bright pulse
                } else if ((i + 1) % count == chase_pos || (i + count - 1) % count == chase_pos) {
                    leds[led_idx] = CRGB(0, 100, 40);  // Tail
                }
            }

            // Highlight estimated aircraft position along the runway
            uint8_t plane_pos = constrain((uint8_t)(rc.progress * (count - 1)), 0, count - 1);
            uint8_t plane_led = rc.active_on_high ? (rc.end_idx - plane_pos) : (rc.start_idx + plane_pos);
            leds[plane_led] = CRGB(255, 255, 255); // White strobe
        }
        else if (rc.current_state == RW_STATE_TAKEOFF) {
            // Takeoff Sequence: Accelerating amber/white roll along runway
            for (uint8_t i = 0; i < count; i++) {
                uint8_t led_idx = rc.active_on_high ? (rc.end_idx - i) : (rc.start_idx + i);
                
                // Base amber centerline
                leds[led_idx] = CRGB(18, 10, 0);

                // Outward roll pulse
                uint8_t roll_pos = (anim_step / 2) % count;
                if (i == roll_pos) {
                    leds[led_idx] = CRGB(255, 180, 0); // Bright Amber
                }
            }

            uint8_t plane_pos = constrain((uint8_t)(rc.progress * (count - 1)), 0, count - 1);
            uint8_t plane_led = rc.active_on_high ? (rc.end_idx - plane_pos) : (rc.start_idx + plane_pos);
            leds[plane_led] = CRGB(255, 220, 100);
        }
    }

    FastLED.show();
}

void showConnectionStatusLed(bool connected) {
    if (!connected) {
        // Flash first LED red if Wi-Fi or server connection is lost
        leds[0] = (millis() % 1000 < 500) ? CRGB::Red : CRGB::Black;
        FastLED.show();
    }
}
