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
import re
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

try:
    from rpi.atis import get_current_datis
except ImportError:
    get_current_datis = lambda: None

try:
    from rpi.opensky_auth import OpenSkyAuth
    opensky_auth = OpenSkyAuth()
except ImportError:
    class DummyAuth:
        def get_headers(self): return {}
        def get_basic_auth(self): return None
        def is_authenticated(self): return False
        def get_auth_status_str(self): return "Anonymous | Quota: 400 req/day"
    opensky_auth = DummyAuth()

OPENSKY_LOCKOUT_UNTIL = 0.0

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
ROUTES_CACHE_FILE = os.path.join(BASE_DIR, "msp_routes_cache.json")

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

# Load local persistent routes database: Maps callsigns to origin/destination
if os.path.exists(ROUTES_CACHE_FILE):
    try:
        with open(ROUTES_CACHE_FILE, "r", encoding="utf-8") as f:
            ROUTES_DB = json.load(f)
        print(f"Loaded {len(ROUTES_DB):,} flight routes from {ROUTES_CACHE_FILE}.")
    except Exception:
        ROUTES_DB = {}
else:
    ROUTES_DB = {}

# In-memory session cache for daily flight routes (maps callsign:action -> formatted route string).
ROUTE_CACHE = {}
ROUTE_CACHE_TTL_DAYS = 14
ROUTE_CACHE_TTL_SEC = ROUTE_CACHE_TTL_DAYS * 86400  # 14 days = 1,209,600s

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

def get_aircraft_type(icao24, callsign=None):
    """
    Resolves the aircraft ICAO type code from its 24-bit Mode-S transponder hex address.
    Multi-source fallback: Local DB -> HexDB -> ADS-B DB -> Callsign Flight Plan.
    """
    if not icao24 and not callsign:
        return "UNKNOWN"
    hex_code = icao24.lower().strip() if icao24 else None
    if hex_code and hex_code in AIRCRAFT_DB:
        return AIRCRAFT_DB[hex_code]

    ac_type = "UNKNOWN"
    if hex_code:
        try:
            url = f"https://hexdb.io/api/v1/aircraft/{hex_code}"
            res = requests.get(url, timeout=3, verify=False)
            if res.status_code == 200:
                data = res.json()
                resolved = data.get("ICAOTypeCode") or data.get("Type")
                if resolved and resolved.strip().upper() != "UNKNOWN":
                    ac_type = resolved.strip().upper()
        except Exception:
            pass 

        if ac_type == "UNKNOWN":
            try:
                url = f"https://api.adsbdb.com/v0/aircraft/{hex_code}"
                res = requests.get(url, timeout=3)
                if res.status_code == 200:
                    data = res.json()
                    ac = data.get("response", {}).get("aircraft", {})
                    resolved = ac.get("icao_type") or ac.get("type")
                    if resolved and resolved.strip().upper() != "UNKNOWN":
                        ac_type = resolved.strip().upper()
            except Exception:
                pass

    if ac_type == "UNKNOWN" and callsign:
        cached_route = ROUTES_DB.get(callsign.strip().upper())
        if isinstance(cached_route, dict) and cached_route.get("aircraft_type"):
            resolved = cached_route["aircraft_type"]
            if resolved and resolved.strip().upper() != "UNKNOWN":
                ac_type = resolved.strip().upper()

    if hex_code and ac_type != "UNKNOWN":
        AIRCRAFT_DB[hex_code] = ac_type
        try:
            with open(AIRCRAFT_DB_FILE, "w") as f:
                json.dump(AIRCRAFT_DB, f)
        except Exception:
            pass

    return ac_type

def is_msp_airport(code):
    """Returns True if the airport code represents Minneapolis-St. Paul (MSP or KMSP)."""
    if not code:
        return False
    return str(code).strip().upper() in ("MSP", "KMSP")

def clean_airport_code(code):
    """Strips leading 'K' on 4-letter US ICAO codes for cleaner 3-letter IATA displays."""
    if not code:
        return ""
    c = str(code).strip().upper()
    if len(c) == 4 and c.startswith("K"):
        return c[1:]
    if len(c) == 4 and c.startswith("C") and c[1:] in ("YYZ", "YVR", "YYC", "YUL", "YEG", "YOW", "YWG", "YHZ"):
        return c[1:]
    return c

