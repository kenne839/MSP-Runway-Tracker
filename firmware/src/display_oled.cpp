#include "display_oled.h"

#if !defined(BOARD_MODE_BLE)
#include <WiFi.h>
#endif

// UCTRONICS 0.96" Dual-Color OLED (128x64 SSD1306)
// Physical Color Geometry:
// - Top Yellow Zone: Rows y = 0 to 15 (16 pixels)
// - Physical Separation Gap: ~2 pixels (unlit boundary)
// - Bottom Blue Zone: Rows y = 16 to 63 (48 pixels)

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static bool display_initialized = false;
static bool is_display_sleeping = false;
static uint32_t last_active_event_time = 0;

static DisplayTelemetryData current_data;
static uint8_t current_flight_idx = 0;
static uint32_t last_cycle_time = 0;
static uint32_t last_telemetry_rx_time = 0;
static bool has_received_initial_data = false;

void wakeDisplay() {
    if (!display_initialized) return;
    last_active_event_time = millis();
    if (is_display_sleeping) {
        display.ssd1306_command(SSD1306_DISPLAYON);
        is_display_sleeping = false;
        Serial.println(F("[OLED] Waking up display from idle sleep."));
    }
}

bool isDisplaySleeping() {
    return is_display_sleeping;
}

static void sleepDisplay() {
    if (!display_initialized || is_display_sleeping) return;
    display.clearDisplay();
    display.display();
    display.ssd1306_command(SSD1306_DISPLAYOFF);
    is_display_sleeping = true;
    Serial.println(F("[OLED] 5-minute idle reached. Display entered sleep mode to prevent burn-in."));
}

// Safe string printer: guarantees string never exceeds max_chars (21 chars = 126px, never clips)
static void printFitted(const char* str, uint8_t max_chars = 21) {
    if (!str) return;
    uint8_t count = 0;
    while (str[count] != '\0' && count < max_chars) {
        display.write(str[count]);
        count++;
    }
}

// Format clean flight label: strips redundant "Airlines" / "Air Lines" so 4-digit flight numbers never cut off
static void formatFlightLabel(const char* raw, char* out_buf, size_t out_len) {
    if (!raw || !out_buf || out_len == 0) return;
    strncpy(out_buf, raw, out_len - 1);
    out_buf[out_len - 1] = '\0';

    // Replace verbose " Air Lines " or " Airlines " or " Airways " with single space
    char* found = strstr(out_buf, " Air Lines ");
    if (found) {
        char temp[32];
        int prefix_len = found - out_buf;
        snprintf(temp, sizeof(temp), "%.*s %s", prefix_len, out_buf, found + 11);
        strncpy(out_buf, temp, out_len - 1);
    }
    found = strstr(out_buf, " Airlines ");
    if (found) {
        char temp[32];
        int prefix_len = found - out_buf;
        snprintf(temp, sizeof(temp), "%.*s %s", prefix_len, out_buf, found + 10);
        strncpy(out_buf, temp, out_len - 1);
    }
    found = strstr(out_buf, " Airways ");
    if (found) {
        char temp[32];
        int prefix_len = found - out_buf;
        snprintf(temp, sizeof(temp), "%.*s %s", prefix_len, out_buf, found + 9);
        strncpy(out_buf, temp, out_len - 1);
    }
}

// I2C Bus Recovery: Clock out any hung slave holding SDA low (NXP UM10204 Bus Clear)
static void recoverI2cBus(uint8_t sda_pin, uint8_t scl_pin) {
    pinMode(sda_pin, INPUT_PULLUP);
    pinMode(scl_pin, OUTPUT);
    digitalWrite(scl_pin, HIGH);
    delayMicroseconds(10);

    for (int i = 0; i < 9; i++) {
        digitalWrite(scl_pin, LOW);
        delayMicroseconds(10);
        digitalWrite(scl_pin, HIGH);
        delayMicroseconds(10);
        if (digitalRead(sda_pin) == HIGH) {
            break; // Slave released SDA
        }
    }

    // Generate I2C STOP condition
    pinMode(sda_pin, OUTPUT);
    digitalWrite(sda_pin, LOW);
    delayMicroseconds(10);
    digitalWrite(scl_pin, HIGH);
    delayMicroseconds(10);
    digitalWrite(sda_pin, HIGH);
    delayMicroseconds(10);

    pinMode(sda_pin, INPUT_PULLUP);
    pinMode(scl_pin, INPUT_PULLUP);
}

