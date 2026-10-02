"""
Main entry point for the MSP Runway Tracker Raspberry Pi daemon.
Starts the background telemetry pipeline and foreground HTTP web server.
"""

import sys
import os
import signal
import time

# Ensure parent directory is in path when run directly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from rpi.config import SERVER_HOST, SERVER_PORT, DATA_SOURCE
from rpi.tracker import TelemetryState, TelemetryTracker
from rpi.server import run_server

def main():
    print("=" * 65)
    print("  KMSP RUNWAY TRACKER & TELEMETRY DAEMON (Raspberry Pi)")
    print("=" * 65)
    print(f"Data Source: {DATA_SOURCE.upper()}")
    print(f"Target Host: {SERVER_HOST}:{SERVER_PORT}")

    state = TelemetryState()
    tracker = TelemetryTracker(state)

    def shutdown(signum, frame):
        print("\nInitiating graceful shutdown...")
        tracker.stop()
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
        print(f"Server encountered error: {e}")
    finally:
        tracker.stop()

if __name__ == "__main__":
    main()
