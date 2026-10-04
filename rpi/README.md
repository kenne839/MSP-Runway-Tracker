# MSP Runway Tracker - Raspberry Pi Telemetry Daemon & Web Service

The Raspberry Pi service is designed for headless 24/7 operation on the **Raspberry Pi 3 Model A+ (512MB RAM)**.

### Target Hardware: Raspberry Pi 3 Model A+ (Plus)
- **CPU:** Quad-Core 64-bit ARM Cortex-A53 @ 1.4 GHz (Broadcom BCM2837B0)
- **RAM:** 512 MB LPDDR2 SDRAM *(Telemetry daemon consumes ~40 MB, leaving >90% headroom)*
- **Wireless:** Dual-band 2.4 GHz and 5.0 GHz IEEE 802.11ac Wi-Fi & Bluetooth 4.2 / BLE
- **Form Factor:** Compact 65 &times; 56 mm square layout (no bulky Ethernet jack)
- **Power:** 5V / 2.5A via standard Micro-USB

The Raspberry Pi service is responsible for:
1. Polling ADS-B telemetry data (via OpenSky Network REST API or local RTL-SDR `dump1090` / `readsb` feeder).
2. Projecting aircraft positions onto KMSP runways (`12L/30R`, `12R/30L`, `4/22`, `17/35`) and gating by velocity, altitude, and vertical speed.
3. Disambiguating intersecting runways (e.g. 4/22 crossing 12L/30R) by orthogonal distance and heading alignment.
4. Resolving 24-bit ICAO transponder hex codes against the local 480k-aircraft registry with HexDB fallback.
5. Serving the real-time aggregated JSON payload over HTTP (`/api/runway_state`) to downstream clients like the ESP32-S3 LED display.
6. Providing an in-memory web dashboard (`/`) and interactive hardware simulator (`/simulator`).
7. Protecting MicroSD card health by caching all state in RAM and serving directly from memory (or `/dev/shm`).

---

## Directory Structure

```text
rpi/
├── config.py              # Configuration thresholds, coordinates, URLs, ports
├── spatial.py             # Tangent plane projection and runway geometry
├── metadata.py            # Airlines, airframes, multi-tier routes, transponder resolver
├── tracker.py             # Polling engine, kinematic filter, thread-safe state store
├── server.py              # Multi-threaded HTTP server & web dashboard
├── main.py                # Service launcher entry point
├── requirements.txt       # Python dependencies
└── service/
    └── msp-tracker.service # systemd auto-start configuration

msp_routes_cache.json      # Persistent local cache of published flight itineraries
msp_aircraft_db.json       # Persistent local Mode S hex -> airframe database
```

---

## Quick Start on Raspberry Pi

### 1. Clone & Install Dependencies
```bash
cd /home/pi
git clone https://github.com/<your-username>/MSP-RUNWAY-TRACKER.git msp-runway-tracker
cd msp-runway-tracker
pip install -r rpi/requirements.txt
```

### 2. Run Manually
```bash
python3 -m rpi.main
```
The server will start listening on port `8080`:
* **ESP32 JSON Endpoint:** `http://<raspberry-pi-ip>:8080/api/runway_state`
* **Web Dashboard:** `http://<raspberry-pi-ip>:8080/`
* **Health Check:** `http://<raspberry-pi-ip>:8080/api/health`

---

## Configuration Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `MSP_DATA_SOURCE` | `opensky` | Telemetry source: `opensky` or `dump1090` (local SDR). |
| `MSP_POLL_INTERVAL` | `10.0` | Polling interval in seconds when using OpenSky. |
| `MSP_DUMP1090_URL` | `http://localhost:8080/data/aircraft.json` | URL of local dump1090 / readsb feeder on Pi. |
| `MSP_SERVER_HOST` | `0.0.0.0` | Listen IP address for HTTP server. |
| `MSP_SERVER_PORT` | `8080` | Listen port for HTTP server. |
| `OPENSKY_USERNAME` | *(empty)* | Optional OpenSky registered account username for higher rate limits. |
| `OPENSKY_PASSWORD` | *(empty)* | Optional OpenSky registered account password. |

---

## Automatic Boot via Systemd

To run the daemon continuously on Raspberry Pi startup:

1. Copy the systemd service file:
   ```bash
   sudo cp rpi/service/msp-tracker.service /etc/systemd/system/
   ```
2. Reload systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   ```
3. Enable and start the service:
   ```bash
   sudo systemctl enable --now msp-tracker.service
   ```
4. View live service logs:
   ```bash
   journalctl -u msp-tracker.service -f
   ```

---

## JSON Payload Schema (`/api/runway_state`)

```json
{
  "timestamp": 1727845600,
  "iso_time": "2026-10-02T05:25:00+00:00",
  "status": "ACTIVE",
  "source": "OpenSky",
  "tracked_count": 14,
  "active_count": 1,
  "active_operations": [
    {
      "runway": "30R",
      "zone": "12L/30R",
      "action": "LANDING",
      "callsign": "DAL793",
      "airline": "Delta Air Lines",
      "flight_number": "793",
      "flight_label": "Delta Air Lines 793",
      "aircraft_type": "Boeing 737-900",
      "type_code": "B738",
      "route": "From KDEN",
      "altitude_ft": 1450,
      "speed_kts": 135,
      "vertical_rate_fpm": -750,
      "progress": 0.42,
      "cross_track_m": 18.2,
      "heading_deg": 302,
      "lat": 44.885,
      "lon": -93.205
    }
  ],
  "primary_operation": {
    "runway": "30R",
    "action": "LANDING",
    "flight_label": "Delta Air Lines 793",
    "aircraft_type": "Boeing 737-900",
    "route": "From KDEN"
  },
  "runway_summary": {
    "12L": { "status": "IDLE", "callsign": null },
    "30R": { "status": "LANDING", "callsign": "Delta Air Lines 793", "aircraft_type": "Boeing 737-900" },
    "12R": { "status": "IDLE", "callsign": null },
    "30L": { "status": "IDLE", "callsign": null },
    "4":   { "status": "IDLE", "callsign": null },
    "22":  { "status": "IDLE", "callsign": null },
    "17":  { "status": "IDLE", "callsign": null },
    "35":  { "status": "IDLE", "callsign": null }
  }
}
```