void initDisplay() {
    if (display_initialized) return;

    // 1. Cold-boot power settling delay (ensures 3.3V rail & OLED POR capacitor are fully charged)
    delay(150);

    // 2. Lock custom pins into TwoWire instance so libraries cannot reassign them
    Wire.setPins(PIN_OLED_SDA, PIN_OLED_SCL);

    bool hardware_ready = false;

    for (int attempt = 1; attempt <= 3; attempt++) {
        // Recover bus from any hung state
        recoverI2cBus(PIN_OLED_SDA, PIN_OLED_SCL);

        // Start I2C at standard 100kHz for reliable startup handshake
        Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL, 100000);
        Wire.setTimeOut(50);
        delay(30);

        // Probe address 0x3C to verify physical hardware is awake and ACK-ing
        Wire.beginTransmission(OLED_I2C_ADDR);
        if (Wire.endTransmission() == 0) {
            hardware_ready = true;
            break;
        }

        Serial.printf("[OLED] Device 0x%02X not responding (attempt %d/3). Retrying in 100ms...\n", OLED_I2C_ADDR, attempt);
        delay(100);
    }

    if (!hardware_ready) {
        Serial.println(F("[OLED] Warning: SSD1306 did not ACK I2C address. Self-healing loop will retry."));
        return;
    }

    // Initialize display with reset=false, periphBegin=false (we already configured Wire)
    if (display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR, false, false)) {
        display_initialized = true;
        is_display_sleeping = false;
        last_active_event_time = millis();

        // Switch to 400kHz Fast I2C for fast screen updates
        Wire.setClock(400000);

        // Explicitly enable charge pump and turn display ON
        display.ssd1306_command(SSD1306_CHARGEPUMP);
        display.ssd1306_command(0x14);
        display.ssd1306_command(SSD1306_DISPLAYON);

        display.clearDisplay();
        display.setTextWrap(false);
        
        // Yellow Header (y=0..15)
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(4, 3);
        display.println(F("KMSP RUNWAY HUB"));
        display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE);

        // Blue Body (y=16..63)
        display.setCursor(0, 24);
        display.println(F("ESP32-S3 Dual-Color"));
        display.setCursor(0, 38);
        display.println(F("UCTRONICS 128x64"));
        display.display();

        Serial.println(F("[OLED] SSD1306 successfully initialized at 400kHz."));
    } else {
        Serial.println(F("[OLED] Warning: display.begin failed. Self-healing loop will retry."));
    }

    memset(&current_data, 0, sizeof(current_data));
    current_data.has_had_event = false;
    current_data.active_runways_summary[0] = '\0';
    current_data.runway_roles_summary[0] = '\0';
    current_data.weather.valid = false;
    last_active_event_time = millis();
    is_display_sleeping = false;
}

void showBootScreen(const char* status_text) {
    if (!display_initialized) return;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    
    // Top Yellow Zone (y=0..15)
    display.setTextSize(1);
    display.setCursor(14, 3);
    display.println(F("KMSP RUNWAY HUB"));
    display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE);

    // Bottom Blue Zone (y=16..63)
    display.setCursor(4, 28);
    display.println(status_text);
    display.display();
}

