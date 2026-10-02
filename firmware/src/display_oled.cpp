#include "display_oled.h"

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
static bool display_initialized = false;

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
}

void showBootScreen(const char* status_text) {
    if (!display_initialized) return;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    
    // Header
    display.setTextSize(1);
    display.setCursor(14, 4);
    display.println(F("KMSP RUNWAY HUB"));
    display.drawFastHLine(0, 16, SCREEN_WIDTH, SSD1306_WHITE);

    // Status
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

void updateDisplayFlight(const DisplayFlightInfo& info) {
    if (!display_initialized) return;
    display.clearDisplay();

    if (info.has_active_flight) {
        // Active Flight Screen
        // Inverted top banner for Runway & Action
        display.fillRect(0, 0, SCREEN_WIDTH, 14, SSD1306_WHITE);
        display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(4, 3);
        display.print(F("RW "));
        display.print(info.runway);
        display.print(F(" - "));
        display.print(info.action);

        // Body text in normal white
        display.setTextColor(SSD1306_WHITE);
        
        // Flight / Callsign
        display.setCursor(0, 18);
        display.setTextSize(1);
        display.println(info.flight_label);

        // Aircraft Type
        display.setCursor(0, 29);
        display.println(info.aircraft_type);

        // Route
        display.setCursor(0, 40);
        display.println(info.route);

        // Telemetry Row (Altitude & Ground Speed)
        display.drawFastHLine(0, 51, SCREEN_WIDTH, SSD1306_WHITE);
        display.setCursor(0, 54);
        display.print(F("Alt:"));
        display.print(info.altitude_ft);
        display.print(F("ft  Spd:"));
        display.print(info.speed_kts);
        display.print(F("kt"));
    } else {
        // Idle Screen
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(10, 6);
        display.println(F("KMSP RUNWAY MONITOR"));
        display.drawFastHLine(0, 18, SCREEN_WIDTH, SSD1306_WHITE);

        display.setCursor(18, 28);
        display.println(F("ALL RUNWAYS CLEAR"));
        display.setCursor(18, 42);
        display.print(F("Airspace: "));
        display.print(info.tracked_count);
        display.print(F(" planes"));

        // Small bottom heartbeat dot
        display.drawFastHLine(0, 54, SCREEN_WIDTH, SSD1306_WHITE);
        display.setCursor(32, 56);
        display.print(F("Live Standby"));
    }

    display.display();
}
