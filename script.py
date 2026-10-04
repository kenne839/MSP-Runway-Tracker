"""
MSP Runway Traffic Monitor & Telemetry Pipeline
================================================
A geofenced Python telemetry daemon monitoring real-time flight operations
at Minneapolis-St. Paul International Airport (KMSP).

Pipeline Workflow:
1. Poll OpenSky Network ADS-B REST API (/states/all) within the MSP bounding box.
2. Filter aircraft by velocity and altitude gates to ignore ground taxi/overflights.
3. Project aircraft positions onto 2D extended runway centerline vectors.
4. Calculate cross-track offset and heading alignment to assign active runway.
5. Classify flight action (TAKING OFF vs LANDING) based on vertical velocity vector.
6. Resolve airline name (ICAO prefix), aircraft airframe model, and route info.
7. Persist active state to 'runway_state.json' for downstream displays or web UIs.
"""

import math
import requests
import time
import json
import os
import sys
import urllib3

# Suppress SSL certificate warnings when querying endpoints with verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure UTF-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Dynamic path resolution to import rpi modules if needed
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from rpi.weather import get_current_weather
except ImportError:
    get_current_weather = lambda: {
        "flight_category": "VFR",
        "temp_f": 60,
        "wind": "Calm",
        "pressure": "30.00 inHg",
        "condition": "Clear"
    }

LAST_LANDING_RUNWAY = None
LAST_DEPARTURE_RUNWAY = None
ACTIVE_FLOW_GROUP = None

FLOW_GROUP_MAP = {
    "30R": "30", "30L": "30",
    "12L": "12", "12R": "12",
    "4": "4", "22": "22",
    "17": "17", "35": "35"
}

# ==============================================================================
# CONFIGURATION & THRESHOLDS
# ==============================================================================

# Geographical bounding box encompassing KMSP terminal airspace (WGS84 lat/lon)
MSP_BBOX = {
    'lamin': 44.800,  # Southern boundary (~Bloomington / Minnesota River)
    'lamax': 44.960,  # Northern boundary (~South Minneapolis / St. Paul)
    'lomin': -93.320, # Western boundary (~Richfield / Edina)
    'lomax': -93.100  # Eastern boundary (~Mendota Heights / Woodbury)
}

# Kinematic Gating Thresholds:
# - Minimum velocity: 35.0 m/s (~68 knots / 78 mph) filters out taxiing aircraft
#   and ground service vehicles on airport ramps/taxiways.
MIN_VELOCITY_MS = 35.0

# - Maximum altitude: 1200.0 m (~3,937 ft MSL) filters out high-altitude transit
#   flights cruising overhead without interacting with KMSP.
MAX_ALTITUDE_M = 1200.0

# Dynamic filesystem path resolution relative to this script file location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "runway_state.json")
AIRLINES_FILE = os.path.join(BASE_DIR, "airlines.json")
AIRCRAFT_DB_FILE = os.path.join(BASE_DIR, "msp_aircraft_db.json")
AIRFRAMES_FILE = os.path.join(BASE_DIR, "airframes.json")

# ==============================================================================
# STATIC & PERSISTENT DATA TABLES
# ==============================================================================

# Load airline lookup table: Maps 3-letter ICAO callsign prefix to commercial brand
# (e.g., "DAL" -> "Delta Air Lines", "EDV" -> "Endeavor Air", "SKW" -> "SkyWest Airlines")
try:
    with open(AIRLINES_FILE, "r") as f:
        AIRLINES = json.load(f)
except FileNotFoundError:
    print(f"Warning: {AIRLINES_FILE} not found. Airline decoding disabled.")
    AIRLINES = {}

# Load airframe mapping table: Maps raw ICAO aircraft designators to readable names
# (e.g., "BCS1" -> "Airbus A220-100", "A21N" -> "Airbus A321neo", "B738" -> "Boeing 737-800")
try:
    with open(AIRFRAMES_FILE, "r") as f:
        AIRFRAMES = json.load(f)
except FileNotFoundError:
    print(f"Warning: {AIRFRAMES_FILE} not found. Readable airframe decoding disabled.")
    AIRFRAMES = {}

# Load local aircraft database cache: Maps 24-bit ICAO hex transponder codes to type codes
# Pre-populated via seed_database.py (~480,000 aircraft records).
if os.path.exists(AIRCRAFT_DB_FILE):
    with open(AIRCRAFT_DB_FILE, "r") as f:
        AIRCRAFT_DB = json.load(f)
    print(f"Loaded {len(AIRCRAFT_DB):,} aircraft records from {AIRCRAFT_DB_FILE}.")
else:
    AIRCRAFT_DB = {}
    print(f"Notice: {AIRCRAFT_DB_FILE} not found. Creating empty local cache.")

