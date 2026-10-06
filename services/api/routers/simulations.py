"""Stage 7, scoped down (ADR 0005): `POST /api/v1/simulations` runs a real
counterfactual - close one airport's (single, representative) runway for a
real historical day and see how much delay it adds - entirely against the
cached BTS month, with no database dependency at all. This is a deliberate
scope reduction from the master spec's full job-queue/worker/WebSocket-
streaming design (see the ADR): synchronous request/response, because a
single day's single-runway DES run completes in well under a second.

Only `runway.close` perturbations are executed - the other four
`ScenarioSpec` perturbation types validate structurally (they're part of
the same Pydantic model every scenario goes through) but this endpoint
rejects them explicitly with a clear 400, rather than silently accepting
and ignoring them.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.common.schemas.scenario import RunwayClose, ScenarioSpec, ScenarioValidationError
from services.simulator import engine, network_ripple
from services.simulator.demand import build_runway_demand
from services.simulator.historical import load_simulation_month, load_simulation_reference_data
from services.simulator.spec import validate_against_reference_data

router = APIRouter(prefix="/simulations", tags=["simulations"])


class SimulationRequest(BaseModel):
    scenario: ScenarioSpec


@router.post("")
async def run_simulation(request: SimulationRequest) -> dict:
    spec = request.scenario
    runway_closes = [p for p in spec.perturbations if isinstance(p, RunwayClose)]
    other_types = {p.type for p in spec.perturbations} - {"runway.close"}
    if other_types:
        raise HTTPException(
            400,
            f"this scoped simulator only executes 'runway.close' perturbations, got: "
            f"{sorted(other_types)} (see docs/adr/0005)",
        )
    if len(runway_closes) != 1:
        raise HTTPException(
            400, "this scoped simulator requires exactly one 'runway.close' perturbation"
        )
    closure = runway_closes[0]
    airport = closure.airport.upper()

    df = load_simulation_month()
    ref = load_simulation_reference_data()
    try:
        validate_against_reference_data(spec, ref)
    except ScenarioValidationError as exc:
        raise HTTPException(400, str(exc)) from exc

    day = spec.fork_ts.date()
    fork_midnight = spec.fork_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if spec.fork_ts + dt.timedelta(minutes=spec.horizon_minutes) > fork_midnight + dt.timedelta(
        days=1
    ):
        raise HTTPException(400, "fork_ts + horizon_minutes must stay within a single calendar day")

    demand = build_runway_demand(df, airport, day)
    if not demand:
        raise HTTPException(
            404, f"no real scheduled flights found for {airport} on {day.isoformat()}"
        )

    result = engine.simulate_runway_closure(spec, demand, airport)

    start_minute = (spec.fork_ts - fork_midnight).total_seconds() / 60.0
    ripple = network_ripple.estimate_ripple(
        airport, day, start_minute, float(closure.duration_minutes)
    )

    top_delayed = sorted(result.flights, key=lambda f: f.added_delay_min, reverse=True)[:20]

    # A large baseline_mean_wait_min with zero perturbations applied would
    # mean this airport's real demand already exceeds what one modeled
    # runway can serve (see services/simulator/engine.py's docstring) -
    # surfaced honestly rather than hidden, since it changes how
    # meaningful the closure diff below actually is for this airport.
    already_saturated = result.baseline_mean_wait_min > 30.0

    return {
        "airport": airport,
        "date": day.isoformat(),
        "total_added_delay_min": round(result.total_added_delay_min, 2),
        "affected_flight_count": result.affected_flight_count,
        "max_added_delay_min": round(result.max_added_delay_min, 2),
        "flights_simulated": result.flights_simulated,
        "baseline_mean_wait_min": round(result.baseline_mean_wait_min, 2),
        "baseline_max_wait_min": round(result.baseline_max_wait_min, 2),
        "single_runway_model_already_saturated": already_saturated,
        "top_delayed_flights": [
            {
                "flight_id": f.flight_id,
                "scheduled_minute": f.scheduled_minute,
                "baseline_wait_min": round(f.baseline_wait_min, 2),
                "scenario_wait_min": round(f.scenario_wait_min, 2),
                "added_delay_min": round(f.added_delay_min, 2),
            }
            for f in top_delayed
        ],
        "network_ripple": (
            {
                "model_version": ripple.model_version,
                "horizon_minutes": ripple.horizon_minutes,
                "neighbor_delay_delta_min": {
                    k: round(v, 3) for k, v in ripple.neighbor_delay_delta_min.items()
                },
            }
            if ripple is not None
            else None
        ),
    }
