#include "display_oled.h"

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
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(0, 0);
        display.println(F("MSP RUNWAY TRACKER"));
        display.println(F("ESP32-S3 Display Hub"));
        display.display();
    } else {
        Serial.println(F("[OLED] Warning: SSD1306 allocation failed. Check I2C address/wiring."));
    }

    memset(&current_data, 0, sizeof(current_data));
    strncpy(current_data.active_runways_summary, "30R, 30L", sizeof(current_data.active_runways_summary) - 1);
}

void showBootScreen(const char* status_text) {
    if (!display_initialized) return;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    
    display.setTextSize(1);
    display.setCursor(14, 4);
    display.println(F("KMSP RUNWAY HUB"));
    display.drawFastHLine(0, 16, SCREEN_WIDTH, SSD1306_WHITE);

    display.setCursor(4, 28);
    display.println(status_text);
    display.display();
}

void showWifiStatus(bool connected, const char* ip_str) {
    if (!display_initialized) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 4);
    display.println(F("WI-FI SETUP"));
    display.drawFastHLine(0, 16, SCREEN_WIDTH, SSD1306_WHITE);

    if (connected) {
        display.setCursor(0, 24);
        display.println(F("Connected to LAN:"));
        display.setCursor(0, 36);
        display.println(ip_str);
    } else {
        display.setCursor(0, 24);
        display.println(F("Connecting to:"));
        display.setCursor(0, 36);
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

        // Top Inverted Banner for Runway & Action + Cycle index
        display.fillRect(0, 0, SCREEN_WIDTH, 14, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(2, 3);
        display.print(F("RW "));
        display.print(f.runway);
        display.print(F(" - "));
        display.print(f.action);

        // If multiple flights, show cycle indicator e.g. "(1/2)"
        if (current_data.flight_count > 1) {
            display.setCursor(96, 3);
            display.print(F("["));
            display.print(current_flight_idx + 1);
            display.print(F("/"));
            display.print(current_data.flight_count);
            display.print(F("]"));
        }

        // Body text in normal white
        display.setTextColor(SSD1306_WHITE);
        
        // Callsign / Airline
        display.setCursor(0, 18);
        display.setTextSize(1);
        display.println(f.flight_label);

        // Aircraft Type
        display.setCursor(0, 29);
        display.println(f.aircraft_type);

        // Route
        display.setCursor(0, 40);
        display.println(f.route);

        // Telemetry Row
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);
        display.setCursor(0, 54);
        display.print(F("Alt:"));
        display.print(f.altitude_ft);
        display.print(F("ft  Spd:"));
        display.print(f.speed_kts);
        display.print(F("kt"));
    } else {
        // IDLE SCREEN: Showing active configuration and yellow polling strobe
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(4, 4);
        display.println(F("KMSP RUNWAY MONITOR"));
        display.drawFastHLine(0, 16, SCREEN_WIDTH, SSD1306_WHITE);

        display.setCursor(0, 22);
        display.print(F("Active: "));
        display.println(current_data.active_runways_summary);

        display.setCursor(0, 34);
        display.println(F("[Yellow Strobe On]"));

        display.setCursor(0, 45);
        display.print(F("Airspace: "));
        display.print(current_data.tracked_count);
        display.print(F(" planes"));

        // Bottom heartbeat indicator
        display.drawFastHLine(0, 54, SCREEN_WIDTH, SSD1306_WHITE);
        display.setCursor(0, 56);
        display.print(F("Telemetry Polling OK"));
    }

    display.display();
}