# In-memory session cache for daily flight routes (maps callsign -> origin/destination string).
# Prevents repeated HTTP calls to OpenSky's /api/routes endpoint during a single session.
ROUTE_CACHE = {}

# ==============================================================================
# COORDINATE PROJECTION & RUNWAY GEOMETRY
# ==============================================================================

# Planar Tangent Plane Projection Scalers centered at KMSP (44.88 N, -93.22 W):
# - 1 degree of latitude is approximately 111,139 meters globally.
# - 1 degree of longitude at 44.88 deg N is approx: 111,139 * cos(44.88 deg) = 78,740 meters.
LAT_TO_M = 111139.0
LON_TO_M = 78740.0
MSP_LAT, MSP_LON = 44.88, -93.22

def to_meters(lat, lon):
    """
    Projects WGS84 spherical coordinates (latitude, longitude) into a local
    2D Cartesian coordinate system in meters relative to MSP reference point.

    Args:
        lat (float): Latitude in degrees.
        lon (float): Longitude in degrees.

    Returns:
        tuple[float, float]: Local (x, y) coordinates in meters.
    """
    return (lon - MSP_LON) * LON_TO_M, (lat - MSP_LAT) * LAT_TO_M

# KMSP Physical Runway Centerlines:
# Each runway entry defines:
#   - 'start' & 'end': (latitude, longitude) coordinates of runway physical thresholds.
#   - 'dirs': Mapping of individual runway designators to magnetic heading directions (degrees).
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
    # Crosswind Runway
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
# METADATA RESOLUTION FUNCTIONS
# ==============================================================================

def get_aircraft_type(icao24):
    """
    Resolves the aircraft ICAO type code from its 24-bit Mode-S transponder hex address.

    Resolution Strategy:
    1. Checks local persistent cache (AIRCRAFT_DB).
    2. Fallback: Queries HexDB REST API (https://hexdb.io).
    3. Caches result (including negative hits as 'UNKNOWN') to minimize network overhead.

    Args:
        icao24 (str): 24-bit ICAO transponder hex code (e.g., 'a835af').

    Returns:
        str: ICAO type code (e.g., 'B738', 'A21N') or 'UNKNOWN'.
    """
    if not icao24:
        return "UNKNOWN"
    hex_code = icao24.lower().strip()
    if hex_code in AIRCRAFT_DB:
        return AIRCRAFT_DB[hex_code]

    ac_type = "UNKNOWN"
    try:
        url = f"https://hexdb.io/api/v1/aircraft/{hex_code}"
        res = requests.get(url, timeout=3, verify=False)
        if res.status_code == 200:
            data = res.json()
            ac_type = data.get("ICAOTypeCode") or data.get("Type") or "UNKNOWN"
    except Exception:
        pass 

    # Negative/positive caching to avoid repeating failed lookups
    AIRCRAFT_DB[hex_code] = ac_type
    try:
        with open(AIRCRAFT_DB_FILE, "w") as f:
            json.dump(AIRCRAFT_DB, f)
    except Exception:
        pass

    return ac_type

def get_flight_route(callsign, action):
    """
    Fetches the flight route from OpenSky Network's routes API based on callsign.
    Parses origin airport for arrivals, and destination airport for departures.

    Args:
        callsign (str): Aircraft flight callsign (e.g., 'DAL793').
        action (str): Current movement classification ('LANDING' or 'TAKING OFF').

    Returns:
        str: Human-readable route string (e.g., 'From KDEN', 'To KORD', or 'Unknown').
    """
    if not callsign or callsign == "UNKNOWN":
        return "Unknown"
    
    callsign = callsign.strip()
    
    # Return cached route if already resolved in this session
    if callsign in ROUTE_CACHE:
        return ROUTE_CACHE[callsign]
        
    try:
        url = f"https://opensky-network.org/api/routes?callsign={callsign}"
        res = requests.get(url, timeout=3, verify=False)
        
        if res.status_code == 200:
            data = res.json()
            route = data.get("route", [])
            
            # route format is expected to be [origin_icao, destination_icao]
            if len(route) >= 2:
                origin = route[0]
                dest = route[1]
                
                if action == "LANDING":
                    route_str = f"From {origin}"
                elif action == "TAKING OFF":
                    route_str = f"To {dest}"
                else:
                    route_str = f"{origin} -> {dest}"
                    
                ROUTE_CACHE[callsign] = route_str
                return route_str
    except Exception:
        pass
        
    ROUTE_CACHE[callsign] = "Unknown"
    return "Unknown"

# ==============================================================================
# SPATIAL RUNWAY MATCHING ALGORITHM
# ==============================================================================

