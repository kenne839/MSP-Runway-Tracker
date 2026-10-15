"""
Lightweight, multi-threaded HTTP server providing the REST JSON API for the ESP32
and an embedded real-time web dashboard for manual monitoring.
"""

import os
import json
import gzip
import logging
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from .tracker import TelemetryState
from .config import SERVER_HOST, SERVER_PORT, OFFICE_API_TOKEN
from .theme import THEME_PRESETS

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_DASHBOARD_PATH = os.path.join(_STATIC_DIR, "dashboard.html")


def _load_dashboard_html() -> str:
    """Reads live dashboard HTML from static/dashboard.html."""
    if os.path.exists(_DASHBOARD_PATH):
        try:
            with open(_DASHBOARD_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error("Failed to read dashboard template (%s): %s", _DASHBOARD_PATH, e)
    return "<!DOCTYPE html><html><body><h1>Dashboard Error: static/dashboard.html not found</h1></body></html>"


# Backward compatibility attribute
DASHBOARD_HTML = _load_dashboard_html()


def make_handler(state: TelemetryState):
    class TelemetryHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress noisy periodic HTTP poll log messages in console
            pass

        def _send_cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-KMSP-Token, X-ESP32-RSSI, X-ESP32-Heap, X-ESP32-Uptime, X-ESP32-FPS")

        def do_OPTIONS(self):
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            # Both legacy /api/runway_state and versioned /api/v1/runway_state supported
            if path in ("/api/runway_state", "/api/state", "/api/v1/runway_state", "/api/v1/state"):
                client_ip = self.client_address[0]
                rssi = self.headers.get("X-ESP32-RSSI")
                heap = self.headers.get("X-ESP32-Heap")
                uptime = self.headers.get("X-ESP32-Uptime")
                fps = self.headers.get("X-ESP32-FPS")
                if any((rssi, heap, uptime, fps)):
                    state.update_esp32_heartbeat(client_ip, rssi, heap, uptime, fps)

                snapshot = state.get_snapshot()
                body = json.dumps(snapshot, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path in ("/api/theme", "/api/v1/theme"):
                theme = state.get_theme()
                body = json.dumps(theme, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path in ("/api/theme/presets", "/api/v1/theme/presets"):
                body = json.dumps(THEME_PRESETS, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path in ("/api/health", "/api/v1/health"):
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

            elif path in ("/api/office_replay_batch", "/api/v1/office_replay_batch"):
                # Token authentication check
                if OFFICE_API_TOKEN:
                    auth_token = self.headers.get("X-KMSP-Token") or ""
                    if not auth_token:
                        auth_header = self.headers.get("Authorization", "")
                        if auth_header.startswith("Bearer "):
                            auth_token = auth_header[7:].strip()
                    if auth_token != OFFICE_API_TOKEN:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self._send_cors_headers()
                        self.end_headers()
                        self.wfile.write(b'{"error": "Unauthorized: invalid or missing X-KMSP-Token"}')
                        return

                # Parse query parameters
                query_params = parse_qs(parsed.query)
                window_minutes = 30
                if "window_minutes" in query_params:
                    try:
                        window_minutes = int(query_params["window_minutes"][0])
                    except (ValueError, TypeError):
                        window_minutes = 30

                batch_dict = state.get_replay_batch(window_minutes=window_minutes)
                raw_body = json.dumps(batch_dict, indent=2).encode("utf-8")

                # Wire compression: check Accept-Encoding
                accept_encoding = self.headers.get("Accept-Encoding", "")
                use_gzip = "gzip" in accept_encoding.lower()

                if use_gzip:
                    body = gzip.compress(raw_body)
                else:
                    body = raw_body

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if use_gzip:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            elif path in ("", "/dashboard"):
                html = _load_dashboard_html()
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            elif path in ("/simulator", "/sim"):
                sim_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulator.html")
                if os.path.exists(sim_path):
                    with open(sim_path, "rb") as sf:
                        body = sf.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Simulator not found")

            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"404 Not Found")

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path in ("/api/theme", "/api/v1/theme"):
                content_len = int(self.headers.get("Content-Length", 0))
                post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
                try:
                    data = json.loads(post_body) if post_body else {}
                    updated = state.update_theme(data)
                    body = json.dumps(updated, indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    logger.warning("Failed processing POST /api/theme: %s", e)
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"404 Not Found")

    return TelemetryHandler


def run_server(state: TelemetryState, host=SERVER_HOST, port=SERVER_PORT):
    handler_class = make_handler(state)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    logger.info("MSP Runway Web Server listening on http://%s:%s", host, port)
    logger.info("  -> ESP32 Endpoint: http://%s:%s/api/runway_state", host, port)
    logger.info("  -> Web Dashboard:  http://%s:%s/", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