def format_route_string(origin, dest, action, allow_turnaround=True):
    """
    Strict MSP Anchor Sanity Check:
    A flight movement at MSP can ONLY have a route that touches MSP.
    If neither origin nor destination is MSP/KMSP, the route is rejected (returns None).
    Handles MSP hub logic (arrivals vs departures) and turnaround leg inference.
    """
    if not origin or not dest:
        return None

    orig_clean = str(origin).strip().upper()
    dest_clean = str(dest).strip().upper()

    is_orig_msp = is_msp_airport(orig_clean)
    is_dest_msp = is_msp_airport(dest_clean)

    # REJECT any itinerary that does not touch Minneapolis-St. Paul!
    if not is_orig_msp and not is_dest_msp:
        return None

    orig_disp = clean_airport_code(orig_clean)
    dest_disp = clean_airport_code(dest_clean)
    act = str(action).strip().upper() if action else ""

    if act == "LANDING":
        if is_dest_msp and not is_orig_msp:
            return f"From {orig_disp}"
        elif is_orig_msp and is_dest_msp:
            return f"From {orig_disp}"
        elif is_orig_msp and not is_dest_msp and allow_turnaround:
            # Turnaround / paired leg in DB (e.g. flight scheduled MSP->XYZ returning)
            return f"From {dest_disp}"
        return None

    elif act in ("TAKING OFF", "TAKEOFF"):
        if is_orig_msp and not is_dest_msp:
            return f"To {dest_disp}"
        elif is_orig_msp and is_dest_msp:
            return f"To {dest_disp}"
        elif is_dest_msp and not is_orig_msp and allow_turnaround:
            # Turnaround / paired leg in DB (e.g. scheduled XYZ->MSP outbound)
            return f"To {orig_disp}"
        return None

    else:
        if is_orig_msp or is_dest_msp:
            return f"{orig_disp} -> {dest_disp}"
        return None

FLIGHTAWARE_LOCKOUT_UNTIL = 0.0

def query_flightaware(callsign, action=None):
    """
    Queries FlightAware live tracking page for real-time filed flight plan.
    Extracts trackpollBootstrap JSON and looks for active legs touching MSP.
    Prioritizes the flight leg matching the requested action (TAKEOFF vs LANDING).
    Returns: (origin, destination, airline_name) or None
    """
    global FLIGHTAWARE_LOCKOUT_UNTIL
    if not callsign or callsign == "UNKNOWN":
        return None

    if time.time() < FLIGHTAWARE_LOCKOUT_UNTIL:
        return None

    try:
        url = f"https://www.flightaware.com/live/flight/{callsign}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code in (429, 403):
            FLIGHTAWARE_LOCKOUT_UNTIL = time.time() + 600.0
            print(f"[FlightAware] Notice: HTTP {res.status_code} detected. Cooldown activated for 10 min.")
            return None
        if res.status_code != 200:
            return None

        match = re.search(r"trackpollBootstrap\s*=\s*(\{.+?\});", res.text)
        if not match:
            return None

        data = json.loads(match.group(1))
        flights = data.get("flights", {})
        if not flights:
            return None

        act = str(action).strip().upper() if action else ""
        candidate_legs = []

        for fid, fval in flights.items():
            orig = fval.get("origin", {}).get("iata") or fval.get("origin", {}).get("icao")
            dest = fval.get("destination", {}).get("iata") or fval.get("destination", {}).get("icao")
            airline = fval.get("airline", {}).get("fullName") or ""
            aircraft_type = fval.get("aircraft", {}).get("type") or fval.get("aircraftType") or ""

            if orig and dest:
                orig_str = str(orig).upper()
                dest_str = str(dest).upper()
                is_orig = is_msp_airport(orig_str)
                is_dest = is_msp_airport(dest_str)
                if is_orig or is_dest:
                    candidate_legs.append((orig_str, dest_str, airline, aircraft_type, is_orig, is_dest))

        if not candidate_legs:
            return None

        # Primary match: leg whose direction matches the current action
        if act in ("TAKEOFF", "TAKING OFF"):
            for orig_s, dest_s, airl, ac_t, is_o, is_d in candidate_legs:
                if is_o:
                    return orig_s, dest_s, airl, ac_t
        elif act == "LANDING":
            for orig_s, dest_s, airl, ac_t, is_o, is_d in candidate_legs:
                if is_d:
                    return orig_s, dest_s, airl, ac_t

        # Secondary match: first leg touching MSP
        orig_s, dest_s, airl, ac_t, _, _ = candidate_legs[0]
        return orig_s, dest_s, airl, ac_t

    except Exception:
        return None

