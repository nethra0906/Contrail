from __future__ import annotations

import datetime as dt

from services.assembler.leg_detect import is_plausible_leg_duration, on_landing, on_takeoff
from services.assembler.track_state import Phase, TrackState

BASE_TS = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def _state(icao24="abc123") -> TrackState:
    return TrackState(icao24=icao24, phase=Phase.CLIMB, last_ts=BASE_TS, last_on_ground=False)


def test_on_takeoff_creates_open_leg_with_origin():
    leg = on_takeoff(_state(), BASE_TS, "KJFK")
    assert leg.icao24 == "abc123"
    assert leg.origin_icao == "KJFK"
    assert leg.actual_dep == BASE_TS


def test_on_takeoff_allows_unknown_origin():
    leg = on_takeoff(_state(), BASE_TS, None)
    assert leg.origin_icao is None


def test_on_landing_closes_leg_preserving_identity():
    open_leg = on_takeoff(_state(), BASE_TS, "KJFK")
    landing_ts = BASE_TS + dt.timedelta(hours=3)
    closed = on_landing(open_leg, landing_ts, "KLAX")

    assert closed.flight_id == open_leg.flight_id
    assert closed.icao24 == "abc123"
    assert closed.origin_icao == "KJFK"
    assert closed.dest_icao == "KLAX"
    assert closed.actual_dep == BASE_TS
    assert closed.actual_arr == landing_ts


def test_plausible_duration_accepts_typical_flight():
    open_leg = on_takeoff(_state(), BASE_TS, "KJFK")
    landing_ts = BASE_TS + dt.timedelta(hours=5)
    assert is_plausible_leg_duration(open_leg, landing_ts) is True


def test_plausible_duration_rejects_near_instant_bounce():
    """Guards against noisy on_ground flag flicker being read as a real leg."""
    open_leg = on_takeoff(_state(), BASE_TS, "KJFK")
    landing_ts = BASE_TS + dt.timedelta(seconds=20)
    assert is_plausible_leg_duration(open_leg, landing_ts) is False


def test_plausible_duration_rejects_implausibly_long_leg():
    open_leg = on_takeoff(_state(), BASE_TS, "KJFK")
    landing_ts = BASE_TS + dt.timedelta(hours=20)
    assert is_plausible_leg_duration(open_leg, landing_ts) is False


def test_plausible_duration_accepts_boundary_values():
    open_leg = on_takeoff(_state(), BASE_TS, "KJFK")
    assert is_plausible_leg_duration(open_leg, BASE_TS + dt.timedelta(minutes=2)) is True
    assert is_plausible_leg_duration(open_leg, BASE_TS + dt.timedelta(hours=8)) is True
    assert is_plausible_leg_duration(open_leg, BASE_TS + dt.timedelta(hours=8, seconds=1)) is False
