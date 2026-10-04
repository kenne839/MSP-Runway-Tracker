"""
Configuration and constants for the MSP Runway Tracker Raspberry Pi daemon.
"""

import os

# ==============================================================================
# GEOGRAPHICAL BOUNDING BOX & COORDINATE SYSTEM
# ==============================================================================

# Geographical bounding box encompassing KMSP terminal airspace (WGS84 lat/lon)
MSP_BBOX = {
    'lamin': 44.800,  # Southern boundary (~Bloomington / Minnesota River)
    'lamax': 44.960,  # Northern boundary (~South Minneapolis / St. Paul)
    'lomin': -93.320, # Western boundary (~Richfield / Edina)
    'lomax': -93.100  # Eastern boundary (~Mendota Heights / Woodbury)
}

# MSP Reference Center for Tangent Plane Projection (WGS84)
MSP_LAT = 44.88
MSP_LON = -93.22

# Planar Tangent Plane Scalers centered at KMSP
# 1 deg latitude ≈ 111,139 m
# 1 deg longitude at 44.88 N ≈ 111,139 * cos(44.88°) = 78,740 m
LAT_TO_M = 111139.0
LON_TO_M = 78740.0

# ==============================================================================
# RUNWAY DEFINITIONS & GEOMETRY
# ==============================================================================

# Physical Runway Centerlines at KMSP:
# Coordinates define threshold endpoints (start, end) and magnetic headings for each end.
RUNWAYS = {
    # Parallel Main Runways (Northwest - Southeast orientation)
    "12L/30R": {
        "start": (44.89295, -93.22099),
        "end":   (44.88125, -93.19397),
        "dirs":  {"12L": 121, "30R": 301}
    },
    "12R/30L": {
        "start": (44.88783, -93.23416),
        "end":   (44.87350, -93.20116),
        "dirs":  {"12R": 121, "30L": 301}
    },
    # Crosswind Runway (Intersects 12L/30R and 12R/30L)
    "4/22": {
        "start": (44.87226, -93.23831),
        "end":   (44.89361, -93.20829),
        "dirs":  {"4": 45, "22": 225}
    },
    # North-South Runway
    "17/35": {
        "start": (44.88775, -93.24225),
        "end":   (44.86617, -93.23664),
        "dirs":  {"17": 170, "35": 350}
    }
}

# ==============================================================================
# KINEMATIC & SPATIAL GATING THRESHOLDS
# ==============================================================================

# Velocity Gate: minimum ground speed to filter stationary / taxi traffic
MIN_VELOCITY_MS = 35.0  # ~68 knots / 78 mph

# Altitude Gate: maximum altitude to filter overflights
MAX_ALTITUDE_M = 1200.0  # ~3,937 ft MSL

# Vertical Rate Gates:
TAKEOFF_MIN_VERT_RATE = 1.5   # m/s (climbing)
LANDING_MAX_VERT_RATE = -1.5  # m/s (descending)

# Spatial Gates:
RUNWAY_MAX_CROSS_TRACK_M = 150.0  # Lateral distance tolerance from extended centerline
DEFAULT_HEADING_TOLERANCE_DEG = 25.0
CRAB_HEADING_TOLERANCE_DEG = 35.0 # Broadened tolerance when close to touchdown centerline

# Corridor projection parameter t:
CORRIDOR_T_MIN = -1.5  # Extended approach capture zone
CORRIDOR_T_MAX = 2.5   # Extended departure capture zone

# ==============================================================================
# DATA SOURCE & POLLING
# ==============================================================================

# Source mode: 'opensky' or 'dump1090' (local SDR receiver)
DATA_SOURCE = os.environ.get("MSP_DATA_SOURCE", "opensky")

# OpenSky API Configuration
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_ROUTES_URL = "https://opensky-network.org/api/routes"
OPENSKY_POLL_INTERVAL = float(os.environ.get("MSP_POLL_INTERVAL", "10.0")) # seconds

# ADS-B DB Routes Configuration (David Taylor / Planebase flight routes)
ADSDB_ROUTES_URL = "https://api.adsbdb.com/v0/callsign"
ROUTE_CACHE_TTL_DAYS = int(os.environ.get("MSP_ROUTE_CACHE_TTL_DAYS", "14"))
ROUTE_CACHE_TTL_SEC = ROUTE_CACHE_TTL_DAYS * 86400  # 14 days = 1,209,600s

# Local ADS-B Feeder (dump1090 / readsb / tar1090) configuration
DUMP1090_URL = os.environ.get("MSP_DUMP1090_URL", "http://localhost:8080/data/aircraft.json")
DUMP1090_POLL_INTERVAL = 1.0  # seconds

# Optional OpenSky Credentials for higher rate limits
OPENSKY_USERNAME = os.environ.get("OPENSKY_USERNAME", "")
OPENSKY_PASSWORD = os.environ.get("OPENSKY_PASSWORD", "")

# ==============================================================================
# SERVER CONFIGURATION
# ==============================================================================

SERVER_HOST = os.environ.get("MSP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("MSP_SERVER_PORT", "8080"))

# ==============================================================================
# STORAGE & CACHING PATHS
# ==============================================================================

RPI_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(RPI_DIR)

def find_file(filename):
    """Finds a file in rpi/data, rpi/, or project root."""
    candidates = [
        os.path.join(RPI_DIR, "data", filename),
        os.path.join(RPI_DIR, filename),
        os.path.join(PROJECT_ROOT, filename)
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[-1]

AIRLINES_FILE = find_file("airlines.json")
AIRFRAMES_FILE = find_file("airframes.json")
AIRCRAFT_DB_FILE = find_file("msp_aircraft_db.json")
ROUTES_CACHE_FILE = find_file("msp_routes_cache.json")

# State output path: prefer Linux RAM disk (/dev/shm) to protect MicroSD card
if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK):
    STATE_FILE = "/dev/shm/msp_runway_state.json"
else:
    STATE_FILE = os.path.join(RPI_DIR, "runway_state.json")
