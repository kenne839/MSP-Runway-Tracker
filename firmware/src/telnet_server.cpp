#include "telnet_server.h"

#if !defined(BOARD_MODE_BLE)

static WiFiServer telnetServer(23);
static WiFiClient telnetClient;

void initTelnetServer() {
    telnetServer.begin();
    telnetServer.setNoDelay(true);
    Serial.println(F("[Telnet] Remote debugging console listening on port 23."));
}

void handleTelnetServer() {
    if (telnetServer.hasClient()) {
        if (!telnetClient || !telnetClient.connected()) {
            if (telnetClient) telnetClient.stop();
            telnetClient = telnetServer.available();
            telnetClient.println();
            telnetClient.println(F("=================================================="));
            telnetClient.println(F("   KMSP RUNWAY TRACKER - WIRELESS TELNET CONSOLE  "));
            telnetClient.printf( "   IP: %s | Free Heap: %u KB | RSSI: %d dBm\n",
                                 WiFi.localIP().toString().c_str(),
                                 ESP.getFreeHeap() / 1024,
                                 WiFi.RSSI());
            telnetClient.println(F("=================================================="));
        } else {
            // Reject extra client gracefully to preserve memory
            WiFiClient extra = telnetServer.available();
            extra.println(F("KMSP Tracker: Server busy (1 client maximum)."));
            extra.stop();
        }
    }
}

void telnetPrint(const char* msg) {
    Serial.print(msg);
    if (telnetClient && telnetClient.connected()) {
        telnetClient.print(msg);
    }
}

void telnetPrintln(const char* msg) {
    Serial.println(msg);
    if (telnetClient && telnetClient.connected()) {
        telnetClient.println(msg);
    }
}

void telnetPrintf(const char* fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    Serial.print(buf);
    if (telnetClient && telnetClient.connected()) {
        telnetClient.print(buf);
    }
}

bool isTelnetClientConnected() {
    return telnetClient && telnetClient.connected();
}

#endif

