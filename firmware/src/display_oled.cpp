#include "display_oled.h"

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

void initDisplay() {
    Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
    if (display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        display_initialized = true;
        display.clearDisplay();
        
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
    if (current_flight_idx >= current_data.flight_count) {
        current_flight_idx = 0;
    }
}

void renderDisplayLoop() {
    if (!display_initialized) return;

    uint32_t now = millis();

    // Cycle through active events every 3000 ms (3 seconds)
    if (current_data.flight_count > 1 && (now - last_cycle_time > 3000)) {
        last_cycle_time = now;
        current_flight_idx = (current_flight_idx + 1) % current_data.flight_count;
    }

    display.clearDisplay();

    if (current_data.flight_count > 0) {
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
        display.print(F(" - "));
        display.print(f.action);

        // If multiple flights, show cycle indicator e.g. "[1/2]"
        if (current_data.flight_count > 1) {
            display.setCursor(96, 4);
            display.print(F("["));
            display.print(current_flight_idx + 1);
            display.print(F("/"));
            display.print(current_data.flight_count);
            display.print(F("]"));
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
    } else {
        // =====================================================================
        // IDLE SCREEN: Showing active runway roles & live KMSP METAR weather
        // =====================================================================
        
        // TOP YELLOW ZONE (Rows y = 0 to 15)
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(4, 3);
        display.print(F("KMSP AIRPORT"));

        // Flight condition category badge e.g. [VFR] or [IFR]
        if (current_data.weather.valid) {
            display.setCursor(84, 3);
            display.print(F("["));
            display.print(current_data.weather.flight_category);
            display.print(F("]"));
        } else {
            display.setCursor(96, 3);
            display.print(F("[IDLE]"));
        }
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

        // Row 3 (y = 40): Pressure & Condition (e.g. Baro 30.06\" Broken)
        display.setCursor(0, 40);
        if (current_data.weather.valid) {
            display.print(F("Baro "));
            char p_buf[12];
            strncpy(p_buf, current_data.weather.pressure, sizeof(p_buf) - 1);
            p_buf[sizeof(p_buf) - 1] = '\0';
            char* inhg = strstr(p_buf, " inHg");
            if (inhg) *inhg = '\0';
            display.print(p_buf);
            display.print(F("\" "));
            display.println(current_data.weather.condition);
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
