"""Splits CONUS coverage into a set of point+radius queries, because
adsb.lol's endpoint is `/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}` with a
radius cap (250 nm) rather than an arbitrary-bbox query.

Deviation from the original spec (documented in docs/adr/0003-hub-tiling.md):
polling the *entire* CONUS bbox on a fixed grid means 60-90 concurrent
requests every poll interval against a free, community-run, unauthenticated
API with no published rate limit - that's not "responsible use," it's the
kind of load that gets an IP banned and takes the feed down for everyone
else. The default tiling mode instead centers a 200 nm circle on each of the
top-N CONUS hub airports by traffic, which covers the vast majority of
flights (most air traffic is near a hub at any given moment) with an order
of magnitude fewer requests. Full-grid tiling is implemented and available
behind `mode="grid"` for anyone running their own feeder/proxy with capacity
to spare.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.common.geo import EARTH_RADIUS_M

NM_TO_M = 1852.0

# Top CONUS hubs by annual operations (FAA/BTS public rankings) - chosen so a
# 200 nm circle around each captures the overwhelming majority of scheduled
# CONUS air traffic at any moment, since traffic density concentrates near
# hubs during climb/descent and en-route corridors between them.
DEFAULT_HUB_ICAOS: dict[str, tuple[float, float]] = {
    "KATL": (33.6367, -84.4281),
    "KDFW": (32.8998, -97.0403),
    "KDEN": (39.8561, -104.6737),
    "KORD": (41.9742, -87.9073),
    "KJFK": (40.6413, -73.7781),
    "KLAX": (33.9416, -118.4085),
    "KLAS": (36.0840, -115.1537),
    "KMCO": (28.4312, -81.3081),
    "KMIA": (25.7959, -80.2870),
    "KCLT": (35.2144, -80.9473),
    "KSEA": (47.4502, -122.3088),
    "KPHX": (33.4342, -112.0116),
    "KEWR": (40.6895, -74.1745),
    "KSFO": (37.6213, -122.3790),
    "KIAH": (29.9902, -95.3368),
    "KBOS": (42.3656, -71.0096),
    "KFLL": (26.0742, -80.1506),
    "KMSP": (44.8848, -93.2223),
    "KLGA": (40.7769, -73.8740),
    "KDTW": (42.2124, -83.3534),
    "KPHL": (39.8744, -75.2424),
    "KSLC": (40.7884, -111.9778),
    "KDCA": (38.8512, -77.0402),
    "KIAD": (38.9531, -77.4565),
    "KBWI": (39.1774, -76.6684),
    "KSAN": (32.7338, -117.1933),
    "KTPA": (27.9755, -82.5332),
    "KHNL": (21.3245, -157.9251),  # kept for aircraft-fleet realism though outside CONUS bbox
    "KSTL": (38.7487, -90.3700),
    "KMDW": (41.7868, -87.7522),
}


@dataclass(frozen=True)
class Tile:
    lat: float
    lon: float
    radius_nm: float


def hub_tiles(radius_nm: float = 200.0) -> list[Tile]:
    return [Tile(lat, lon, radius_nm) for lat, lon in DEFAULT_HUB_ICAOS.values()]


# A smaller default set. Measured live on 2026-09-17 (see
# docs/adr/0003-hub-tiling.md, "Update - second measurement" and the
# follow-up quota-exhaustion finding): adsb.lol's limiter appears to react
# to the NUMBER OF DISTINCT never-before-seen coordinates queried in a
# session/window, not just request rate - spacing fresh requests 3s apart
# did not prevent 429s past the first one or two. A materially smaller
# default set reduces how much of whatever budget exists gets consumed per
# sweep, and is a safer default while that behavior is only partially
# understood. Pass a larger slice of hub_tiles() explicitly if you have your
# own feeder/proxy with more headroom.
CORE_HUB_ICAOS = ("KATL", "KDFW", "KORD", "KJFK", "KLAX", "KDEN", "KSEA", "KMIA")


def core_hub_tiles(radius_nm: float = 250.0) -> list[Tile]:
    return [
        Tile(*DEFAULT_HUB_ICAOS[icao], radius_nm)
        for icao in CORE_HUB_ICAOS
        if icao in DEFAULT_HUB_ICAOS
    ]


def grid_tiles(
    bbox: tuple[float, float, float, float], radius_nm: float = 200.0, overlap: float = 0.75
) -> list[Tile]:
    """Full-CONUS fallback: a lat/lon grid of circles spaced at
    `overlap * radius` so adjacent circles overlap enough to leave no gaps.
    Longitude spacing widens per-row using cos(lat) to account for meridian
    convergence - otherwise high-latitude rows are needlessly dense.
    """
    min_lat, max_lat, min_lon, max_lon = bbox
    step_m = radius_nm * NM_TO_M * overlap
    lat_step_deg = (step_m / EARTH_RADIUS_M) * (180 / 3.141592653589793)

    tiles: list[Tile] = []
    lat = min_lat
    while lat <= max_lat:
        import math

        lon_step_deg = lat_step_deg / max(math.cos(math.radians(lat)), 0.1)
        lon = min_lon
        while lon <= max_lon:
            tiles.append(Tile(lat, lon, radius_nm))
            lon += lon_step_deg
        lat += lat_step_deg
    return tiles
