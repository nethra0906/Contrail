"""Unit tests for the assembler's pure per-message decision logic
(services/assembler/pipeline.py) - no Kafka/Redis/Postgres involved, plain
StateVectorIn objects fed through in-memory state.
"""

from __future__ import annotations

import datetime as dt

from services.assembler.pipeline import (
    AssemblerState,
    advance_track,
    close_leg_if_landing,
    open_leg_if_takeoff,
)
from services.common.schemas.aircraft import StateVectorIn


def _sv(
    ts: dt.datetime, on_ground: bool, alt: float | None = None, vrate: float | None = None
) -> StateVectorIn:
    return StateVectorIn(
        icao24="abc123",
        ts=ts,
        lat=40.0,
        lon=-74.0,
        baro_alt_ft=alt,
        vert_rate_fpm=vrate,
        velocity_kt=200.0,
        on_ground=on_ground,
        source="test",
    )


def test_takeoff_opens_a_leg_and_landing_closes_it():
    state = AssemblerState()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    on_ground = advance_track(_sv(t0, on_ground=True), state)
    assert open_leg_if_takeoff(on_ground, state, "KJFK") is None

    airborne = advance_track(
        _sv(t0 + dt.timedelta(seconds=30), on_ground=False, alt=500, vrate=1000), state
    )
    opened = open_leg_if_takeoff(airborne, state, "KJFK")
    assert opened is not None
    assert opened.icao24 == "abc123"
    assert opened.origin_icao == "KJFK"
    assert "abc123" in state.open_legs

    landed = advance_track(_sv(t0 + dt.timedelta(hours=2), on_ground=True), state)
    closed, rejected = close_leg_if_landing(landed, state, "KBOS")
    assert rejected is False
    assert closed is not None
    assert closed.flight_id == opened.flight_id
    assert closed.dest_icao == "KBOS"
    assert "abc123" not in state.open_legs


def test_landing_with_no_open_leg_is_rejected_not_fabricated():
    state = AssemblerState()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    advance_track(_sv(t0, on_ground=False, alt=5000, vrate=0), state)
    landed = advance_track(_sv(t0 + dt.timedelta(minutes=5), on_ground=True), state)

    closed, rejected = close_leg_if_landing(landed, state, "KBOS")
    assert closed is None
    assert rejected is True


def test_implausibly_short_leg_is_rejected():
    state = AssemblerState()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    advance_track(_sv(t0, on_ground=True), state)
    airborne = advance_track(
        _sv(t0 + dt.timedelta(seconds=5), on_ground=False, alt=500, vrate=1000), state
    )
    open_leg_if_takeoff(airborne, state, "KJFK")

    # "landed" 10 seconds later - clearly noise, not a real flight.
    landed = advance_track(_sv(t0 + dt.timedelta(seconds=15), on_ground=True), state)
    closed, rejected = close_leg_if_landing(landed, state, "KJFK")

    assert closed is None
    assert rejected is True
    assert "abc123" not in state.open_legs


def test_coverage_gap_resets_phase_but_keeps_open_leg_state_separate():
    state = AssemblerState()
    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    advance_track(_sv(t0, on_ground=False, alt=10000, vrate=1000), state)
    # A 5-minute coverage gap exceeds COVERAGE_GAP_SECONDS (120s).
    after_gap = advance_track(
        _sv(t0 + dt.timedelta(minutes=5), on_ground=False, alt=10000, vrate=0), state
    )

    assert after_gap.just_took_off is False
    assert after_gap.just_landed is False
