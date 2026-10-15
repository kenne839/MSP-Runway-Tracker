#include "runway_leds.h"
#include "theme_manager.h"

#if !defined(BOARD_MODE_BLE)
#include "network_client.h"
#endif

static SemaphoreHandle_t runway_mutex = nullptr;
static TaskHandle_t led_task_handle = nullptr;

#define LOCK_RUNWAYS()    if (runway_mutex) xSemaphoreTake(runway_mutex, portMAX_DELAY)
#define UNLOCK_RUNWAYS()  if (runway_mutex) xSemaphoreGive(runway_mutex)

struct RunwayLockGuard {
    RunwayLockGuard() { LOCK_RUNWAYS(); }
    ~RunwayLockGuard() { UNLOCK_RUNWAYS(); }
};

static CRGB leds[NUM_LEDS];
static bool has_had_event = true; // Default true so the prevailing runway (30R) slowly strobes from power-on

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
    {"30R", "12L", RW_30R_12L_START, RW_30R_12L_END, RW_30R_12L_CROSSING, RW_STATE_IDLE, true,  0.0f, 0.006f, true,  true,  0.0f, norm_30R_12L},
    {"22",  "4",   RW_22_4_START,    RW_22_4_END,    -1,                   RW_STATE_IDLE, true,  0.0f, 0.006f, false, true,  0.0f, norm_22_4},
    {"35",  "17",  RW_35_17_START,   RW_35_17_END,   -1,                   RW_STATE_IDLE, true,  0.0f, 0.006f, false, true,  0.0f, norm_35_17}
};

static uint32_t last_frame_time = 0;

bool hasHadEventOccurred() {
    return has_had_event;
}

// Precomputed CIE 1931 Perceptual Lightness Lookup Table (0-255)
// Maps linear duty cycles to human logarithmic luminance perception, eliminating steps at low duty cycles.
static const uint8_t PROGMEM cie1931[256] = {
    0,   0,   0,   0,   0,   1,   1,   1,   1,   1,   1,   1,   1,   1,   2,   2,
    2,   2,   2,   2,   3,   3,   3,   3,   4,   4,   4,   4,   5,   5,   5,   5,
    6,   6,   6,   7,   7,   7,   8,   8,   8,   9,   9,  10,  10,  10,  11,  11,
   12,  12,  13,  13,  14,  14,  15,  15,  16,  16,  17,  18,  18,  19,  19,  20,
   21,  21,  22,  23,  23,  24,  25,  26,  26,  27,  28,  29,  30,  30,  31,  32,
   33,  34,  35,  36,  37,  38,  39,  40,  41,  42,  43,  44,  45,  46,  47,  48,
   50,  51,  52,  53,  54,  56,  57,  58,  59,  61,  62,  63,  65,  66,  68,  69,
   71,  72,  74,  75,  77,  78,  80,  82,  83,  85,  87,  88,  90,  92,  94,  96,
   98, 100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 121, 123, 125, 127, 130,
  132, 134, 137, 139, 142, 144, 147, 149, 152, 154, 157, 160, 162, 165, 168, 171,
  173, 176, 179, 182, 185, 188, 191, 194, 197, 200, 203, 207, 210, 213, 216, 220,
  223, 227, 230, 234, 237, 241, 244, 248, 251, 255
};

static inline uint8_t applyCieGamma(uint8_t val) {
    return pgm_read_byte(&cie1931[val]);
}

#if defined(BOARD_MODE_DEVKIT)
static void setDevKitRgb(uint8_t r, uint8_t g, uint8_t b) {
    uint8_t mb = getThemeConfig().master_brightness;
    if (mb < 40) mb = 40; // Ensure visible brightness on desktop

    uint8_t sr = (uint8_t)(((uint16_t)r * mb + 127) / 255);
    uint8_t sg = (uint8_t)(((uint16_t)g * mb + 127) / 255);
    uint8_t sb = (uint8_t)(((uint16_t)b * mb + 127) / 255);

    // Native hardware WS2812 driver for ESP32-S3:
    // GPIO 48 (DevKitC-1 v1.0), GPIO 38 (DevKitC-1 v1.1 / clones), GPIO 21 (Zero/Mini)
    neopixelWrite(48, sr, sg, sb);
    neopixelWrite(38, sr, sg, sb);
    neopixelWrite(21, sr, sg, sb);
#ifdef RGB_BUILTIN
    neopixelWrite(RGB_BUILTIN, sr, sg, sb);
#endif

    leds[0] = CRGB(sr, sg, sb);
}
#endif

