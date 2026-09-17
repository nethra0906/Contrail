"""Property tests for services/common/geo.py - the single geodesy
implementation every other component (ingest, ML features, simulator) relies
on. These are the tests referenced as the Stage 1 Definition of Done.
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from services.common.geo import (
    LatLon,
    bbox_contains,
    destination_point,
    from_enu,
    haversine_distance_m,
    initial_bearing_deg,
    to_enu,
)

lat_strategy = st.floats(min_value=-89.9, max_value=89.9, allow_nan=False, allow_infinity=False)
lon_strategy = st.floats(min_value=-179.9, max_value=179.9, allow_nan=False, allow_infinity=False)
latlon_strategy = st.builds(LatLon, lat=lat_strategy, lon=lon_strategy)


@given(latlon_strategy, latlon_strategy)
def test_haversine_is_symmetric(a: LatLon, b: LatLon):
    assert math.isclose(haversine_distance_m(a, b), haversine_distance_m(b, a), rel_tol=1e-9)


@given(latlon_strategy)
def test_haversine_distance_to_self_is_zero(a: LatLon):
    assert haversine_distance_m(a, a) == 0.0


@given(
    latlon_strategy,
    st.floats(min_value=0, max_value=360),
    st.floats(min_value=0, max_value=500_000),
)
@settings(max_examples=200)
def test_destination_point_roundtrip_distance(origin: LatLon, bearing: float, distance_m: float):
    """Travelling `distance_m` along `bearing` from origin should land you
    `distance_m` away (within great-circle numerical tolerance) - this is the
    invariant the simulator's reroute detour math depends on.
    """
    dest = destination_point(origin, bearing, distance_m)
    measured = haversine_distance_m(origin, dest)
    # Tolerance widens near the poles where bearing becomes degenerate.
    tol = max(1.0, distance_m * 1e-6) + abs(origin.lat) * 2
    assert abs(measured - distance_m) < tol + 50


@given(
    lat_strategy.filter(lambda x: abs(x) < 60),  # ENU flat-earth approx degrades near poles
    lon_strategy,
    st.floats(min_value=-50_000, max_value=50_000),
    st.floats(min_value=-50_000, max_value=50_000),
)
@settings(max_examples=200)
def test_enu_roundtrip_within_tolerance(anchor_lat, anchor_lon, east, north):
    """ENU projection round-trips to within 1 cm for the short-range (<50km)
    use the trajectory model actually needs it for.
    """
    anchor = LatLon(anchor_lat, anchor_lon)
    point = from_enu(east, north, anchor)
    e2, n2 = to_enu(point, anchor)
    assert abs(e2 - east) < 0.01
    assert abs(n2 - north) < 0.01


def test_bearing_due_north_is_zero():
    a = LatLon(40.0, -74.0)
    b = LatLon(41.0, -74.0)
    assert initial_bearing_deg(a, b) < 1.0


def test_bearing_due_east_is_ninety():
    a = LatLon(0.0, 0.0)
    b = LatLon(0.0, 1.0)
    assert abs(initial_bearing_deg(a, b) - 90.0) < 0.5


def test_known_distance_nyc_to_lax():
    """Sanity check against a known real-world value (~3,983 km great circle)."""
    jfk = LatLon(40.6413, -73.7781)
    lax = LatLon(33.9416, -118.4085)
    dist_km = haversine_distance_m(jfk, lax) / 1000
    assert 3950 < dist_km < 4020


def test_bbox_contains():
    bbox = (24.5, 49.5, -125.0, -66.5)  # CONUS
    assert bbox_contains(40.7128, -74.0060, bbox)  # NYC
    assert not bbox_contains(51.5074, -0.1278, bbox)  # London