def get_active_runway(lat, lon, heading):
    """
    Determines if an aircraft is operating within an active runway corridor ("tube").

    Algorithm:
    1. Project aircraft point P and runway threshold endpoints A, B into metric coordinates.
    2. Form vector AB (runway centerline) and vector AP (aircraft relative to threshold A).
    3. Calculate scalar projection parameter t:
           t = (AP . AB) / |AB|^2
       - t = 0.0 corresponds to runway start threshold A.
       - t = 1.0 corresponds to runway end threshold B.
       - Clamped to [-1.5, 2.5] to extend capture zone for approach funnels and climb-outs.
    4. Compute orthogonal cross-track distance from aircraft to extended centerline.
    5. Check if cross-track distance < 150 meters.
    6. Verify aircraft heading aligns with either directional runway heading within +/- 25 deg.

    Args:
        lat (float): Aircraft latitude.
        lon (float): Aircraft longitude.
        heading (float): Aircraft true track / heading in degrees (0-360).

    Returns:
        str | None: Active runway designator (e.g., '12L', '30R') or None if no match.
    """
    if lat is None or lon is None or heading is None:
        return None
    p_x, p_y = to_meters(lat, lon)
    
    for rw_zone, data in RUNWAYS.items():
        a_x, a_y = to_meters(data["start"][0], data["start"][1])
        b_x, b_y = to_meters(data["end"][0], data["end"][1])
        
        AB_x, AB_y = b_x - a_x, b_y - a_y
        AP_x, AP_y = p_x - a_x, p_y - a_y
        
        dot_AB_AB = AB_x**2 + AB_y**2
        if dot_AB_AB == 0:
            continue
            
        # Scalar projection of aircraft vector onto runway segment
        t = (AP_x * AB_x + AP_y * AB_y) / dot_AB_AB
        # Clamp t to corridor bounds: -1.5 (approach extension) to 2.5 (departure extension)
        t_clamped = max(-1.5, min(2.5, t))
        
        # Closest point on extended runway centerline
        closest_x = a_x + t_clamped * AB_x
        closest_y = a_y + t_clamped * AB_y
        
        # Orthogonal distance from centerline
        cross_track_dist = math.hypot(p_x - closest_x, p_y - closest_y)
        
        # Gating: must be within 150 meters lateral distance of centerline
        if cross_track_dist < 150:
            for rw_name, target_heading in data["dirs"].items():
                # Calculate minimal angular difference accounting for 360-degree boundary wrap
                diff = abs((heading - target_heading + 180) % 360 - 180)
                if diff <= 25:
                    return rw_name
    return None

def get_runway_roles():
    global LAST_LANDING_RUNWAY, LAST_DEPARTURE_RUNWAY
    if LAST_LANDING_RUNWAY and LAST_DEPARTURE_RUNWAY:
        summary = f"ARR {LAST_LANDING_RUNWAY} / DEP {LAST_DEPARTURE_RUNWAY}"
        full_summary = f"LANDING {LAST_LANDING_RUNWAY} / DEPARTURES {LAST_DEPARTURE_RUNWAY}"
    elif LAST_LANDING_RUNWAY:
        summary = f"LANDING: {LAST_LANDING_RUNWAY}"
        full_summary = f"LANDING {LAST_LANDING_RUNWAY} (No Dep Active)"
    elif LAST_DEPARTURE_RUNWAY:
        summary = f"DEPARTURES: {LAST_DEPARTURE_RUNWAY}"
        full_summary = f"DEPARTURES {LAST_DEPARTURE_RUNWAY} (No Arr Active)"
    else:
        summary = "RW: Standby"
        full_summary = "Standby / Waiting for Traffic"
    return {
        "landing": LAST_LANDING_RUNWAY,
        "departure": LAST_DEPARTURE_RUNWAY,
        "summary": summary,
        "full_summary": full_summary
    }

def write_state(runway, action, callsign, aircraft_type, route, tracked_count=0):
    """
    Persists current runway telemetry event, runway roles, and live METAR weather
    to local JSON file for downstream consumers (ESP32 LED displays, dashboards).
    """
    global LAST_LANDING_RUNWAY, LAST_DEPARTURE_RUNWAY, ACTIVE_FLOW_GROUP

    # Update runway operational role tracking
    if runway != "NONE":
        flow_grp = FLOW_GROUP_MAP.get(runway)
        if flow_grp and ACTIVE_FLOW_GROUP and flow_grp != ACTIVE_FLOW_GROUP:
            LAST_LANDING_RUNWAY = None
            LAST_DEPARTURE_RUNWAY = None
        if flow_grp:
            ACTIVE_FLOW_GROUP = flow_grp

        if action == "LANDING":
            LAST_LANDING_RUNWAY = runway
        elif action in ("TAKING OFF", "TAKEOFF"):
            LAST_DEPARTURE_RUNWAY = runway

    roles = get_runway_roles()
    weather = get_current_weather()

    active_ops = []
    if runway != "NONE" and action != "IDLE":
        active_ops.append({
            "runway": runway,
            "action": "TAKEOFF" if "TAKING" in action else action,
            "callsign": callsign,
            "flight_label": callsign,
            "aircraft_type": aircraft_type,
            "route": route
        })

    payload = {
        "active_runway": runway,
        "action": action,
        "callsign": callsign,
        "aircraft_type": aircraft_type,
        "route": route,
        "timestamp": int(time.time()),
        "tracked_count": tracked_count,
        "active_operations": active_ops,
        "weather": weather,
        "runway_roles": roles,
        "runway_roles_summary": roles["summary"]
    }
    with open(STATE_FILE, "w") as f:
        json.dump(payload, f, indent=2)