void initLeds() {
    if (runway_mutex == nullptr) {
        runway_mutex = xSemaphoreCreateMutex();
    }
    uint8_t init_brightness = getThemeConfig().master_brightness;
#if defined(BOARD_MODE_DEVKIT)
    // Enable RGB power-gate pin (GPIO 47 on DevKitC-1 v1.0, GPIO 33 on some clones)
    pinMode(47, OUTPUT);
    digitalWrite(47, HIGH);
    pinMode(33, OUTPUT);
    digitalWrite(33, HIGH);

    // Clear uninitialized power-on state
    setDevKitRgb(0, 0, 0);
    delay(50);

    // Quick POST startup color sequence: Green -> Orange -> Blue -> Off
    setDevKitRgb(0, 180, 0);       // Green
    delay(200);
    setDevKitRgb(255, 120, 0);     // Orange (Landing preview)
    delay(200);
    setDevKitRgb(0, 100, 255);     // Blue (Takeoff preview)
    delay(200);
    setDevKitRgb(0, 0, 0);         // Off
#else
    FastLED.addLeds<WS2812B, PIN_LED_DATA, GRB>(leds, NUM_LEDS);
    FastLED.setMaxPowerInVoltsAndMilliamps(5, 1200);
    FastLED.setBrightness(init_brightness);
    FastLED.setDither(DISABLE_DITHER);
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

int getRunwayIndex(const char* runway_name) {
    if (!runway_name) return -1;
    for (int i = 0; i < 4; i++) {
        if (strcmp(runways[i].name_start, runway_name) == 0 ||
            strcmp(runways[i].name_end, runway_name) == 0) {
            return i;
        }
    }
    return -1;
}

void setRunwayIdle(int runway_idx) {
    RunwayLockGuard lock;
    if (runway_idx >= 0 && runway_idx < 4) {
        runways[runway_idx].state = RW_STATE_IDLE;
    }
}

void resetAllRunwaysToIdle() {
    RunwayLockGuard lock;
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
    } else if (strcmp(action_str, "TAKEOFF") == 0 || strcmp(action_str, "TAKING OFF") == 0) {
        op = RW_STATE_TAKEOFF;
    }

    if (op == RW_STATE_IDLE) return;

    RunwayLockGuard lock;
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

        bool is_start = (strcmp(r.name_start, runway_name) == 0);
        bool is_end   = (strcmp(r.name_end, runway_name) == 0);

        if (!is_start && !is_end) {
            continue;
        }

        // Aviation Rule: On the exact same physical runway strip, LANDING has absolute priority over TAKEOFF.
        // If the strip is currently animating an arrival rollout, do not interrupt it with a departure comet.
        if (r.state == RW_STATE_LANDING && op == RW_STATE_TAKEOFF) {
            return;
        }

        bool forward = is_start; // true: start_idx -> end_idx, false: end_idx -> start_idx
        bool state_changed = (r.state != op || r.moving_forward != forward);

        r.state = op;
        r.moving_forward = forward;
        r.speed_norm = 0.006f;   // 5.0s constant physical linear speed
        r.was_recently_used = true;
        r.recent_forward = forward;

        // CRUCIAL STABILITY FIX:
        // Only reset comet_norm to 0.0f when an operation genuinely changes or reverses.
        // If the same operation is re-confirmed on subsequent polls, preserve comet_norm so the
        // animation glides seamlessly down the runway without resetting every 1.5 seconds!
        if (state_changed) {
            r.comet_norm = 0.0f;
        }
        return;
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
    RunwayLockGuard lock;
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

bool renderRunwayAnimations() {
    uint32_t now = millis();
    if (last_frame_time != 0 && (now - last_frame_time) < 10) { // ~71.4 FPS animation loop limiter
        return false;
    }
    float dt = (last_frame_time == 0) ? 0.014f : (float)(now - last_frame_time) / 1000.0f;
    if (dt > 0.050f) dt = 0.050f; // Clamp to 50ms max to prevent jumps during network latency
    last_frame_time = now;

    RunwayLockGuard lock;

#if defined(BOARD_MODE_DEVKIT)
    // DevKit Test Mode:
    // Single onboard RGB LED: Strobes during active operations using dynamic theme palette
    const LedThemeConfig& theme = getThemeConfig();
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
        // Aviation Approach Strobe: Double-flash theme Landing color every 750ms
        CRGB l_col = CRGB(theme.landing_tail_rgb);
        uint32_t cycle = now % 750;
        if (cycle < 60) {
            setDevKitRgb(l_col.r, l_col.g, l_col.b); // Flash 1 (Full Strobe)
        } else if (cycle < 130) {
            setDevKitRgb(l_col.r / 4, l_col.g / 4, l_col.b / 4); // Strobe Dip
        } else if (cycle < 190) {
            setDevKitRgb(l_col.r, l_col.g, l_col.b); // Flash 2 (Full Strobe)
        } else {
            setDevKitRgb(l_col.r / 6, l_col.g / 6, l_col.b / 6); // Low baseline approach glow
        }
    } else if (active_op == RW_STATE_TAKEOFF) {
        // Aviation Departure Strobe: Crisp theme Takeoff flash every 500ms
        CRGB t_col = CRGB(theme.takeoff_tail_rgb);
        uint32_t cycle = now % 500;
        if (cycle < 80) {
            setDevKitRgb(t_col.r, t_col.g, t_col.b); // Flash (Full Strobe)
        } else if (cycle < 160) {
            setDevKitRgb(t_col.r / 3, t_col.g / 3, t_col.b / 3); // Decay
        } else {
            setDevKitRgb(t_col.r / 6, t_col.g / 6, t_col.b / 6); // Low baseline departure glow
        }
    } else {
        // Idle mode: Silky smooth breathing pulse using theme afterburner/takeoff color
        if (has_had_event) {
            CRGB idle_base = CRGB(theme.takeoff_tail_rgb);
            // Smooth quadratic sine respiration:
            // Maps smoothly between 25% and 100% of brightness using quadratic ease
            // to eliminate flat-bottoming near zero and prevent discrete color quantization.
            float s = (sin(now / 600.0f) + 1.0f) * 0.5f; // 0.0 to 1.0
            float breath = 0.25f + 0.75f * (s * s);
            uint8_t r = (uint8_t)(idle_base.r * breath);
            uint8_t g = (uint8_t)(idle_base.g * breath);
            uint8_t b = (uint8_t)(idle_base.b * breath);
            setDevKitRgb(r, g, b);
        } else {
            setDevKitRgb(0, 10, 40); // Standby indicator (scales with brightness)
        }
    }
    return true;
#else
    FastLED.clear();
    const LedThemeConfig& theme = getThemeConfig();

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
            CRGB idle_bound = CRGB(theme.idle_threshold_rgb);
            if (idle_bound.r == 0 && idle_bound.g == 0 && idle_bound.b == 0) {
                idle_bound = CRGB(6, 9, 14);
            }
            leds[seg.start_idx] = idle_bound;
            leds[seg.end_idx]   = idle_bound;

            if (seg.crossing_idx >= 0) {
                leds[seg.crossing_idx] = CRGB(4, 5, 8);
            }

            // IDLE YELLOW STROBE:
            // Runs ONLY AFTER at least one flight event has occurred since startup
            if (has_had_event && !has_active_flight && seg.was_recently_used) {
                const float IDLE_TAIL = 0.20f;
                seg.idle_strobe_norm += 0.15f * dt; // ~9.0s slow relaxing glide (frame-rate independent)
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
                            uint8_t cie_f = applyCieGamma((uint8_t)((1.0f - f) * 255.0f));
                            y_color = blend(CRGB(160, 90, 0), CRGB::Black, 255 - cie_f);
                        }
                        addLedColor(strip_idx, y_color);
                    }
                }
            }
        }
        else {
            // ACTIVE FLIGHT COMET (Landing or Takeoff at constant 4.5s physical pace, frame-rate independent)
            seg.comet_norm += 0.30f * dt;
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
                        // Landing Comet: Touchdown Strobe Head + Rollout Glidepath Tail
                        CRGB head_col = CRGB(theme.landing_head_rgb);
                        CRGB mid_col  = CRGB(theme.landing_tail_rgb);
                        if (u < 0.15f) {
                            led_color = head_col;
                        } else if (u < 0.50f) {
                            float f = (u - 0.15f) / 0.35f;
                            led_color = blend(head_col, mid_col, (uint8_t)(f * 255));
                        } else {
                            float f = (u - 0.50f) / 0.50f;
                            uint8_t cie_f = applyCieGamma((uint8_t)((1.0f - f) * 255.0f));
                            led_color = blend(mid_col, CRGB::Black, 255 - cie_f);
                        }
                    } else {
                        // Takeoff Comet: Acceleration Roll Head + Afterburner Flame Tail
                        CRGB head_col = CRGB(theme.takeoff_head_rgb);
                        CRGB mid_col  = CRGB(theme.takeoff_tail_rgb);
                        if (u < 0.15f) {
                            led_color = head_col;
                        } else if (u < 0.50f) {
                            float f = (u - 0.15f) / 0.35f;
                            led_color = blend(head_col, mid_col, (uint8_t)(f * 255));
                        } else {
                            float f = (u - 0.50f) / 0.50f;
                            uint8_t cie_f = applyCieGamma((uint8_t)((1.0f - f) * 255.0f));
                            led_color = blend(mid_col, CRGB::Black, 255 - cie_f);
                        }
                    }

                    addLedColor(strip_idx, led_color);
                }
            }

            // Illuminate runway threshold base
            CRGB thresh_col = (seg.state == RW_STATE_LANDING) ? CRGB(theme.landing_tail_rgb) : CRGB(theme.takeoff_tail_rgb);
            thresh_col.nscale8_video(40);
            if (seg.moving_forward) {
                addLedColor(seg.start_idx, thresh_col);
            } else {
                addLedColor(seg.end_idx,   thresh_col);
            }

            if (seg.crossing_idx >= 0) {
                addLedColor(seg.crossing_idx, CRGB(10, 8, 4));
            }
        }
    }

    FastLED.show();
    return true;
#endif
}

void showConnectionStatusLed(bool connected) {
    if (connected) return;
    RunwayLockGuard lock;
#if defined(BOARD_MODE_DEVKIT)
    bool blink = (millis() % 1000 < 500);
    setDevKitRgb(blink ? 120 : 0, 0, 0);
#else
    leds[0] = (millis() % 1000 < 500) ? CRGB(180, 0, 0) : CRGB::Black;
    FastLED.show();
#endif
}

static void ledAnimationTask(void* pvParameters) {
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xFrequency = pdMS_TO_TICKS(14); // ~71.4 FPS target

    for (;;) {
        bool rendered = renderRunwayAnimations();
#if !defined(BOARD_MODE_BLE)
        if (rendered) {
            recordFrameRendered();
        }
#endif
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void startLedAnimationTask() {
    if (runway_mutex == nullptr) {
        runway_mutex = xSemaphoreCreateMutex();
    }
    if (led_task_handle == nullptr) {
        xTaskCreatePinnedToCore(
            ledAnimationTask,
            "LED_Anim_Task",
            4096,
            nullptr,
            2, // Priority 2
            &led_task_handle,
            1  // Core 1
        );
    }
}
