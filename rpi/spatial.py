"""
Spatial and geometric calculations for KMSP runway centerline projection,
corridor gating, and intersecting runway disambiguation.
"""

import math
from .config import (
    MSP_LAT,
    MSP_LON,
    LAT_TO_M,
    LON_TO_M,
    RUNWAYS,
    RUNWAY_MAX_CROSS_TRACK_M,
    DEFAULT_HEADING_TOLERANCE_DEG,
    CRAB_HEADING_TOLERANCE_DEG,
    DEPARTURE_FAN_MAX_CROSS_TRACK_M,
    DEPARTURE_FAN_HEADING_TOLERANCE_DEG,
    TAKEOFF_MIN_VERT_RATE,
    CORRIDOR_T_MIN,
    CORRIDOR_T_MAX,
)

def to_meters(lat: float, lon: float) -> tuple[float, float]:
    """
    Projects WGS84 spherical coordinates (latitude, longitude) into a local
    2D Cartesian coordinate system in meters relative to MSP reference center.
    """
    return (lon - MSP_LON) * LON_TO_M, (lat - MSP_LAT) * LAT_TO_M

def get_runway_match(lat: float, lon: float, heading: float, vertical_rate: float = None) -> dict | None:
    """
    Projects an aircraft's position and heading onto KMSP runways to determine
    the active runway assignment.

    Handles:
    - Extended arrival and departure corridors (-2.0 to 3.0 t-projection).
    - Cross-track lateral offset filtering (<= 150m for arrivals, up to 900m for climbing departure fans).
    - Heading alignment filtering with crab-angle tolerance near touchdown and SID turn fan (up to 85°).
    - Intersecting runway disambiguation (prioritizes minimal orthogonal offset).

    Args:
        lat: Aircraft latitude in degrees.
        lon: Aircraft longitude in degrees.
        heading: Aircraft true track / heading in degrees (0-360).
        vertical_rate: Aircraft climb/descent rate in m/s (optional, activates departure fan).

    Returns:
        dict with match details or None if no match:
        {
            "runway": "30R",
            "zone": "12L/30R",
            "cross_track_m": 18.2,
            "t_progress": 0.42,
            "heading_delta": 3.5
        }
    """
    if lat is None or lon is None or heading is None:
        return None

    p_x, p_y = to_meters(lat, lon)
    candidates = []
    is_climbing = (vertical_rate is not None and vertical_rate >= TAKEOFF_MIN_VERT_RATE)

    for rw_zone, data in RUNWAYS.items():
        a_x, a_y = to_meters(data["start"][0], data["start"][1])
        b_x, b_y = to_meters(data["end"][0], data["end"][1])

        ab_x, ab_y = b_x - a_x, b_y - a_y
        ap_x, ap_y = p_x - a_x, p_y - a_y

        dot_ab_ab = ab_x**2 + ab_y**2
        if dot_ab_ab == 0:
            continue

        rw_len = math.sqrt(dot_ab_ab)

        # Scalar projection t:
        # t=0.0 at threshold A, t=1.0 at threshold B
        t = (ap_x * ab_x + ap_y * ab_y) / dot_ab_ab
        t_clamped = max(CORRIDOR_T_MIN, min(CORRIDOR_T_MAX, t))

        closest_x = a_x + t_clamped * ab_x
        closest_y = a_y + t_clamped * ab_y

        cross_track_dist = math.hypot(p_x - closest_x, p_y - closest_y)

        for rw_name, target_heading in data["dirs"].items():
            is_reverse = (target_heading > 180)
            in_dep_zone = (t < 0.2) if is_reverse else (t > 0.8)
            dep_dist_m = max(0.0, -t * rw_len) if is_reverse else max(0.0, (t - 1.0) * rw_len)

            # Dynamic spatial gating:
            if is_climbing and in_dep_zone:
                # Expanding departure fan corridor to capture immediate SID turns (e.g. DAL2089)
                allowed_cross_track = min(DEPARTURE_FAN_MAX_CROSS_TRACK_M, RUNWAY_MAX_CROSS_TRACK_M + dep_dist_m * 0.35)
                allowed_heading_diff = DEPARTURE_FAN_HEADING_TOLERANCE_DEG
            elif cross_track_dist < 60.0 and -0.2 <= t <= 0.8:
                # Approach crab angle tolerance near touchdown
                allowed_cross_track = RUNWAY_MAX_CROSS_TRACK_M
                allowed_heading_diff = CRAB_HEADING_TOLERANCE_DEG
            else:
                # Standard straight-in / rollout tolerance
                allowed_cross_track = RUNWAY_MAX_CROSS_TRACK_M
                allowed_heading_diff = DEFAULT_HEADING_TOLERANCE_DEG

            if cross_track_dist > allowed_cross_track:
                continue

            # Angular difference wrapped between -180 and +180
            diff = abs((heading - target_heading + 180) % 360 - 180)
            if diff <= allowed_heading_diff:
                # Calculate normalized progress along direction of travel:
                # If aircraft is moving towards end threshold B (e.g. 12L or 12R heading ~121 deg),
                # progress is t. If moving from B to A (e.g. 30R or 30L heading ~301 deg), progress is 1 - t.
                progress = max(0.0, min(1.0, (1.0 - t) if is_reverse else t))

                candidates.append({
                    "runway": rw_name,
                    "zone": rw_zone,
                    "cross_track_m": round(cross_track_dist, 1),
                    "t_progress": round(progress, 3),
                    "heading_delta": round(diff, 1)
                })

    if not candidates:
        return None

    # Disambiguation for Intersecting Runways (e.g., 4/22 crossing 12L/30R):
    # Sort candidates by smallest cross-track offset first, then by smallest heading delta.
    candidates.sort(key=lambda c: (c["cross_track_m"], c["heading_delta"]))
    return candidates[0]
