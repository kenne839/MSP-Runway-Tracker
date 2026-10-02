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
    CORRIDOR_T_MIN,
    CORRIDOR_T_MAX,
)

def to_meters(lat: float, lon: float) -> tuple[float, float]:
    """
    Projects WGS84 spherical coordinates (latitude, longitude) into a local
    2D Cartesian coordinate system in meters relative to MSP reference center.
    """
    return (lon - MSP_LON) * LON_TO_M, (lat - MSP_LAT) * LAT_TO_M

def get_runway_match(lat: float, lon: float, heading: float) -> dict | None:
    """
    Projects an aircraft's position and heading onto KMSP runways to determine
    the active runway assignment.

    Handles:
    - Extended arrival and departure corridors (-1.5 to 2.5 t-projection).
    - Cross-track lateral offset filtering (<= 150m).
    - Heading alignment filtering with crab-angle tolerance near touchdown.
    - Intersecting runway disambiguation (prioritizes minimal orthogonal offset).

    Args:
        lat: Aircraft latitude in degrees.
        lon: Aircraft longitude in degrees.
        heading: Aircraft true track / heading in degrees (0-360).

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

    for rw_zone, data in RUNWAYS.items():
        a_x, a_y = to_meters(data["start"][0], data["start"][1])
        b_x, b_y = to_meters(data["end"][0], data["end"][1])

        ab_x, ab_y = b_x - a_x, b_y - a_y
        ap_x, ap_y = p_x - a_x, p_y - a_y

        dot_ab_ab = ab_x**2 + ab_y**2
        if dot_ab_ab == 0:
            continue

        # Scalar projection t:
        # t=0.0 at threshold A, t=1.0 at threshold B
        t = (ap_x * ab_x + ap_y * ab_y) / dot_ab_ab
        t_clamped = max(CORRIDOR_T_MIN, min(CORRIDOR_T_MAX, t))

        closest_x = a_x + t_clamped * ab_x
        closest_y = a_y + t_clamped * ab_y

        cross_track_dist = math.hypot(p_x - closest_x, p_y - closest_y)

        if cross_track_dist > RUNWAY_MAX_CROSS_TRACK_M:
            continue

        # Dynamic heading tolerance:
        # If very close to centerline (< 60m) and in the touchdown / rollout area (t between -0.2 and 0.8),
        # allow broader crab angle tolerance to accommodate severe crosswinds.
        if cross_track_dist < 60.0 and -0.2 <= t <= 0.8:
            allowed_heading_diff = CRAB_HEADING_TOLERANCE_DEG
        else:
            allowed_heading_diff = DEFAULT_HEADING_TOLERANCE_DEG

        for rw_name, target_heading in data["dirs"].items():
            # Angular difference wrapped between -180 and +180
            diff = abs((heading - target_heading + 180) % 360 - 180)
            if diff <= allowed_heading_diff:
                # Calculate normalized progress along direction of travel:
                # If aircraft is moving towards end threshold B (e.g. 12L or 12R heading ~121 deg),
                # progress is t. If moving from B to A (e.g. 30R or 30L heading ~301 deg), progress is 1 - t.
                is_reverse = (diff < 90 and target_heading > 180)
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
