"""State-machine tests for the assembler's per-aircraft track logic -
coverage-gap handling and ground/air transitions are called out explicitly
in the Stage 3 Definition of Done.
"""

from __future__ import annotations

import datetime as dt

from services.assembler.track_state import (
    COVERAGE_GAP_SECONDS,
    Phase,
    advance,
    should_reset_for_gap,
    with_reset,
)
from services.common.schemas.aircraft import StateVectorIn

BASE_TS = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)


def _sv(**overrides) -> StateVectorIn:
    base = dict(
        icao24="abc123",
        ts=BASE_TS,
        lat=40.0,
        lon=-74.0,
        baro_alt_ft=10000.0,
        velocity_kt=300.0,
        heading_deg=90.0,
        vert_rate_fpm=0.0,
        on_ground=False,
        source="test",
    )
    base.update(overrides)
    return StateVectorIn(**base)


def test_first_observation_with_no_previous_state():
    state = advance(_sv(), previous=None)
    assert state.icao24 == "abc123"
    assert state.just_took_off is False
    assert state.just_landed is False


def test_takeoff_transition_detected():
    ground_state = advance(_sv(on_ground=True, baro_alt_ft=None), previous=None)
    airborne = advance(_sv(on_ground=False, vert_rate_fpm=1500, baro_alt_ft=500), ground_state)
    assert airborne.just_took_off is True
    assert airborne.just_landed is False


def test_landing_transition_detected():
    airborne = advance(_sv(on_ground=False), previous=None)
    landed = advance(_sv(on_ground=True, baro_alt_ft=None), airborne)
    assert landed.just_landed is True
    assert landed.just_took_off is False


def test_climb_phase_classification():
    state = advance(_sv(vert_rate_fpm=1200, baro_alt_ft=3000), previous=None)
    assert state.phase == Phase.CLIMB


def test_descent_phase_classification():
    state = advance(_sv(vert_rate_fpm=-1500, baro_alt_ft=15000), previous=None)
    assert state.phase == Phase.DESCENT


def test_cruise_phase_classification():
    state = advance(_sv(vert_rate_fpm=0, baro_alt_ft=35000), previous=None)
    assert state.phase == Phase.CRUISE


def test_ground_phase_overrides_everything():
    state = advance(_sv(on_ground=True, baro_alt_ft=None, vert_rate_fpm=None), previous=None)
    assert state.phase == Phase.GROUND


def test_approach_phase_continues_from_descent():
    descending = advance(_sv(vert_rate_fpm=-1000, baro_alt_ft=5000), previous=None)
    lower = advance(_sv(vert_rate_fpm=-200, baro_alt_ft=2000), descending)
    assert lower.phase == Phase.APPROACH


def test_unknown_phase_when_altitude_missing():
    state = advance(_sv(baro_alt_ft=None, on_ground=False), previous=None)
    assert state.phase == Phase.UNKNOWN


def test_should_reset_for_gap_detects_long_dropout():
    later = BASE_TS + dt.timedelta(seconds=COVERAGE_GAP_SECONDS + 1)
    assert should_reset_for_gap(BASE_TS, later) is True


def test_should_reset_for_gap_ignores_short_gap():
    later = BASE_TS + dt.timedelta(seconds=5)
    assert should_reset_for_gap(BASE_TS, later) is False


def test_with_reset_clears_phase_but_keeps_identity():
    state = advance(_sv(vert_rate_fpm=1200, baro_alt_ft=3000), previous=None)
    reset_state = with_reset(state)
    assert reset_state.icao24 == state.icao24
    assert reset_state.phase == Phase.UNKNOWN
    assert reset_state.just_took_off is False


def test_oceanic_gap_does_not_falsely_report_instant_climb():
    """The exact scenario named in the master spec's interview questions:
    an aircraft disappears from coverage for 8 minutes and reappears at a
    very different altitude - this must NOT be read as an instantaneous
    phase transition once the caller has applied with_reset() for the gap.
    """
    before_gap = advance(_sv(baro_alt_ft=5000, vert_rate_fpm=-500), previous=None)
    assert should_reset_for_gap(before_gap.last_ts, BASE_TS + dt.timedelta(minutes=8))
    reset = with_reset(before_gap)

    after_gap = advance(
        _sv(ts=BASE_TS + dt.timedelta(minutes=8), baro_alt_ft=35000, vert_rate_fpm=0),
        reset,
    )
    # Correctly classified as cruise from the data itself, not inferred as a
    # continuation of the pre-gap descent.
    assert after_gap.phase == Phase.CRUISE