void showWifiStatus(bool connected, const char* ip_str) {
    if (!display_initialized) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);

    // Top Yellow Zone (y=0..15)
    display.setCursor(30, 3);
    display.println(F("WI-FI SETUP"));
    display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE);

    // Bottom Blue Zone (y=16..63)
    if (connected) {
        display.setCursor(0, 24);
        display.println(F("Connected to LAN:"));
        display.setCursor(0, 38);
        display.println(ip_str);
    } else {
        display.setCursor(0, 24);
        display.println(F("Connecting to:"));
        display.setCursor(0, 38);
        display.println(WIFI_SSID);
    }
    display.display();
}

void updateTelemetryData(const DisplayTelemetryData& data) {
    current_data = data;
    last_telemetry_rx_time = millis();
    has_received_initial_data = true;
    if (current_flight_idx >= current_data.flight_count) {
        current_flight_idx = 0;
    }
    // If active flight operations are present, wake display and reset idle sleep timer
    if (current_data.flight_count > 0) {
        wakeDisplay();
    }
}

void renderDisplayLoop() {
    uint32_t now = millis();

    // Self-healing recovery: If OLED failed on cold boot, retry every 2 seconds until it connects
    if (!display_initialized) {
        static uint32_t last_init_retry_time = 0;
        if (now - last_init_retry_time > 2000) {
            last_init_retry_time = now;
            initDisplay();
        }
        return;
    }

    // Synchronized link loss timeout: if offline > LINK_LOSS_IDLE_TIMEOUT_MS (45s), declare link lost
    bool is_stale = has_received_initial_data && (now - last_telemetry_rx_time > LINK_LOSS_IDLE_TIMEOUT_MS);

    if (is_stale) {
        // Expire active flights so old landing/takeoff events are not displayed
        current_data.flight_count = 0;
        current_data.link_online = false;
    }

    bool has_active_flights = (current_data.flight_count > 0 && !is_stale);

    if (has_active_flights) {
        last_active_event_time = now;
        if (is_display_sleeping) {
            wakeDisplay();
        }
    } else {
        // Idle state: Check if 5-minute timeout has elapsed
        if (now - last_active_event_time >= OLED_IDLE_SLEEP_TIMEOUT_MS) {
            if (!is_display_sleeping) {
                sleepDisplay();
            }
            return; // While asleep, do not draw or refresh display
        }
    }

    // If sleeping, do nothing
    if (is_display_sleeping) {
        return;
    }

    static uint32_t last_display_draw_time = 0;
    if (now - last_display_draw_time < 100) {
        return; // ~10 FPS display refresh rate: frees I2C bus so LED animations run silky-smooth at 50 FPS
    }
    last_display_draw_time = now;

    // Cycle through active events every 5000 ms (5 seconds)
    if (current_data.flight_count > 1 && (now - last_cycle_time > 5000)) {
        last_cycle_time = now;
        current_flight_idx = (current_flight_idx + 1) % current_data.flight_count;
    }

    display.clearDisplay();
    display.setTextWrap(false);

    if (current_data.flight_count > 0 && !is_stale) {
        const FlightEvent& f = current_data.flights[current_flight_idx];

        // =====================================================================
        // TOP YELLOW ZONE (Rows y = 0 to 15): Runway & Action Banner + Cycle Indicator
        // =====================================================================
        display.fillRect(0, 0, SCREEN_WIDTH, 15, SSD1306_WHITE); // Glowing yellow header
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(2, 4);
        display.print(F("RW "));
        display.print(f.runway);
        display.print(F(" "));

        // If multiple flights, show cycle indicator e.g. "[1/2]" with safe gap
        if (current_data.flight_count > 1) {
            display.print(f.action);
            char cyc[8];
            snprintf(cyc, sizeof(cyc), "[%d/%d]", current_flight_idx + 1, current_data.flight_count);
            int cyc_x = SCREEN_WIDTH - ((int)strlen(cyc) * 6) - 2;
            display.setCursor(cyc_x, 4);
            display.print(cyc);
        } else {
            display.print(F("- "));
            display.print(f.action);
        }

        // =====================================================================
        // BOTTOM BLUE ZONE (Rows y = 16 to 63): Flight Callsign, Type, Route, & Status
        // =====================================================================
        display.setTextColor(SSD1306_WHITE); // Lights up physical blue pixels
        
        // Callsign / Airline (Row 1) - formatted & bounded to 21 chars max
        display.setCursor(0, 19);
        display.setTextSize(1);
        char label_buf[32];
        formatFlightLabel(f.flight_label, label_buf, sizeof(label_buf));
        printFitted(label_buf, 21);

        // Aircraft Type (Row 2) - bounded to 21 chars max
        display.setCursor(0, 30);
        printFitted(f.aircraft_type, 21);

        // Route (Row 3) - bounded to 21 chars max
        display.setCursor(0, 41);
        printFitted(f.route, 21);

        // Horizontal blue divider
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        // Footer telemetry status (Row 4) - bounded to 21 chars max
        display.setCursor(0, 54);
        char foot[24];
        if (current_data.flight_count > 1) {
            if (strlen(current_data.updated_time) > 0) {
                snprintf(foot, sizeof(foot), "Cyc 5s | %s", current_data.updated_time);
            } else {
                snprintf(foot, sizeof(foot), "Cycle 5s | %d tracked", current_data.tracked_count);
            }
        } else {
            if (strlen(current_data.updated_time) > 0) {
                snprintf(foot, sizeof(foot), "Updated: %s", current_data.updated_time);
            } else {
                snprintf(foot, sizeof(foot), "Airspace: %d tracked", current_data.tracked_count);
            }
        }
        printFitted(foot, 21);
    } else if (is_stale) {
        // =====================================================================
        // OFFLINE DIAGNOSTIC SCREEN: Telemetry connection lost or Pi unreachable
        // =====================================================================
#if defined(BOARD_MODE_BLE)
        // TOP YELLOW ZONE (Rows y = 0 to 15)
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(2, 3);
        display.print(F("KMSP AIRPORT"));
        display.setCursor(90, 3);
        display.print(F("[STBY]"));
        display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE);

        // BOTTOM BLUE ZONE (Rows y = 16 to 63)
        display.setCursor(0, 18);
        display.println(F("Office PC Standby"));

        display.setCursor(0, 29);
        display.print(F("BLE: "));
        display.println(BLE_DEVICE_NAME);

        display.setCursor(0, 40);
        display.println(F("Waiting for link..."));

        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        display.setCursor(0, 54);
        display.print(F("Link lost "));
        display.print((now - last_telemetry_rx_time) / 1000);
        display.println(F("s ago"));
#else
        bool wifi_down = (WiFi.status() != WL_CONNECTED);

        // TOP YELLOW ZONE (Rows y = 0 to 15)
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(2, 3);
        display.print(F("KMSP AIRPORT"));
        if (wifi_down) {
            display.setCursor(78, 3);
            display.print(F("[NO-NET]"));
        } else {
            display.setCursor(72, 3);
            display.print(F("[OFFLINE]"));
        }
        display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE);

        // BOTTOM BLUE ZONE (Rows y = 16 to 63)
        display.setCursor(0, 18);
        if (wifi_down) {
            display.println(F("Wi-Fi Disconnected"));
        } else {
            display.println(F("Raspberry Pi Offline"));
        }

        display.setCursor(0, 29);
        if (wifi_down) {
            display.print(F("SSID: "));
            display.println(WIFI_SSID);
        } else {
            display.println(F("Target: Port 8080"));
        }

        display.setCursor(0, 40);
        display.println(F("Auto-retrying link..."));

        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        display.setCursor(0, 54);
        display.print(F("Link lost "));
        display.print((now - last_telemetry_rx_time) / 1000);
        display.println(F("s ago"));
