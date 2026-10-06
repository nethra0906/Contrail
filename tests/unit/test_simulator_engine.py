"""Stage 7 (scoped, see docs/adr/0005): tests for the DES driver that
couples the already-tested SimClock and runway.py into a real
counterfactual run. Uses a small synthetic BTS-shaped DataFrame throughout
- never the real downloaded month - so this suite runs fully offline and
fast, the same convention tests/unit/test_simulator_spec.py's
InMemoryReferenceData already established for this module's neighbors.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from services.common.schemas.scenario import ScenarioSpec
from services.simulator.demand import build_runway_demand
from services.simulator.engine import simulate_runway_closure
from services.simulator.historical import SIMULATED_RUNWAY_IDENT, BtsReferenceData


def _bts_row(
    carrier,
    flight_number,
    origin,
    dest,
    crs_dep_hhmm,
    crs_arr_hhmm,
    cancelled=0.0,
    date="2024-01-15",
):
    return {
        "flight_date": pd.Timestamp(date),
        "carrier": carrier,
        "tail_number": f"N{flight_number}",
        "flight_number": str(flight_number),
        "origin": origin,
        "dest": dest,
        "crs_dep_hhmm": crs_dep_hhmm,
        "crs_arr_hhmm": crs_arr_hhmm,
        "cancelled": cancelled,
        "dep_delay_min": 0.0,
        "arr_delay_min": 0.0,
    }


@pytest.fixture
def busy_day_df():
    """20 flights into/out of "AAA" packed into a single hour (600-660,
    every 3 minutes) - dense enough that a runway closure during that hour
    has real demand to collide with.
    """
    rows = [
        _bts_row("XX", 100 + i, "AAA", "BBB", 600 + i * 3, 700 + i * 3)
        if i % 2 == 0
        else _bts_row("XX", 100 + i, "BBB", "AAA", 700 + i * 3, 600 + i * 3)
        for i in range(20)
    ]
    return pd.DataFrame(rows)


def _spec_with_closure(airport: str, start_minute: float, duration_minutes: int) -> ScenarioSpec:
    fork_ts = dt.datetime(2024, 1, 15, tzinfo=dt.UTC) + dt.timedelta(minutes=start_minute)
    return ScenarioSpec(
        fork_ts=fork_ts,
        horizon_minutes=min(duration_minutes + 60, 720),
        perturbations=[
            {
                "type": "runway.close",
                "airport": airport,
                "runway": SIMULATED_RUNWAY_IDENT,
                "from": fork_ts,
                "duration_minutes": duration_minutes,
            }
        ],
    )


def test_closure_during_busy_period_adds_real_delay(busy_day_df):
    demand = build_runway_demand(busy_day_df, "AAA", dt.date(2024, 1, 15))
    # busy_day_df packs demand into real-clock 06:00-06:57 (HHMM 600-657),
    # i.e. scheduled_minute 360-417 after midnight - the closure below must
    # overlap that, not raw minute 615 (which would be 10:15, long after).
    spec = _spec_with_closure("AAA", start_minute=380.0, duration_minutes=20)

    result = simulate_runway_closure(spec, demand, "AAA")

    assert result.total_added_delay_min > 0
    assert result.affected_flight_count > 0
    assert result.max_added_delay_min > 0


def test_closure_never_decreases_total_delay_vs_baseline(busy_day_df):
    """The same monotonicity property test_runway.py already proves at the
    pure-function level, re-checked here at the full-engine level with a
    real demand sequence driving it.
    """
    demand = build_runway_demand(busy_day_df, "AAA", dt.date(2024, 1, 15))
    spec = _spec_with_closure("AAA", start_minute=380.0, duration_minutes=20)

    result = simulate_runway_closure(spec, demand, "AAA")

    for flight in result.flights:
        assert flight.added_delay_min >= -1e-9


def test_longer_closure_adds_at_least_as_much_delay(busy_day_df):
    demand = build_runway_demand(busy_day_df, "AAA", dt.date(2024, 1, 15))
    short = simulate_runway_closure(
        _spec_with_closure("AAA", start_minute=380.0, duration_minutes=10), demand, "AAA"
    )
    long = simulate_runway_closure(
        _spec_with_closure("AAA", start_minute=380.0, duration_minutes=40), demand, "AAA"
    )
    assert long.total_added_delay_min >= short.total_added_delay_min


def test_result_is_deterministic_across_repeated_runs(busy_day_df):
    demand = build_runway_demand(busy_day_df, "AAA", dt.date(2024, 1, 15))
    spec = _spec_with_closure("AAA", start_minute=380.0, duration_minutes=20)

    first = simulate_runway_closure(spec, demand, "AAA")
    second = simulate_runway_closure(spec, demand, "AAA")

    assert first == second


def test_no_closure_overlapping_demand_means_zero_added_delay(busy_day_df):
    demand = build_runway_demand(busy_day_df, "AAA", dt.date(2024, 1, 15))
    # Closure scheduled long after every flight in this fixture.
    spec = _spec_with_closure("AAA", start_minute=1000.0, duration_minutes=20)

    result = simulate_runway_closure(spec, demand, "AAA")

    assert result.total_added_delay_min == 0.0
    assert result.affected_flight_count == 0


def test_build_runway_demand_only_includes_this_airport_and_day():
    df = pd.DataFrame(
        [
            _bts_row("XX", 1, "AAA", "BBB", 600, 700),
            _bts_row("XX", 2, "CCC", "BBB", 600, 700),  # different origin airport
            _bts_row("XX", 3, "AAA", "BBB", 600, 700, date="2024-01-16"),  # different day
            _bts_row("XX", 4, "AAA", "BBB", 600, 700, cancelled=1.0),  # cancelled
        ]
    )
    demand = build_runway_demand(df, "AAA", dt.date(2024, 1, 15))
    assert len(demand) == 1
    assert demand[0].flight_id == "XX1_2024-01-15_dep"


def test_bts_reference_data_reflects_real_month_contents():
    df = pd.DataFrame([_bts_row("XX", 1, "AAA", "BBB", 600, 700)])
    ref = BtsReferenceData.from_month(df)

    assert ref.airport_exists("aaa")  # case-insensitive
    assert ref.airport_exists("BBB")
    assert not ref.airport_exists("ZZZ")
    assert ref.runway_exists("AAA", SIMULATED_RUNWAY_IDENT)
    assert not ref.runway_exists("AAA", "09L")
    assert ref.flight_exists("XX1_2024-01-15")
