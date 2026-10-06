"""The Stage 7 DES driver: couples services/simulator/des/clock.py and
des/runway.py - both already built and tested standalone - into the actual
counterfactual simulation the master spec calls the flagship feature.

Scope decision (ADR 0005), stated plainly rather than silently assumed:
this models exactly ONE runway per airport (SIMULATED_RUNWAY_IDENT, see
historical.py), shared by every arrival and departure, with a single fixed
service duration rather than the spec's fitted per-(airport, runway,
config, wtc-pair) distribution. That's a real reduction from the full spec
- a large hub's true multi-runway capacity isn't modeled - but it's enough
to make "close the runway, see delay increase" a real, running, physically
sensible simulation today rather than the full airspace model, which is
genuinely weeks of additional work this project doesn't have time for.

Determinism (master spec rule 7, "never read the wall clock inside the
simulation engine"): every event here is scheduled from a BTS-derived
scheduled time, never dt.datetime.now(); given the same demand sequence and
the same closures, two runs produce byte-identical results - see
tests/unit/test_simulator_engine.py's determinism test.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.common.schemas.scenario import RunwayClose, ScenarioSpec
from services.simulator.demand import DemandEvent
from services.simulator.des.clock import Event, SimClock
from services.simulator.des.runway import RunwayState, close_runway, request_service

# A single fixed runway-occupancy assumption (landing or departure) in
# minutes, not a fitted distribution (see module docstring) - 1.5 minutes
# is a commonly cited rough average runway occupancy time for a single
# operation at a US commercial airport; this is a deliberately simple,
# stated assumption, not a value derived from this project's own data.
SERVICE_DURATION_MIN = 1.5

HORIZON_BUFFER_MIN = 60.0  # let demand scheduled right at the horizon edge still clear the queue


@dataclass(frozen=True)
class FlightResult:
    flight_id: str
    scheduled_minute: float
    baseline_wait_min: float
    scenario_wait_min: float
    added_delay_min: float


@dataclass(frozen=True)
class SimulationResult:
    airport: str
    flights: tuple[FlightResult, ...]
    total_added_delay_min: float
    affected_flight_count: int
    max_added_delay_min: float
    flights_simulated: int
    # Baseline (no-closure) wait stats, reported alongside the diff rather
    # than hidden - a real demand volume higher than this one-runway model
    # can represent (see module docstring) shows up here as a large
    # baseline_mean_wait_min even with zero perturbations, which is the
    # honest signal that this airport's real capacity exceeds what this
    # scoped simulator models, not a bug in the diff.
    baseline_mean_wait_min: float
    baseline_max_wait_min: float


def _run_runway_day(
    demand: list[DemandEvent], closures: list[tuple[float, float]]
) -> dict[str, float]:
    """Drives one pass of the DES loop over `demand`, returns each flight's
    wait_time_min. `closures` is a list of (start_minute, duration_minutes)
    applied to the runway before any demand is served.
    """
    clock = SimClock()
    state = RunwayState()
    for start, duration in closures:
        state = close_runway(state, start, duration)

    waits: dict[str, float] = {}

    def handler(event: Event) -> None:
        nonlocal state
        result = request_service(state, event.sim_time, SERVICE_DURATION_MIN)
        state = result.new_state
        waits[event.entity_id] = result.wait_time

    for d in demand:
        clock.schedule(d.scheduled_minute, d.flight_id, "runway_request")

    horizon = max((d.scheduled_minute for d in demand), default=0.0) + HORIZON_BUFFER_MIN
    clock.run_until(horizon, handler)
    return waits


def _closures_from_spec(spec: ScenarioSpec, airport: str) -> list[tuple[float, float]]:
    """Extracts RunwayClose perturbations for `airport` as (start_minute,
    duration_minutes) pairs relative to midnight on the simulated day -
    the only perturbation type this scoped engine executes (see
    services/api/routers/simulations.py for the explicit rejection of any
    other perturbation type, rather than silently ignoring it).
    """
    closures = []
    for p in spec.perturbations:
        if isinstance(p, RunwayClose) and p.airport.upper() == airport.upper():
            fork_midnight = spec.fork_ts.replace(hour=0, minute=0, second=0, microsecond=0)
            start_minute = (spec.fork_ts - fork_midnight).total_seconds() / 60.0
            closures.append((start_minute, float(p.duration_minutes)))
    return closures


def simulate_runway_closure(
    spec: ScenarioSpec, demand: list[DemandEvent], airport: str
) -> SimulationResult:
    """Runs the exact same demand sequence twice - once against an
    unperturbed runway (the baseline), once with the scenario's RunwayClose
    perturbations applied - and returns the per-flight and aggregate delay
    this scenario adds. Both runs share one demand list and one
    SERVICE_DURATION_MIN, so any difference in the result is attributable
    only to the closure, which is what makes the diff a fair one.
    """
    closures = _closures_from_spec(spec, airport)

    baseline_waits = _run_runway_day(demand, closures=[])
    scenario_waits = _run_runway_day(demand, closures=closures)

    flights = tuple(
        FlightResult(
            flight_id=d.flight_id,
            scheduled_minute=d.scheduled_minute,
            baseline_wait_min=baseline_waits[d.flight_id],
            scenario_wait_min=scenario_waits[d.flight_id],
            added_delay_min=scenario_waits[d.flight_id] - baseline_waits[d.flight_id],
        )
        for d in demand
    )

    added = [f.added_delay_min for f in flights]
    baseline_wait_values = [f.baseline_wait_min for f in flights]
    return SimulationResult(
        airport=airport,
        flights=flights,
        total_added_delay_min=sum(added),
        affected_flight_count=sum(1 for a in added if a > 1e-9),
        max_added_delay_min=max(added, default=0.0),
        flights_simulated=len(flights),
        baseline_mean_wait_min=(
            sum(baseline_wait_values) / len(baseline_wait_values) if baseline_wait_values else 0.0
        ),
        baseline_max_wait_min=max(baseline_wait_values, default=0.0),
    )
