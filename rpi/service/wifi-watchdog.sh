#!/usr/bin/env bash
# ==============================================================================
# KMSP Runway Tracker - Headless Wi-Fi Watchdog for Raspberry Pi
# ==============================================================================
# Ensures 24/7 headless connectivity by preventing Wi-Fi chip power-saving sleep
# and auto-recovering from dropped router handshakes / channel changes.
# ==============================================================================

# 1. Disable Wi-Fi power management (fixes notorious Broadcom BCM sleep bug on RPi)
if command -v iw >/dev/null 2>&1; then
    iw dev wlan0 set power_save off 2>/dev/null || true
fi

# 2. Determine default router gateway
GATEWAY_IP=$(ip route | awk '/default/ { print $3; exit }')
TARGET="${GATEWAY_IP:-1.1.1.1}"

# 3. Check connectivity
if ! ping -c 2 -W 3 "$TARGET" >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Wi-Fi Watchdog] Ping to $TARGET failed. Initiating recovery..."

    # Step A: Non-disruptive WPA reassociation
    if command -v wpa_cli >/dev/null 2>&1; then
        wpa_cli -i wlan0 reassociate >/dev/null 2>&1 || true
    fi
    sleep 5

    # Step B: If still down, cycle the wlan0 interface
    if ! ping -c 2 -W 3 "$TARGET" >/dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Wi-Fi Watchdog] Reassociation failed. Cycling wlan0 link..."
        ip link set wlan0 down 2>/dev/null || true
        sleep 2
        ip link set wlan0 up 2>/dev/null || true
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Wi-Fi Watchdog] Link restored via reassociation."
    fi
fi
