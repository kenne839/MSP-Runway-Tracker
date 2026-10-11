#pragma once

#include <Arduino.h>

#if !defined(BOARD_MODE_BLE)
#include <WiFi.h>

void initTelnetServer();
void handleTelnetServer();
void telnetPrint(const char* msg);
void telnetPrintln(const char* msg);
void telnetPrintf(const char* fmt, ...);
bool isTelnetClientConnected();

#endif