CHARTER_CACHE_TTL_SEC = 21600  # 6 hours for ad-hoc charter/ferry operations

def is_charter_or_special(callsign):
    """
    Identifies whether a callsign represents an ad-hoc charter, sports team charter,
    military contract flight, or repositioning/ferry flight (e.g. 8000-9999 series).
    These operations do not have permanent city pairs, so static databases (like adsbdb.com)
    frequently contain stale historical legs from previous missions (e.g. SCX8457 SWF vs SDF).
    """
    if not callsign or len(callsign) < 3:
        return False
    c = callsign.strip().upper()
    m = re.search(r"(\d+)$", c)
    if m:
        val = int(m.group(1))
        if val >= 8000:
            return True
    return False

def get_flight_route(callsign, action):
    """
    Multi-tier route resolver with Strict MSP Anchor Sanity Check:
    1. In-memory session cache (0ms)
    2. Charter / Special flight check (8000-9999 series): Always query live FlightAware first!
    3. Persistent on-disk routes DB (msp_routes_cache.json) (0ms if fresh < 14 days and valid MSP anchor)
    4. ADS-B DB API (adsbdb.com) (~150ms; only accepted if it touches MSP)
    5. FlightAware live real-time flight plan scraper (~400ms; parses filed FAA/ADS-B flight plan)
    6. OpenSky Network API (/api/routes) fallback (~250ms; only accepted if it touches MSP)
    7. Stale Fallback: If network lookup fails, retains existing cached itinerary IF it touches MSP.
    8. Negative caching to prevent repeated network spam for unresolvable callsigns.
    """
    if not callsign or callsign == "UNKNOWN":
        return "Unknown"

    callsign = callsign.strip().upper()
    cache_key = f"{callsign}:{action}"

    if cache_key in ROUTE_CACHE:
        return ROUTE_CACHE[cache_key]

    now = time.time()
    is_charter = is_charter_or_special(callsign)
    cached_entry = ROUTES_DB.get(callsign)

    # 1. Charter Flights (8000-9999 series): Prioritize live FlightAware FAA flight plan!
    # Static databases like adsbdb.com store old historical charter legs (e.g. SWF instead of SDF).
    if is_charter:
        fa_data = query_flightaware(callsign)
        if fa_data:
            orig_fa, dest_fa, airline_fa, *ac_extra = fa_data
            ac_type_fa = ac_extra[0] if ac_extra else ""
            formatted = format_route_string(orig_fa, dest_fa, action)
            if formatted is not None:
                ROUTES_DB[callsign] = {
                    "origin": clean_airport_code(orig_fa),
                    "destination": clean_airport_code(dest_fa),
                    "airline": airline_fa,
                    "aircraft_type": ac_type_fa,
                    "updated": int(now),
                    "is_charter": True
                }
                try:
                    with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(ROUTES_DB, f, indent=2)
                except Exception:
                    pass
                ROUTE_CACHE[cache_key] = formatted
                return formatted

        # If FlightAware has no active flight plan, check fresh same-day charter cache (< 6 hours)
        if cached_entry and isinstance(cached_entry, dict):
            origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
            dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
            last_updated = cached_entry.get("updated", 0)
            age = now - last_updated
            if age < CHARTER_CACHE_TTL_SEC:
                formatted = format_route_string(origin, dest, action)
                if formatted is not None:
                    ROUTE_CACHE[cache_key] = formatted
                    return formatted

    # 2. Check persistent on-disk routes database for verified flights
    if not is_charter and cached_entry:
        if isinstance(cached_entry, dict):
            origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
            dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
            last_updated = cached_entry.get("updated", 0)
            age = now - last_updated
            is_fa_verified = (cached_entry.get("source") == "flightaware")

            formatted = format_route_string(origin, dest, action, allow_turnaround=False)
            if formatted is None:
                # Direction mismatch (e.g. cached arrival leg SAV->MSP for a departure).
                # Bypass cache so live FlightAware flight plan is queried!
                cached_entry = None
            elif is_fa_verified and age < ROUTE_CACHE_TTL_SEC:
                ROUTE_CACHE[cache_key] = formatted
                return formatted

        elif isinstance(cached_entry, str) and cached_entry and cached_entry != "Unknown":
            cached_entry = None

    # 3. Query FlightAware LIVE REAL-TIME FAA flight plan first!
    # FlightAware queries the active filed FAA radar flight plan (e.g. DAL2295 MSP->FAR, DAL2594 PHL->MSP, DAL1711 MSP->GRR).
    # Static databases like adsbdb.com frequently have outdated schedules from previous seasons (e.g. DAL2295 MSP->SFO).
    fa_data = query_flightaware(callsign, action)
    if fa_data:
        orig_fa, dest_fa, airline_fa, *ac_extra = fa_data
        ac_type_fa = ac_extra[0] if ac_extra else ""
        formatted = format_route_string(orig_fa, dest_fa, action, allow_turnaround=False)
        if formatted is not None:
            ROUTES_DB[callsign] = {
                "origin": clean_airport_code(orig_fa),
                "destination": clean_airport_code(dest_fa),
                "airline": airline_fa,
                "aircraft_type": ac_type_fa,
                "updated": int(now),
                "source": "flightaware"
            }
            try:
                with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(ROUTES_DB, f, indent=2)
            except Exception:
                pass
            ROUTE_CACHE[cache_key] = formatted
            return formatted

    # 4. Fallback: If FlightAware had no active flight, check existing cached entry if fresh
    if cached_entry and isinstance(cached_entry, dict):
        origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
        dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
        last_updated = cached_entry.get("updated", 0)
        age = now - last_updated
        formatted = format_route_string(origin, dest, action, allow_turnaround=False)
        if formatted is not None and age < ROUTE_CACHE_TTL_SEC:
            ROUTE_CACHE[cache_key] = formatted
            return formatted

    # 5. Fallback: Query adsbdb.com API (secondary fallback if FlightAware has no live data)
    try:
        url = f"https://api.adsbdb.com/v0/callsign/{callsign}"
        res = requests.get(url, headers={"User-Agent": "MSP-Runway-Tracker/1.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            fr = data.get("response", {}).get("flightroute", {})
            if fr and fr.get("origin") and fr.get("destination"):
                orig_iata = fr["origin"].get("iata_code") or fr["origin"].get("icao_code")
                dest_iata = fr["destination"].get("iata_code") or fr["destination"].get("icao_code")
                if orig_iata and dest_iata:
                    formatted = format_route_string(orig_iata, dest_iata, action, allow_turnaround=False)
                    if formatted is not None:
                        ROUTES_DB[callsign] = {
                            "origin": clean_airport_code(orig_iata),
                            "destination": clean_airport_code(dest_iata),
                            "airline": fr.get("airline", {}).get("name", ""),
                            "updated": int(now),
                            "source": "adsbdb"
                        }
                        try:
                            with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                                json.dump(ROUTES_DB, f, indent=2)
                        except Exception:
                            pass
                        ROUTE_CACHE[cache_key] = formatted
                        return formatted
    except Exception:
        pass

    # 4. Fallback: Query OpenSky routes API
    try:
        url = f"https://opensky-network.org/api/routes?callsign={callsign}"
        res = requests.get(url, timeout=3, verify=False)
        if res.status_code == 200:
            data = res.json()
            route = data.get("route", [])
            if len(route) >= 2:
                origin, dest = route[0], route[1]
                formatted = format_route_string(origin, dest, action, allow_turnaround=False)
                if formatted is not None:
                    ROUTES_DB[callsign] = {
                        "origin": clean_airport_code(origin),
                        "destination": clean_airport_code(dest),
                        "updated": int(now)
                    }
                    try:
                        with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(ROUTES_DB, f, indent=2)
                    except Exception:
                        pass
                    ROUTE_CACHE[cache_key] = formatted
                    return formatted
    except Exception:
        pass

    # 5. Graceful Stale Fallback: If network query failed but we have a valid stale MSP entry, keep using it!
    if cached_entry and isinstance(cached_entry, dict):
        origin = cached_entry.get("origin") or cached_entry.get("origin_icao") or ""
        dest = cached_entry.get("destination") or cached_entry.get("dest_icao") or ""
        formatted = format_route_string(origin, dest, action, allow_turnaround=False)
        if formatted is None:
            formatted = format_route_string(origin, dest, action, allow_turnaround=True)
        if formatted is not None:
            cached_entry["updated"] = int(now - ROUTE_CACHE_TTL_SEC + 86400)
            ROUTE_CACHE[cache_key] = formatted
            return formatted

    # 6. Negative caching in memory only (never persist "Unknown" or non-MSP routes to disk)
    ROUTE_CACHE[cache_key] = "Unknown"
    return "Unknown"

# ==============================================================================
# SPATIAL RUNWAY MATCHING ALGORITHM
# ==============================================================================

def get_active_runway(lat, lon, heading, vertical_rate=None):
    """
    Determines if an aircraft is operating within an active runway corridor ("tube").
    Supports expanding departure fan for climbing aircraft executing initial departure turns.

    Args:
        lat (float): Aircraft latitude.
        lon (float): Aircraft longitude.
        heading (float): Aircraft true track / heading in degrees (0-360).
        vertical_rate (float): Aircraft vertical speed in m/s (optional, activates departure fan).

    Returns:
        str | None: Active runway designator (e.g., '12L', '30R') or None if no match.
    """
    if lat is None or lon is None or heading is None:
        return None
    p_x, p_y = to_meters(lat, lon)
    is_climbing = (vertical_rate is not None and vertical_rate >= 1.5)
    candidates = []
    
    for rw_zone, data in RUNWAYS.items():
        a_x, a_y = to_meters(data["start"][0], data["start"][1])
        b_x, b_y = to_meters(data["end"][0], data["end"][1])
        
        AB_x, AB_y = b_x - a_x, b_y - a_y
        AP_x, AP_y = p_x - a_x, p_y - a_y
        
        dot_AB_AB = AB_x**2 + AB_y**2
        if dot_AB_AB == 0:
            continue
            
        rw_len = math.sqrt(dot_AB_AB)
        # Scalar projection of aircraft vector onto runway segment
        t = (AP_x * AB_x + AP_y * AB_y) / dot_AB_AB
        # Clamp t to corridor bounds: -2.0 (approach/departure extension) to 3.0
        t_clamped = max(-2.0, min(3.0, t))
        
        # Closest point on extended runway centerline
        closest_x = a_x + t_clamped * AB_x
        closest_y = a_y + t_clamped * AB_y
        
        # Orthogonal distance from centerline
        cross_track_dist = math.hypot(p_x - closest_x, p_y - closest_y)
        
        for rw_name, target_heading in data["dirs"].items():
            is_reverse = (target_heading > 180)
            in_dep_zone = (t < 0.2) if is_reverse else (t > 0.8)
            dep_dist_m = max(0.0, -t * rw_len) if is_reverse else max(0.0, (t - 1.0) * rw_len)

            if is_climbing and in_dep_zone:
                max_xtrack = min(900.0, 150.0 + dep_dist_m * 0.35)
                max_hdg_diff = 85.0
            elif cross_track_dist < 60.0 and -0.2 <= t <= 0.8:
                max_xtrack = 150.0
                max_hdg_diff = 35.0
            else:
                max_xtrack = 150.0
                max_hdg_diff = 25.0

            if cross_track_dist > max_xtrack:
                continue

            # Calculate minimal angular difference accounting for 360-degree boundary wrap
            diff = abs((heading - target_heading + 180) % 360 - 180)
            if diff <= max_hdg_diff:
                candidates.append((cross_track_dist, diff, rw_name))

    if candidates:
        candidates.sort(key=lambda c: (c[0], c[1]))
        return candidates[0][2]
    return None

def get_runway_roles():
    global LAST_LANDING_RUNWAY, LAST_DEPARTURE_RUNWAY
    datis = get_current_datis() if callable(get_current_datis) else None
    closed_runways = datis.get("closed_runways", []) if datis else []
    atis_code = datis.get("code") if datis else None

    if LAST_LANDING_RUNWAY and LAST_DEPARTURE_RUNWAY:
        summary = f"ARR {LAST_LANDING_RUNWAY} / DEP {LAST_DEPARTURE_RUNWAY}"
        full_summary = f"LANDING {LAST_LANDING_RUNWAY} / DEPARTURES {LAST_DEPARTURE_RUNWAY}"
        landing_role = LAST_LANDING_RUNWAY
        dep_role = LAST_DEPARTURE_RUNWAY
        source_role = "ADS-B"
    elif LAST_LANDING_RUNWAY:
        summary = f"LANDING: {LAST_LANDING_RUNWAY}"
        full_summary = f"LANDING {LAST_LANDING_RUNWAY} (No Dep Active)"
        landing_role = LAST_LANDING_RUNWAY
        dep_role = None
        source_role = "ADS-B"
    elif LAST_DEPARTURE_RUNWAY:
        summary = f"DEPARTURES: {LAST_DEPARTURE_RUNWAY}"
        full_summary = f"DEPARTURES {LAST_DEPARTURE_RUNWAY} (No Arr Active)"
        landing_role = None
        dep_role = LAST_DEPARTURE_RUNWAY
        source_role = "ADS-B"
    elif datis and datis.get("valid"):
        summary = datis["summary"]
        full_summary = datis["full_summary"]
        landing_role = datis.get("primary_arr")
        dep_role = datis.get("primary_dep")
        source_role = "D-ATIS"
    else:
        summary = "RW: Standby"
        full_summary = "Standby / Waiting for Traffic"
        landing_role = None
        dep_role = None
        source_role = "STANDBY"

    return {
        "landing": landing_role,
        "departure": dep_role,
        "summary": summary,
        "full_summary": full_summary,
        "source": source_role,
        "closed_runways": closed_runways,
        "atis_code": atis_code
    }

HELD_OPERATIONS = {}

def write_state(runway, action, callsign, aircraft_type, route, tracked_count=0, active_operations=None):
    """
    Persists current runway telemetry event, runway roles, and live METAR weather
    to local JSON file for downstream consumers (ESP32 LED displays, dashboards).
    """
    global LAST_LANDING_RUNWAY, LAST_DEPARTURE_RUNWAY, ACTIVE_FLOW_GROUP

    ops = list(active_operations) if active_operations else []
    if not ops and runway != "NONE" and action != "IDLE":
        ops.append({
            "runway": runway,
            "action": "TAKEOFF" if "TAKING" in action else action,
            "callsign": callsign,
            "flight_label": callsign,
            "aircraft_type": aircraft_type,
            "route": route
        })

    # Update runway operational role tracking from all active operations
    for op in ops:
        rw = op.get("runway")
        act = op.get("action")
        if rw and rw != "NONE":
            flow_grp = FLOW_GROUP_MAP.get(rw)
            if flow_grp and ACTIVE_FLOW_GROUP and flow_grp != ACTIVE_FLOW_GROUP:
                LAST_LANDING_RUNWAY = None
                LAST_DEPARTURE_RUNWAY = None
            if flow_grp:
                ACTIVE_FLOW_GROUP = flow_grp

            if act == "LANDING":
                LAST_LANDING_RUNWAY = rw
            elif act in ("TAKING OFF", "TAKEOFF"):
                LAST_DEPARTURE_RUNWAY = rw

    roles = get_runway_roles()
    weather = get_current_weather()

    # Determine primary operation (prioritize LANDING)
    primary = None
    if ops:
        landings = [o for o in ops if o.get("action") == "LANDING"]
        primary = landings[0] if landings else ops[0]

    primary_rw = primary["runway"] if primary else runway
    primary_act = primary["action"] if primary else action
    primary_call = primary["flight_label"] if primary else callsign
    primary_type = primary["aircraft_type"] if primary else aircraft_type
    primary_route = primary["route"] if primary else route

    # Build runway summary for display consumers
    ALL_RUNWAYS = ["12L", "12R", "30L", "30R", "4", "22", "17", "35"]
    runway_summary = {r: {"status": "IDLE", "callsign": None} for r in ALL_RUNWAYS}
    for op in ops:
        rw = op.get("runway")
        if rw in runway_summary:
            prev = runway_summary[rw].get("status")
            if prev == "LANDING" and op.get("action") != "LANDING":
                continue
            runway_summary[rw] = {
                "status": op.get("action"),
                "callsign": op.get("flight_label"),
                "aircraft_type": op.get("aircraft_type")
            }

    payload = {
        "active_runway": primary_rw,
        "action": primary_act,
        "callsign": primary_call,
        "aircraft_type": primary_type,
        "route": primary_route,
        "timestamp": int(time.time()),
        "updated_time": time.strftime("%I:%M:%S%p"),
        "tracked_count": tracked_count,
        "active_operations": ops,
        "runway_summary": runway_summary,
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
    global OPENSKY_LOCKOUT_UNTIL
    now = time.time()
    if now < OPENSKY_LOCKOUT_UNTIL:
        remaining = int(OPENSKY_LOCKOUT_UNTIL - now)
        hours = remaining / 3600.0
        print(f"\n[{time.strftime('%X')}] ⚠️ OpenSky rate-limit lockout active ({hours:.1f}h remaining). Waiting for quota reset...")
        write_state("NONE", "IDLE", "", "NONE", "NONE")
        return

    print(f"\n[{time.strftime('%X')}] Fetching MSP traffic from OpenSky...")
    url = 'https://opensky-network.org/api/states/all'
    headers = opensky_auth.get_headers()
    auth = opensky_auth.get_basic_auth()
    
    try:
        response = requests.get(url, params=MSP_BBOX, headers=headers, auth=auth, timeout=10, verify=False)
        if response.status_code == 429:
            retry_sec = 300
            try:
                retry_sec = int(response.headers.get("X-Rate-Limit-Retry-After-Seconds") or response.headers.get("Retry-After") or "300")
            except Exception:
                pass
            OPENSKY_LOCKOUT_UNTIL = time.time() + retry_sec
            hours = retry_sec / 3600.0
            print(f"  -> ⚠️ OpenSky API rate limit reached (HTTP 429). Retry in {hours:.1f}h ({retry_sec}s).")
            if not opensky_auth.is_authenticated():
                print("  -> 💡 TIP: Add OpenSky credentials in .env or credentials.json to increase daily quota from 400 to 4,000 requests!")
            write_state("NONE", "IDLE", "", "NONE", "NONE")
            return

        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as e:
        if hasattr(e, "response") and e.response is not None and e.response.status_code == 429:
            retry_sec = 300
            try:
                retry_sec = int(e.response.headers.get("X-Rate-Limit-Retry-After-Seconds") or e.response.headers.get("Retry-After") or "300")
            except Exception:
                pass
            OPENSKY_LOCKOUT_UNTIL = time.time() + retry_sec
            hours = retry_sec / 3600.0
            print(f"  -> ⚠️ OpenSky API rate limit reached (HTTP 429). Retry in {hours:.1f}h ({retry_sec}s).")
            if not opensky_auth.is_authenticated():
                print("  -> 💡 TIP: Add OpenSky credentials in .env or credentials.json to increase daily quota from 400 to 4,000 requests!")
            write_state("NONE", "IDLE", "", "NONE", "NONE")
            return
        print(f"  -> OpenSky API request failed: {e}")
        return
    except Exception as e:
        print(f"  -> OpenSky API request failed: {e}")
        return

    states = data.get('states')
    if not states:
        print("  -> No aircraft found.")
        write_state("NONE", "IDLE", "", "NONE", "NONE")
        return

    print(f"  -> Tracking {len(states)} aircraft. Checking runway tubes...")
    active_events = 0

    now_ts = time.time()
    matched_ops = []
    freshly_matched_keys = set()

    # Parse each aircraft state vector:
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
            
        # Spatial Filter: Project onto extended runway corridors (with departure fan support)
        assigned_runway = get_active_runway(lat, lon, heading, vertical_rate)
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
            for sfx in (" Airlines", " Air Lines", " Airways", " Aviation", " Express"):
                if airline_name.endswith(sfx):
                    airline_name = airline_name[:-len(sfx)].strip()
            flight_num = raw_callsign[3:].strip()
            flight_str = f"{airline_name} {flight_num}".strip()
        else:
            flight_str = raw_callsign

        # Resolve Aircraft Type and Translate to Human-Readable Format
        raw_ac_type = get_aircraft_type(icao24, callsign=raw_callsign)
        readable_ac_type = AIRFRAMES.get(raw_ac_type, raw_ac_type)
        
        # Unit Conversions for Console Telemetry Output
        speed_kts = int(velocity * 1.94384)
        alt_ft = int(altitude * 3.28084)
        route_display = f"({flight_route})" if flight_route != "Unknown" else ""
        
        print(f"  ✈  MATCH! [{readable_ac_type}] {flight_str} {route_display} | "
              f"{action} on {assigned_runway} | Spd: {speed_kts}kts, Alt: {alt_ft}ft")
        
        normalized_act = "TAKEOFF" if "TAKING" in action else action
        op_dict = {
            "runway": assigned_runway,
            "action": normalized_act,
            "callsign": raw_callsign,
            "flight_label": flight_str,
            "aircraft_type": readable_ac_type,
            "route": flight_route,
            "speed_kts": speed_kts,
            "altitude_ft": alt_ft
        }
        op_key = f"{assigned_runway}_{raw_callsign}_{normalized_act}"
        freshly_matched_keys.add(op_key)
        HELD_OPERATIONS[op_key] = {
            "op": op_dict,
            "expires_at": now_ts + 25.0
        }
        matched_ops.append(op_dict)

    # Clean up expired held operations, and preserve unexpired active operations
    for op_key, held in list(HELD_OPERATIONS.items()):
        if now_ts >= held["expires_at"]:
            del HELD_OPERATIONS[op_key]
        elif op_key not in freshly_matched_keys:
            matched_ops.append(held["op"])

    # Airport Operational Flow Constraint
    VALID_FLOWS = [
        {"30R", "30L"},  # Parallel NW flow
        {"12L", "12R"},  # Parallel SE flow
        {"4"},           # Crosswind NE
        {"22"},          # Crosswind SW
        {"17"},          # North-South S
        {"35"}           # North-South N
    ]
    if len(matched_ops) > 1:
        best_matches = []
        best_count = 0
        for flow in VALID_FLOWS:
            matches = [op for op in matched_ops if op["runway"] in flow]
            if len(matches) > best_count:
                best_count = len(matches)
                best_matches = matches
        matched_ops = best_matches if best_matches else [matched_ops[0]]

        # Purge held operations outside winning flow
        winning_runways = {op["runway"] for op in matched_ops}
        for op_key in list(HELD_OPERATIONS.keys()):
            rw = op_key.split("_")[0]
            if rw not in winning_runways:
                del HELD_OPERATIONS[op_key]

    if matched_ops:
        write_state("NONE", "ACTIVE", "", "NONE", "NONE", tracked_count=len(states), active_operations=matched_ops)
    else:
        print("  -> No active operations detected right now.")
        write_state("NONE", "IDLE", "", "NONE", "NONE", tracked_count=len(states))

if __name__ == "__main__":
    poll_sec = float(os.environ.get("MSP_POLL_INTERVAL", "30.0"))
    auth_desc = opensky_auth.get_auth_status_str()
    print("==================================================")
    print("    KMSP RUNWAY TRAFFIC MONITOR & TELEMETRY       ")
    print("==================================================")
    print(f"Auth Status   : {auth_desc}")
    print(f"Poll Interval : {poll_sec}s")
    if not opensky_auth.is_authenticated():
        print("Notice        : Anonymous mode has a 400 req/day limit.")
        print("                To enable 4,000 req/day, add OpenSky credentials in .env")
    print("--------------------------------------------------")
    print("Starting tracker loop. Press Ctrl+C to stop.")
    while True:
        fetch_msp_traffic()
        time.sleep(poll_sec)