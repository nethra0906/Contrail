"""Unit tests for the DES event queue/clock - ordering, tie-breaking, and
cancellation. The full-scenario golden determinism test lives in
tests/golden/test_sim_determinism.py; these are the building-block tests for
the primitive it depends on.
"""

from __future__ import annotations

import pytest

from services.simulator.des.clock import SimClock


def test_events_dispatch_in_time_order():
    clock = SimClock()
    order: list[str] = []
    clock.schedule(5.0, "a", "tick")
    clock.schedule(1.0, "b", "tick")
    clock.schedule(3.0, "c", "tick")

    clock.run_until(end_time=100, handler=lambda e: order.append(e.entity_id))
    assert order == ["b", "c", "a"]


def test_clock_now_advances_to_dispatched_event_time():
    clock = SimClock()
    clock.schedule(10.0, "a", "tick")
    clock.step(handler=lambda e: None)
    assert clock.now == 10.0


def test_ties_at_same_sim_time_break_by_insertion_order():
    """This is the determinism-critical case: two events at the exact same
    sim_time must always dispatch in the order they were scheduled, not in
    whatever order a naive heap comparison (or dict/set iteration) happens
    to produce.
    """
    clock = SimClock()
    order: list[str] = []
    clock.schedule(5.0, "first", "tick")
    clock.schedule(5.0, "second", "tick")
    clock.schedule(5.0, "third", "tick")

    clock.run_until(end_time=100, handler=lambda e: order.append(e.entity_id))
    assert order == ["first", "second", "third"]


def test_run_until_leaves_events_beyond_horizon_unfired():
    clock = SimClock()
    clock.schedule(5.0, "in_horizon", "tick")
    clock.schedule(50.0, "beyond_horizon", "tick")

    dispatched = clock.run_until(end_time=10, handler=lambda e: None)
    assert [e.entity_id for e in dispatched] == ["in_horizon"]
    assert clock.peek_next_time() == 50.0  # still queued, not dropped


def test_negative_delay_rejected():
    clock = SimClock()
    with pytest.raises(ValueError, match="past"):
        clock.schedule(-1.0, "a", "tick")


def test_handler_can_schedule_new_events_during_dispatch():
    """The common DES pattern: handling one event schedules the next one
    (e.g. a runway finishing a landing schedules the next queued aircraft's
    landing). Must not break the queue's ordering invariants.
    """
    clock = SimClock()
    order: list[str] = []

    def handler(event):
        order.append(event.entity_id)
        if event.entity_id == "first":
            clock.schedule(1.0, "chained", "tick")

    clock.schedule(1.0, "first", "tick")
    clock.run_until(end_time=100, handler=handler)
    assert order == ["first", "chained"]


def test_cancel_prevents_dispatch():
    clock = SimClock()
    order: list[str] = []
    event = clock.schedule(5.0, "cancel_me", "tick")
    clock.schedule(10.0, "still_fires", "tick")

    clock.cancel(event)
    clock.run_until(end_time=100, handler=lambda e: order.append(e.entity_id))
    assert order == ["still_fires"]


def test_cancelling_already_dispatched_event_is_a_noop():
    clock = SimClock()
    event = clock.schedule(1.0, "a", "tick")
    clock.step(handler=lambda e: None)
    clock.cancel(event)  # must not raise


def test_empty_queue_step_returns_none():
    clock = SimClock()
    assert clock.step(handler=lambda e: None) is None


def test_event_log_records_full_dispatch_history_in_order():
    clock = SimClock()
    clock.schedule(3.0, "c", "tick")
    clock.schedule(1.0, "a", "tick")
    clock.schedule(2.0, "b", "tick")
    clock.run_until(end_time=100, handler=lambda e: None)

    assert [e.entity_id for e in clock.event_log] == ["a", "b", "c"]
