"""The non-negotiable golden test named explicitly in
docs/CONTRAIL_MASTER_SPEC.md §11: "the same (snapshot, spec, seed) produces
identical output," and the DES-specific version of it in §9 Stage 7's DoD -
"run the same triple 3 times, assert byte-identical event streams."

This runs a synthetic but realistic multi-entity scenario (several aircraft
converging on one runway, with randomness driving service-time jitter via a
seeded Rng) three times from scratch and asserts the full event log is
identical every time. It's a stand-in for the eventual full simulator golden
test - that one exercises the real DES/runway/GNN pipeline once Stage 7
builds it - but it already proves the two building blocks that pipeline
depends on (SimClock's ordering, and Rng's seeding) are themselves
deterministic, which is the precondition for the real one meaning anything.
"""

from __future__ import annotations

from services.common.determinism import Rng
from services.simulator.des.clock import SimClock


def run_scenario(seed: int) -> tuple:
    """A toy but nontrivial DES scenario: 5 aircraft each request a runway
    slot at a jittered arrival time (jitter from the seeded Rng, not
    wall-clock or module-level `random`); a single-server runway dispatches
    them in arrival order with a fixed service time, chaining the next
    aircraft's landing off the previous one's completion - the same
    "handler schedules the next event" pattern the real runway model
    (services/simulator/des/runway.py, Stage 7) will use.
    """
    clock = SimClock()
    rng = Rng(seed)
    runway_busy_until = 0.0
    landed_order: list[str] = []

    aircraft_ids = [f"AC{i}" for i in range(5)]
    for i, aid in enumerate(aircraft_ids):
        jitter = rng.uniform(-2.0, 2.0)
        clock.schedule(max(0.0, i * 10.0 + jitter), aid, "request_landing")

    def handler(event):
        nonlocal runway_busy_until
        if event.event_type == "request_landing":
            start = max(event.sim_time, runway_busy_until)
            service_time = 6.0 + rng.uniform(0, 1.0)
            runway_busy_until = start + service_time
            clock.schedule(runway_busy_until - event.sim_time, event.entity_id, "land")
        elif event.event_type == "land":
            landed_order.append(event.entity_id)

    clock.run_until(end_time=1000, handler=handler)
    return tuple(clock.event_log), tuple(landed_order)


def test_scenario_is_byte_identical_across_three_runs():
    seed = 42
    run1 = run_scenario(seed)
    run2 = run_scenario(seed)
    run3 = run_scenario(seed)

    assert run1 == run2 == run3


def test_different_seeds_produce_different_jitter():
    """Sanity check that the Rng is actually doing something - if this test
    ever fails because two different seeds produce the same output, the
    determinism test above would be vacuously true (deterministic because
    nothing varies) rather than meaningfully testing reproducibility under
    real randomness.
    """
    run_a = run_scenario(1)
    run_b = run_scenario(2)
    assert run_a != run_b


def test_landing_order_is_deterministic_and_matches_arrival_order():
    _, landed_order = run_scenario(seed=7)
    assert landed_order == tuple(f"AC{i}" for i in range(5))
