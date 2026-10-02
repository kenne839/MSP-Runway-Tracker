#pragma once

#include <Arduino.h>
#include "display_oled.h"

#if defined(BOARD_MODE_BLE)

void initBleClient();
bool isBleConnected();
bool pollBleTelemetry(DisplayTelemetryData& out_telemetry);

#endif
