"""Geodesy helpers shared by ingestion, ML feature code, and the simulator.

Every distance/bearing calculation in the system goes through this module so
there is exactly one implementation to get right and test. WGS-84 great-circle
math (haversine) is used throughout; for the short-range local-frame work the
trajectory model needs, `to_enu`/`from_enu` give a flat local approximation
that is accurate to sub-metre error within the ~50 km neighbourhoods it's used
for (verified by the round-trip property test in tests/unit/test_geo.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8  # IUGG mean radius


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float


def haversine_distance_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points, in metres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    h = min(1.0, max(0.0, h))  # guard against float noise pushing asin out of domain
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def initial_bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial great-circle bearing from a to b, degrees, 0=North, clockwise."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def destination_point(origin: LatLon, bearing_deg: float, distance_m: float) -> LatLon:
    """Point reached from origin travelling bearing_deg for distance_m along a great circle."""
    lat1 = math.radians(origin.lat)
    lon1 = math.radians(origin.lon)
    brng = math.radians(bearing_deg)
    ang_dist = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat1) * math.cos(ang_dist) + math.cos(lat1) * math.sin(ang_dist) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(ang_dist) * math.cos(lat1),
        math.cos(ang_dist) - math.sin(lat1) * math.sin(lat2),
    )
    return LatLon(math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180)


def to_enu(point: LatLon, anchor: LatLon) -> tuple[float, float]:
    """Local flat-earth East-North-Up projection of `point` relative to `anchor`, in metres.

    Valid for short ranges (tens of km) - this is what the trajectory model's
    local frame uses, never for long-haul distances.
    """
    lat_rad = math.radians(anchor.lat)
    d_lat = math.radians(point.lat - anchor.lat)
    d_lon = math.radians(point.lon - anchor.lon)
    north = d_lat * EARTH_RADIUS_M
    east = d_lon * EARTH_RADIUS_M * math.cos(lat_rad)
    return east, north


def from_enu(east: float, north: float, anchor: LatLon) -> LatLon:
    """Inverse of to_enu."""
    lat_rad = math.radians(anchor.lat)
    d_lat = north / EARTH_RADIUS_M
    d_lon = east / (EARTH_RADIUS_M * math.cos(lat_rad))
    return LatLon(anchor.lat + math.degrees(d_lat), anchor.lon + math.degrees(d_lon))


def bbox_contains(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    """bbox = (min_lat, max_lat, min_lon, max_lon)"""
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
