#pragma once

#include <Arduino.h>
#include "display_oled.h"

#if defined(BOARD_MODE_BLE)

void initBleClient();
bool isBleConnected();
bool pollBleTelemetry(DisplayTelemetryData& out_telemetry);
void sendBleApplicationAck(bool success, uint32_t batch_id, uint8_t ops_count, const char* error_msg = nullptr);

#endif
