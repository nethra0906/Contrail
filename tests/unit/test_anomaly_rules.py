from __future__ import annotations

import datetime as dt

from services.assembler.track_state import Phase, TrackState
from services.common.schemas.aircraft import StateVectorIn
from services.inference.anomaly_rules import (
    AnomalyKind,
    check_all_rules,
    check_emergency_squawk,
    check_go_around,
    check_rapid_descent,
)

BASE_TS = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _sv(**overrides) -> StateVectorIn:
    base = dict(
        icao24="abc123",
        ts=BASE_TS,
        lat=40.0,
        lon=-74.0,
        baro_alt_ft=10000.0,
        velocity_kt=300.0,
        vert_rate_fpm=0.0,
        on_ground=False,
        squawk="1200",
        source="test",
    )
    base.update(overrides)
    return StateVectorIn(**base)


def _state(phase: Phase) -> TrackState:
    return TrackState(icao24="abc123", phase=phase, last_ts=BASE_TS, last_on_ground=False)


def test_squawk_7700_triggers_emergency():
    event = check_emergency_squawk(_sv(squawk="7700"))
    assert event is not None
    assert event.kind == AnomalyKind.SQUAWK_EMERGENCY


def test_squawk_1200_vfr_is_not_an_emergency():
    assert check_emergency_squawk(_sv(squawk="1200")) is None


def test_squawk_7500_hijack_triggers():
    event = check_emergency_squawk(_sv(squawk="7500"))
    assert event is not None


def test_rapid_descent_below_threshold_triggers():
    event = check_rapid_descent(_sv(vert_rate_fpm=-5000, baro_alt_ft=5000))
    assert event is not None
    assert event.kind == AnomalyKind.RAPID_DESCENT


def test_rapid_descent_at_cruise_altitude_does_not_trigger():
    """A -5000 fpm descent starting from FL350 is a normal initial descent,
    not an emergency - the altitude ceiling on this rule is deliberate.
    """
    event = check_rapid_descent(_sv(vert_rate_fpm=-5000, baro_alt_ft=35000))
    assert event is None


def test_rapid_descent_on_ground_does_not_trigger():
    event = check_rapid_descent(_sv(vert_rate_fpm=-5000, baro_alt_ft=None, on_ground=True))
    assert event is None


def test_moderate_descent_does_not_trigger():
    event = check_rapid_descent(_sv(vert_rate_fpm=-1500, baro_alt_ft=5000))
    assert event is None


def test_go_around_detected_on_approach_to_climb_transition():
    previous = _state(Phase.APPROACH)
    current = _state(Phase.CLIMB)
    sv = _sv(baro_alt_ft=1500, vert_rate_fpm=800)
    event = check_go_around(previous, current, sv)
    assert event is not None
    assert event.kind == AnomalyKind.GO_AROUND


def test_go_around_not_triggered_without_prior_approach():
    current = _state(Phase.CLIMB)
    sv = _sv(baro_alt_ft=1500, vert_rate_fpm=800)
    assert check_go_around(None, current, sv) is None


def test_go_around_not_triggered_at_high_altitude():
    previous = _state(Phase.APPROACH)
    current = _state(Phase.CLIMB)
    sv = _sv(baro_alt_ft=8000, vert_rate_fpm=800)  # too high to be a go-around
    assert check_go_around(previous, current, sv) is None


def test_check_all_rules_can_return_multiple_events():
    """An emergency squawk during a rapid descent should surface both, not
    collapse into a single ambiguous event.
    """
    previous = _state(Phase.DESCENT)
    current = _state(Phase.DESCENT)
    sv = _sv(squawk="7700", vert_rate_fpm=-5000, baro_alt_ft=4000)
    events = check_all_rules(sv, previous, current)
    kinds = {e.kind for e in events}
    assert AnomalyKind.SQUAWK_EMERGENCY in kinds
    assert AnomalyKind.RAPID_DESCENT in kinds


def test_check_all_rules_returns_empty_for_normal_flight():
    previous = _state(Phase.CRUISE)
    current = _state(Phase.CRUISE)
    sv = _sv(vert_rate_fpm=0, baro_alt_ft=35000, squawk="1200")
    assert check_all_rules(sv, previous, current) == []
