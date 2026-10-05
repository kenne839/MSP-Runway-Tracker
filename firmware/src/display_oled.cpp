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

static DisplayTelemetryData current_data;
static uint8_t current_flight_idx = 0;
static uint32_t last_cycle_time = 0;
static uint32_t last_telemetry_rx_time = 0;
static bool has_received_initial_data = false;

void initDisplay() {
    Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
    if (display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        display_initialized = true;
        display.clearDisplay();
        display.setTextWrap(false); // Prevent long lines from wrapping and shifting lower rows
        
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
    } else {
        Serial.println(F("[OLED] Warning: SSD1306 allocation failed. Check I2C address/wiring."));
    }

    memset(&current_data, 0, sizeof(current_data));
    current_data.has_had_event = false;
    current_data.active_runways_summary[0] = '\0';
    current_data.runway_roles_summary[0] = '\0';
    current_data.weather.valid = false;
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
}

void renderDisplayLoop() {
    if (!display_initialized) return;

    uint32_t now = millis();
    // Allow up to 65 seconds (two 30s OpenSky poll cycles + buffer) before declaring link lost
    bool is_stale = has_received_initial_data && (now - last_telemetry_rx_time > 65000);

    if (is_stale) {
        // Expire active flights so old landing/takeoff events are not displayed
        current_data.flight_count = 0;
    }

    // Cycle through active events every 3000 ms (3 seconds)
    if (current_data.flight_count > 1 && (now - last_cycle_time > 3000)) {
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
        
        // Callsign / Airline (Row 1)
        display.setCursor(0, 19);
        display.setTextSize(1);
        display.println(f.flight_label);

        // Aircraft Type (Row 2)
        display.setCursor(0, 30);
        display.println(f.aircraft_type);

        // Route (Row 3)
        display.setCursor(0, 41);
        display.println(f.route);

        // Horizontal blue divider
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        // Footer telemetry status (Row 4)
        display.setCursor(0, 54);
        if (current_data.flight_count > 1) {
            display.print(F("Cycle 3s | "));
            display.print(current_data.tracked_count);
            display.print(F(" tracked"));
        } else {
            display.print(F("Airspace: "));
            display.print(current_data.tracked_count);
            display.print(F(" tracked"));
        }
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
            display.println(current_data.runway_roles_summary);
        } else if (current_data.active_runways_summary[0] != '\0') {
            display.print(F("RW: "));
            display.println(current_data.active_runways_summary);
        } else {
            display.println(F("RW: Standby"));
        }

        // Row 2 (y = 29): Wind & Temperature (e.g. Wind 270@11kt  59F)
        display.setCursor(0, 29);
        if (current_data.weather.valid) {
            display.print(F("Wind "));
            display.print(current_data.weather.wind);
            display.print(F("  "));
            display.print(current_data.weather.temp_f);
            display.println(F("F"));
        } else {
            display.println(F("Wind: Polling METAR..."));
        }

        // Row 3 (y = 40): Pressure & Condition (e.g. 30.24" | Few)
        display.setCursor(0, 40);
        if (current_data.weather.valid) {
            char p_buf[12];
            strncpy(p_buf, current_data.weather.pressure, sizeof(p_buf) - 1);
            p_buf[sizeof(p_buf) - 1] = '\0';
            char* inhg = strstr(p_buf, " inHg");
            if (inhg) *inhg = '\0';
            display.print(p_buf);
            display.print(F("\" | "));

            // Compact condition labels so they easily fit 128px width
            const char* cond = current_data.weather.condition;
            if (strncmp(cond, "Few Clouds", 10) == 0) cond = "Few";
            else if (strncmp(cond, "Scattered Clouds", 16) == 0) cond = "Sct";
            else if (strncmp(cond, "Broken Clouds", 13) == 0) cond = "Broken";
            else if (strncmp(cond, "Clear Skies", 11) == 0) cond = "Clear";
            display.println(cond);
        } else {
            display.print(F("Airspace: "));
            display.print(current_data.tracked_count);
            display.println(F(" planes"));
        }

        // Horizontal blue divider
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);

        // Row 4 (y = 54): Status Footer
        display.setCursor(0, 54);
        if (current_data.has_had_event) {
            display.print(F("Airspace: "));
            display.print(current_data.tracked_count);
            display.print(F(" tracked"));
        } else {
            display.print(F("Telemetry Polling Ready"));
        }
    }

    display.display();
}