# ==============================================================================
# MAIN TELEMETRY LOOP
# ==============================================================================

def fetch_msp_traffic():
    """
    Polls OpenSky Network ADS-B API, applies kinematic and spatial filters,
    and updates the active runway state.
    """
    print(f"\n[{time.strftime('%X')}] Fetching MSP traffic...")
    url = 'https://opensky-network.org/api/states/all'
    
    try:
        response = requests.get(url, params=MSP_BBOX, timeout=10, verify=False)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"  -> API Request failed: {e}")
        return

    states = data.get('states')
    if not states:
        print("  -> No aircraft found.")
        write_state("NONE", "IDLE", "", "NONE", "NONE")
        return

    print(f"  -> Tracking {len(states)} aircraft. Checking runway tubes...")
    active_events = 0

    # Parse each aircraft state vector:
    # OpenSky State Vector indices:
    # [0] icao24 (hex), [1] callsign, [5] longitude, [6] latitude,
    # [7] baro_altitude (m), [9] velocity (m/s), [10] true_track (deg), [11] vertical_rate (m/s)
    for plane in states:
        icao24 = str(plane[0]).strip() if plane[0] else None
        raw_callsign = str(plane[1]).strip() if plane[1] else "UNKNOWN"
        lon, lat, altitude = plane[5], plane[6], plane[7]
        velocity, heading, vertical_rate = plane[9], plane[10], plane[11]

        # Kinematic Filter 1: Ignore stationary / slow ground vehicles
        if velocity is None or velocity < MIN_VELOCITY_MS:
            continue

        # Kinematic Filter 2: Ignore high-altitude overflights
        if altitude is None or altitude > MAX_ALTITUDE_M:
            continue
            
        # Spatial Filter: Project onto extended runway corridors
        assigned_runway = get_active_runway(lat, lon, heading)
        if not assigned_runway:
            continue

        # Movement Classification: Require positive or negative vertical rate
        if vertical_rate and vertical_rate > 1.5:
            action = "TAKING OFF"
        elif vertical_rate and vertical_rate < -1.5:
            action = "LANDING"
        else:
            # Aircraft flying completely level inside corridor is ignored
            continue

        active_events += 1

        # Resolve Route Information (From/To)
        flight_route = get_flight_route(raw_callsign, action)

        # Resolve Airline Name from 3-letter ICAO prefix
        airline_code = raw_callsign[:3]
        if airline_code in AIRLINES:
            airline_name = AIRLINES[airline_code]
            flight_num = raw_callsign[3:].strip()
            flight_str = f"{airline_name} {flight_num}".strip()
        else:
            flight_str = raw_callsign

        # Resolve Aircraft Type and Translate to Human-Readable Format
        raw_ac_type = get_aircraft_type(icao24)
        readable_ac_type = AIRFRAMES.get(raw_ac_type, raw_ac_type)
        
        # Unit Conversions for Console Telemetry Output
        speed_kts = int(velocity * 1.94384)
        alt_ft = int(altitude * 3.28084)
        route_display = f"({flight_route})" if flight_route != "Unknown" else ""
        
        print(f"  ✈  MATCH! [{readable_ac_type}] {flight_str} {route_display} | "
              f"{action} on {assigned_runway} | Spd: {speed_kts}kts, Alt: {alt_ft}ft")
        
        write_state(assigned_runway, action, flight_str, readable_ac_type, flight_route, tracked_count=len(states))

    # When no active takeoffs/landings are matched, reset state to idle
    if active_events == 0:
        print("  -> No active operations detected right now.")
        write_state("NONE", "IDLE", "", "NONE", "NONE", tracked_count=len(states))

if __name__ == "__main__":
    print("Starting MSP Runway Tracker. Press Ctrl+C to stop.")
    while True:
        fetch_msp_traffic()
        time.sleep(30)