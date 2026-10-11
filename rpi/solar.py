"""
KMSP Solar Elevation and Circadian Brightness Calculation Module.
Uses NOAA / Jean Meeus astronomical algorithms to compute the exact solar
elevation angle at Minneapolis-St. Paul International Airport (KMSP: 44.8819° N, -93.2217° W)
and maps it smoothly to human-perceptual LED luminance.
"""

import math
import datetime

KMSP_LAT = 44.8819
KMSP_LON = -93.2217

CIVIL_TWILIGHT_ELEVATION_DEG = -6.0   # Horizon threshold for civil dusk/dawn
FULL_DAYLIGHT_ELEVATION_DEG = 25.0    # Elevation where daylight brightness saturates to ceiling


def get_solar_elevation(dt_utc=None, lat=KMSP_LAT, lon=KMSP_LON) -> float:
    """
    Computes true solar elevation angle (degrees above/below horizon)
    for KMSP coordinates using NOAA astronomical ephemeris equations.
    """
    if dt_utc is None:
        dt_utc = datetime.datetime.now(datetime.timezone.utc)
    elif dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)

    Y = dt_utc.year
    M = dt_utc.month
    D = dt_utc.day
    hr = dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0

    if M <= 2:
        Y -= 1
        M += 12

    A = math.floor(Y / 100)
    B = 2 - A + math.floor(A / 4)
    JD = math.floor(365.25 * (Y + 4716)) + math.floor(30.6001 * (M + 1)) + D + B - 1524.5 + hr / 24.0
    T = (JD - 2451545.0) / 36525.0

    # Geometric Mean Longitude of Sun (degrees)
    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360.0

    # Geometric Mean Anomaly of Sun (degrees)
    M_anom = (357.52911 + T * (35999.05029 - 0.0001537 * T)) % 360.0
    M_rad = math.radians(M_anom)

    # Eccentricity of Earth orbit
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)

    # Sun Equation of Center
    C = (math.sin(M_rad) * (1.914602 - T * (0.004817 + 0.000014 * T)) +
         math.sin(2 * M_rad) * (0.019993 - 0.000101 * T) +
         math.sin(3 * M_rad) * 0.000289)

    sun_true_lon = (L0 + C) % 360.0
    omega = math.radians(125.04 - 1934.136 * T)
    lambda_app = math.radians(sun_true_lon - 0.00569 - 0.00478 * math.sin(omega))

    # Obliquity of ecliptic
    eps0 = 23.439291 - 0.013004167 * T
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))

    # Solar Declination
    sin_dec = math.sin(eps) * math.sin(lambda_app)
    dec = math.asin(sin_dec)

    # Equation of Time (minutes)
    y = math.tan(eps / 2.0) ** 2
    L0_rad = math.radians(L0)
    E_time = 4.0 * math.degrees(
        y * math.sin(2 * L0_rad) - 2 * e * math.sin(M_rad) +
        4 * e * y * math.sin(M_rad) * math.cos(2 * L0_rad) -
        0.5 * (y ** 2) * math.sin(4 * L0_rad) - 1.25 * (e ** 2) * math.sin(2 * M_rad)
    )

    # True Solar Time and Hour Angle
    time_offset = E_time + 4.0 * lon
    tst = (hr * 60.0 + time_offset) % 1440.0
    ha = (tst / 4.0) - 180.0 if (tst / 4.0) >= 0 else (tst / 4.0) + 180.0
    ha_rad = math.radians(ha)
    lat_rad = math.radians(lat)

    # Solar Elevation Angle
    sin_elev = math.sin(lat_rad) * math.sin(dec) + math.cos(lat_rad) * math.cos(dec) * math.cos(ha_rad)
    elev = math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))
    return round(elev, 2)


def get_solar_phase(elevation_deg: float) -> str:
    """Classifies the current solar phase for UI status display."""
    if elevation_deg >= FULL_DAYLIGHT_ELEVATION_DEG:
        return "Peak Daylight"
    elif elevation_deg >= 5.0:
        return "Daylight"
    elif elevation_deg >= 0.0:
        return "Golden Hour / Sunset"
    elif elevation_deg >= CIVIL_TWILIGHT_ELEVATION_DEG:
        return "Civil Twilight"
    elif elevation_deg >= -12.0:
        return "Nautical Dusk / Dawn"
    else:
        return "Night"


def compute_circadian_brightness(elevation_deg: float, night_floor: int = 8, day_ceiling: int = 32) -> int:
    """
    Mathematical transfer function:
    Maps solar elevation angle to perceptual LED luminance using a Hermite Smoothstep curve.
    Smoothstep ensures zero abrupt jumps at dawn and dusk, conforming to human eye dark-adaptation.
    """
    night_floor = max(5, min(20, int(night_floor)))
    day_ceiling = max(night_floor, min(48, int(day_ceiling)))

    # Normalized progress t in [0.0, 1.0] from Civil Twilight (-6°) to Bright Morning (25°)
    span = FULL_DAYLIGHT_ELEVATION_DEG - CIVIL_TWILIGHT_ELEVATION_DEG
    t = (elevation_deg - CIVIL_TWILIGHT_ELEVATION_DEG) / span
    t = max(0.0, min(1.0, t))

    # Cubic Hermite Smoothstep S(t) = 3t^2 - 2t^3
    smooth = 3.0 * (t ** 2) - 2.0 * (t ** 3)

    target_brightness = night_floor + (day_ceiling - night_floor) * smooth
    return int(round(target_brightness))

