"""
Lightweight, multi-threaded HTTP server providing the REST JSON API for the ESP32
and an embedded real-time web dashboard for manual monitoring.
"""

import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from .tracker import TelemetryState
from .config import SERVER_HOST, SERVER_PORT

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KMSP Runway LED Tracker - Live Dashboard</title>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #151d30;
            --text-color: #e2e8f0;
            --text-muted: #94a3b8;
            --accent-green: #10b981;
            --accent-blue: #38bdf8;
            --accent-amber: #f59e0b;
            --accent-red: #ef4444;
            --border: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
        body { background: var(--bg-color); color: var(--text-color); padding: 24px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
        h1 { font-size: 1.5rem; letter-spacing: -0.5px; }
        .badge { background: #1e293b; color: var(--accent-green); padding: 4px 10px; border-radius: 999px; font-size: 0.85rem; font-weight: bold; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
        .card h3 { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; }
        .metric { font-size: 1.7rem; font-weight: bold; }
        .runway-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
        @media(max-width: 600px) { .runway-grid { grid-template-columns: repeat(2, 1fr); } }
        .rw-box { background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px; text-align: center; }
        .rw-name { font-size: 1.2rem; font-weight: bold; margin-bottom: 4px; }
        .rw-status { font-size: 0.8rem; font-weight: bold; }
        .status-LANDING { color: var(--accent-green); }
        .status-TAKEOFF { color: var(--accent-amber); }
        .status-IDLE { color: var(--text-muted); }
        table { width: 100%; border-collapse: collapse; background: var(--card-bg); border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
        th, td { padding: 12px 16px; text-align: left; font-size: 0.9rem; }
        th { background: #1a243c; color: var(--text-muted); text-transform: uppercase; font-size: 0.75rem; }
        tr:not(:last-child) { border-bottom: 1px solid var(--border); }
        .empty { text-align: center; color: var(--text-muted); padding: 32px; }
        .footer { margin-top: 24px; font-size: 0.8rem; color: var(--text-muted); text-align: center; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>MSP Runway Live Telemetry</h1>
            <p style="color: var(--text-muted); font-size: 0.85rem; margin-top: 4px;">Hardware Telemetry Server &bull; ESP32-S3 Display Hub</p>
        </div>
        <div>
            <span id="liveBadge" class="badge">LIVE</span>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h3>Active Movements</h3>
            <div id="activeCount" class="metric">0</div>
        </div>
        <div class="card">
            <h3>Airspace Traffic Tracked</h3>
            <div id="trackedCount" class="metric">0</div>
        </div>
        <div class="card">
            <h3>Telemetry Source</h3>
            <div id="telemetrySource" class="metric" style="font-size: 1.3rem;">OpenSky</div>
        </div>
        <div class="card">
            <h3>Last Update</h3>
            <div id="lastUpdate" class="metric" style="font-size: 1.1rem; color: var(--accent-blue);">--:--:--</div>
        </div>
    </div>

    <h2 style="font-size: 1.1rem; margin-bottom: 12px;">Runway Status Summary</h2>
    <div id="runwayGrid" class="runway-grid"></div>

    <h2 style="font-size: 1.1rem; margin-bottom: 12px;">Active Movement Details</h2>
    <table>
        <thead>
            <tr>
                <th>Runway</th>
                <th>Action</th>
                <th>Callsign</th>
                <th>Aircraft</th>
                <th>Route</th>
                <th>Altitude</th>
                <th>Speed</th>
                <th>Corridor Offset</th>
            </tr>
        </thead>
        <tbody id="opsTableBody">
            <tr><td colspan="8" class="empty">No active takeoff or landing operations detected in KMSP runway corridors.</td></tr>
        </tbody>
    </table>

    <div class="footer">
        Endpoint for ESP32: <code>/api/runway_state</code> &bull; Polled in real-time
    </div>

    <script>
        async function refresh() {
            try {
                const res = await fetch('/api/runway_state');
                const data = await res.json();
                
                document.getElementById('activeCount').innerText = data.active_count;
                document.getElementById('trackedCount').innerText = data.tracked_count;
                document.getElementById('telemetrySource').innerText = data.source;
                document.getElementById('lastUpdate').innerText = new Date(data.timestamp * 1000).toLocaleTimeString();

                // Render Runways
                const rwContainer = document.getElementById('runwayGrid');
                let rwHtml = '';
                for (const [rw, info] of Object.entries(data.runway_summary || {})) {
                    rwHtml += `
                        <div class="rw-box">
                            <div class="rw-name">${rw}</div>
                            <div class="rw-status status-${info.status}">${info.status}</div>
                            ${info.callsign ? `<div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">${info.callsign}</div>` : ''}
                        </div>
                    `;
                }
                rwContainer.innerHTML = rwHtml;

                // Render Table
                const tbody = document.getElementById('opsTableBody');
                if (!data.active_operations || data.active_operations.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="empty">No active takeoff or landing operations detected in KMSP runway corridors.</td></tr>';
                } else {
                    let rows = '';
                    for (const op of data.active_operations) {
                        rows += `
                            <tr>
                                <td><b>${op.runway}</b></td>
                                <td><span class="rw-status status-${op.action}">${op.action}</span></td>
                                <td>${op.flight_label}</td>
                                <td>${op.aircraft_type} <span style="color:var(--text-muted); font-size:0.75rem;">(${op.type_code})</span></td>
                                <td>${op.route}</td>
                                <td>${op.altitude_ft} ft</td>
                                <td>${op.speed_kts} kts</td>
                                <td>${op.cross_track_m}m</td>
                            </tr>
                        `;
                    }
                    tbody.innerHTML = rows;
                }
            } catch (err) {
                console.error("Refresh failed", err);
                document.getElementById('liveBadge').innerText = "DISCONNECTED";
                document.getElementById('liveBadge').style.color = "var(--accent-red)";
            }
        }
        setInterval(refresh, 2000);
        refresh();
    </script>
</body>
</html>
"""

def make_handler(state: TelemetryState):
    class TelemetryHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress noisy HTTP poll log messages in console
            pass

        def _send_cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path in ("/api/runway_state", "/api/state"):
                snapshot = state.get_snapshot()
                body = json.dumps(snapshot, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path == "/api/health":
                snapshot = state.get_snapshot()
                health = {
                    "status": "healthy",
                    "timestamp": snapshot.get("timestamp"),
                    "source": snapshot.get("source"),
                    "tracked_aircraft": snapshot.get("tracked_count"),
                    "active_operations": snapshot.get("active_count")
                }
                body = json.dumps(health).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path in ("", "/dashboard"):
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"404 Not Found")

    return TelemetryHandler


def run_server(state: TelemetryState, host=SERVER_HOST, port=SERVER_PORT):
    handler_class = make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    print(f"MSP Runway Web Server listening on http://{host}:{port}")
    print(f"  -> ESP32 Endpoint: http://{host}:{port}/api/runway_state")
    print(f"  -> Web Dashboard:  http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
