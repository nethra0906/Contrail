"""Tests for the pure runway queueing model. Includes the exact test named
in the master spec's Stage 7 DoD: "closing a runway must increase delay
monotonically."
"""

from __future__ import annotations

import pytest

from services.simulator.des.runway import (
    Closure,
    RunwayState,
    close_runway,
    is_closed_at,
    request_service,
)


def test_first_request_on_idle_runway_starts_immediately():
    state = RunwayState()
    result = request_service(state, arrival_time=100.0, service_duration=60.0)
    assert result.start_time == 100.0
    assert result.finish_time == 160.0
    assert result.wait_time == 0.0


def test_second_request_queues_behind_the_first():
    state = RunwayState()
    first = request_service(state, arrival_time=0.0, service_duration=60.0)
    second = request_service(first.new_state, arrival_time=10.0, service_duration=60.0)

    assert second.start_time == 60.0  # waits for the first to finish
    assert second.wait_time == 50.0
    assert second.finish_time == 120.0


def test_request_arriving_after_runway_is_free_does_not_wait():
    state = RunwayState()
    first = request_service(state, arrival_time=0.0, service_duration=60.0)
    second = request_service(first.new_state, arrival_time=200.0, service_duration=30.0)

    assert second.start_time == 200.0
    assert second.wait_time == 0.0


def test_zero_or_negative_service_duration_rejected():
    state = RunwayState()
    with pytest.raises(ValueError, match="positive"):
        request_service(state, arrival_time=0.0, service_duration=0.0)


def test_closure_construction_rejects_backwards_interval():
    with pytest.raises(ValueError, match="after start"):
        Closure(start=100.0, end=50.0)


def test_is_closed_at_checks_half_open_interval():
    state = close_runway(RunwayState(), start=100.0, duration=50.0)
    assert is_closed_at(state, 100.0) is True  # inclusive start
    assert is_closed_at(state, 149.9) is True
    assert is_closed_at(state, 150.0) is False  # exclusive end
    assert is_closed_at(state, 99.9) is False


def test_request_arriving_during_closure_waits_until_reopening():
    state = close_runway(RunwayState(), start=0.0, duration=100.0)
    result = request_service(state, arrival_time=50.0, service_duration=20.0)
    assert result.start_time == 100.0
    assert result.finish_time == 120.0
    assert result.wait_time == 50.0


def test_request_that_would_span_into_a_closure_is_pushed_past_it():
    """The service window [90, 130) would overlap a closure starting at 100
    - the request must be pushed to start AFTER the closure ends, not just
    truncated or allowed to run through it.
    """
    state = close_runway(RunwayState(), start=100.0, duration=50.0)  # closed [100, 150)
    result = request_service(state, arrival_time=90.0, service_duration=40.0)
    assert result.start_time == 150.0  # pushed past the closure
    assert result.finish_time == 190.0


def test_request_entirely_before_closure_is_unaffected():
    state = close_runway(RunwayState(), start=100.0, duration=50.0)
    result = request_service(state, arrival_time=0.0, service_duration=30.0)
    assert result.start_time == 0.0
    assert result.finish_time == 30.0


def test_multiple_closures_are_both_respected():
    state = close_runway(RunwayState(), start=100.0, duration=20.0)  # [100,120)
    state = close_runway(state, start=200.0, duration=20.0)  # [200,220)
    # Request that would span across both if pushed only once.
    result = request_service(state, arrival_time=115.0, service_duration=90.0)
    # Pushed past first closure to 120, service [120,210) overlaps second
    # closure at 200, so pushed again to 220.
    assert result.start_time == 220.0
    assert result.finish_time == 310.0


def test_closing_a_runway_never_decreases_total_delay_for_a_fixed_demand_sequence():
    """The exact property named in the master spec's Stage 7 DoD: given the
    same sequence of arrivals, adding a closure can only ever increase (or
    leave unchanged) total wait time versus no closure - never decrease it.
    """
    arrivals = [(i * 30.0, 45.0) for i in range(10)]  # 10 aircraft, 30s apart, 45s service

    def total_wait(state: RunwayState) -> float:
        total = 0.0
        for arrival_time, service_duration in arrivals:
            result = request_service(state, arrival_time, service_duration)
            total += result.wait_time
            state = result.new_state
        return total

    baseline_wait = total_wait(RunwayState())
    closed_state = close_runway(RunwayState(), start=150.0, duration=120.0)
    closed_wait = total_wait(closed_state)

    assert closed_wait >= baseline_wait


def test_longer_closure_never_decreases_delay_versus_shorter_closure():
    arrivals = [(i * 30.0, 45.0) for i in range(10)]

    def total_wait(state: RunwayState) -> float:
        total = 0.0
        for arrival_time, service_duration in arrivals:
            result = request_service(state, arrival_time, service_duration)
            total += result.wait_time
            state = result.new_state
        return total

    short_closure = close_runway(RunwayState(), start=150.0, duration=30.0)
    long_closure = close_runway(RunwayState(), start=150.0, duration=120.0)

    assert total_wait(long_closure) >= total_wait(short_closure)
