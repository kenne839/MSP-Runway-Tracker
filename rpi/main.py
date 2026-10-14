"""
Main entry point for the MSP Runway Tracker Raspberry Pi daemon.
Starts the background telemetry pipeline and foreground HTTP web server.
"""

import sys
import os
import signal
import time
import logging

# Configure structured logging for stdout / journalctl
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("msp.main")

# Ensure parent directory is in path when run directly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from rpi.config import SERVER_HOST, SERVER_PORT, DATA_SOURCE
from rpi.tracker import TelemetryState, TelemetryTracker
from rpi.metadata import MetadataResolver
from rpi.opensky_auth import OpenSkyAuth
from rpi.server import run_server


def main():
    print("=" * 65)
    print("  KMSP RUNWAY TRACKER & TELEMETRY DAEMON (Raspberry Pi)")
    print("=" * 65)
    print(f"Data Source: {DATA_SOURCE.upper()}")
    print(f"Target Host: {SERVER_HOST}:{SERVER_PORT}")

    state = TelemetryState()
    auth = OpenSkyAuth()
    meta = MetadataResolver(auth=auth)
    tracker = TelemetryTracker(state=state, auth=auth, meta=meta)

    def shutdown(signum, frame):
        logger.info("Initiating graceful shutdown...")
        tracker.stop()
        meta.flush_caches()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start background telemetry engine
    tracker.start()

    # Give tracker a moment to log initialization
    time.sleep(0.5)

    # Run HTTP server (blocking on main thread)
    try:
        run_server(state, host=SERVER_HOST, port=SERVER_PORT)
    except Exception as e:
        logger.error("Server encountered fatal error: %s", e)
    finally:
        tracker.stop()
        meta.flush_caches()


if __name__ == "__main__":
    main()
