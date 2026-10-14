"""
Canonical domain data models and schema definitions for the MSP Runway Tracker system.
Uses Python dataclasses with slots for optimal memory efficiency and fast attribute access
on resource-constrained embedded nodes (Raspberry Pi 3A+).
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True)
class FlightOperation:
    """Represents an active landing or takeoff operation in a KMSP runway corridor."""
    runway: str
    zone: str
    action: str  # 'LANDING' or 'TAKEOFF'
    callsign: str
    airline: str
    flight_number: str
    flight_label: str
    aircraft_type: str
    type_code: str
    route: str
    altitude_ft: int
    speed_kts: int
    vertical_rate_fpm: int
    progress: float
    cross_track_m: float
    heading_deg: int
    lat: float
    lon: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlightOperation:
        return cls(
            runway=str(data.get("runway", "")),
            zone=str(data.get("zone", "")),
            action=str(data.get("action", "")),
            callsign=str(data.get("callsign", "")),
            airline=str(data.get("airline", "")),
            flight_number=str(data.get("flight_number", "")),
            flight_label=str(data.get("flight_label", "")),
            aircraft_type=str(data.get("aircraft_type", "")),
            type_code=str(data.get("type_code", "")),
            route=str(data.get("route", "")),
            altitude_ft=int(data.get("altitude_ft", 0)),
            speed_kts=int(data.get("speed_kts", 0)),
            vertical_rate_fpm=int(data.get("vertical_rate_fpm", 0)),
            progress=float(data.get("progress", 0.0)),
            cross_track_m=float(data.get("cross_track_m", 0.0)),
            heading_deg=int(data.get("heading_deg", 0)),
            lat=float(data.get("lat", 0.0)),
            lon=float(data.get("lon", 0.0)),
        )


@dataclass(slots=True)
class RunwayStatus:
    """Summary state of an individual physical runway."""
    status: str = "IDLE"  # 'IDLE', 'LANDING', 'TAKEOFF', 'CLOSED'
    callsign: str | None = None
    aircraft_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WeatherData:
    """Current decoded KMSP surface weather observation (METAR)."""
    flight_category: str = "VFR"
    temp_f: int = 59
    temp_c: float = 15.0
    wind: str = "270@11kt"
    pressure: str = "30.06 inHg"
    condition: str = "Clear"
    raw: str = "METAR KMSP (Pending initialization)"
    updated_at: int = 0
    is_stale: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunwayRoles:
    """Airport operational configuration (active arrival and departure runways)."""
    landing: str | None = None
    departure: str | None = None
    summary: str = "RW: Standby"
    full_summary: str = "Standby / Waiting for Traffic"
    source: str = "STANDBY"
    closed_runways: list[str] = field(default_factory=list)
    atis_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ESP32Node:
    """Hardware heartbeat and diagnostics reported by the ESP32 display client."""
    ip: str = ""
    rssi: int | None = None
    free_heap_kb: float | None = None
    uptime_sec: int | None = None
    fps: int | None = None
    last_seen_ts: int = 0
    online: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NetworkStatus:
    """OpenSky Network rate-limit quota and internet connectivity telemetry."""
    online: bool = True
    rate_limited: bool = False
    requests_today: int = 0
    quota_limit: int = 4000
    quota_percent: float = 0.0
    last_successful_poll: int = 0
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ThemeConfig:
    """LED color palette and brightness parameters synchronized with ESP32."""
    name: str = "Classic Emerald & Amber"
    version: int = 1
    brightness: int = 24
    landing_head: str = "0xFFFFFF"
    landing_tail: str = "0x00FF88"
    takeoff_head: str = "0xFFE0A0"
    takeoff_tail: str = "0xFF4500"
    idle_threshold: str = "0x002200"
    conflict_warn: str = "0xFF0000"
    solar_sync: bool = False
    night_floor: int = 8
    day_ceiling: int = 40
    solar_elevation: float = 0.0
    solar_phase: str = "Night"
    effective_brightness: int = 24

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