#endif
    } else {
        // =====================================================================
        // IDLE SCREEN: Showing active runway roles & live KMSP METAR weather
        // =====================================================================
        
        // TOP YELLOW ZONE (Rows y = 0 to 15)
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(2, 3);
        display.print(F("KMSP AIRPORT"));

        // Dynamically right-align badge ([IDLE], [VFR], [MVFR], etc.) with 2px margin from right edge
        char badge[10];
        if (current_data.weather.valid && current_data.weather.flight_category[0] != '\0') {
            snprintf(badge, sizeof(badge), "[%s]", current_data.weather.flight_category);
        } else {
            strcpy(badge, "[IDLE]");
        }
        int badge_x = SCREEN_WIDTH - ((int)strlen(badge) * 6) - 2;
        display.setCursor(badge_x, 3);
        display.print(badge);
        display.drawFastHLine(0, 15, SCREEN_WIDTH, SSD1306_WHITE); // Yellow dividing line

        // BOTTOM BLUE ZONE (Rows y = 16 to 63)
        // Row 1 (y = 18): Runway Operational Roles (e.g. ARR 30R / DEP 30L)
        display.setCursor(0, 18);
        if (current_data.runway_roles_summary[0] != '\0') {
            printFitted(current_data.runway_roles_summary, 21);
        } else if (current_data.active_runways_summary[0] != '\0') {
            char r_buf[24];
            snprintf(r_buf, sizeof(r_buf), "RW: %s", current_data.active_runways_summary);
            printFitted(r_buf, 21);
        } else {
            printFitted("RW: Standby", 21);
        }

        // Row 2 (y = 29): Wind & Temperature (e.g. Wind 270@11kt  59F)
        display.setCursor(0, 29);
        if (current_data.weather.valid) {
            char w_buf[24];
            snprintf(w_buf, sizeof(w_buf), "Wind %s %dF", current_data.weather.wind, current_data.weather.temp_f);
            printFitted(w_buf, 21);
        } else {
            printFitted("Wind: Polling METAR", 21);
        }

        // Row 3 (y = 40): Pressure & Condition (e.g. 30.24" | Few)
        display.setCursor(0, 40);
        if (current_data.weather.valid) {
            char p_buf[12];
            strncpy(p_buf, current_data.weather.pressure, sizeof(p_buf) - 1);
            p_buf[sizeof(p_buf) - 1] = '\0';
            char* inhg = strstr(p_buf, " inHg");
            if (inhg) *inhg = '\0';

            // Compact condition labels so they easily fit 128px width
            const char* cond = current_data.weather.condition;
            if (strncmp(cond, "Few Clouds", 10) == 0) cond = "Few";
            else if (strncmp(cond, "Scattered Clouds", 16) == 0) cond = "Sct";
            else if (strncmp(cond, "Broken Clouds", 13) == 0) cond = "Broken";
            else if (strncmp(cond, "Clear Skies", 11) == 0) cond = "Clear";
            else if (strncmp(cond, "Overcast", 8) == 0) cond = "Ovc";

            char b_buf[24];
            snprintf(b_buf, sizeof(b_buf), "%s\" | %s", p_buf, cond);
            printFitted(b_buf, 21);
        } else {
            char b_buf[24];
            snprintf(b_buf, sizeof(b_buf), "METAR Pending...");
            printFitted(b_buf, 21);
        }

        // Horizontal blue divider
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        // Row 4 (y = 54): Status Footer (strictly <= 21 chars, never clips)
        display.setCursor(0, 54);
        char foot[24];
        if (strlen(current_data.updated_time) > 0) {
            snprintf(foot, sizeof(foot), "Updated: %s", current_data.updated_time);
        } else if (current_data.has_had_event) {
            snprintf(foot, sizeof(foot), "Airspace: %d tracked", current_data.tracked_count);
        } else {
            snprintf(foot, sizeof(foot), "Telemetry Ready");
        }
        printFitted(foot, 21);
    }

    display.display();
}
