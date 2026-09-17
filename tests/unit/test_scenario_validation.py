"""Every rejection rule in ScenarioSpec has a test here - this is the
security boundary between untrusted input (a UI form, or an LLM's output)
and the simulation engine. See services/common/schemas/scenario.py.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from services.common.schemas.scenario import MAX_PERTURBATIONS, ScenarioSpec

NOW = dt.datetime(2026, 8, 14, 18, 40, tzinfo=dt.UTC)


def _valid_runway_close(**overrides) -> dict:
    base = {
        "type": "runway.close",
        "airport": "KJFK",
        "runway": "22L",
        "from": NOW.isoformat(),
        "duration_minutes": 90,
    }
    base.update(overrides)
    return base


def test_valid_spec_parses():
    spec = ScenarioSpec(fork_ts=NOW, horizon_minutes=360, perturbations=[_valid_runway_close()])
    assert spec.horizon_minutes == 360
    assert len(spec.perturbations) == 1


def test_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=dt.datetime(2026, 8, 14, 18, 40),  # no tzinfo
            horizon_minutes=60,
            perturbations=[_valid_runway_close()],
        )


def test_rejects_horizon_over_max():
    with pytest.raises(ValidationError):
        ScenarioSpec(fork_ts=NOW, horizon_minutes=721, perturbations=[_valid_runway_close()])


def test_rejects_zero_horizon():
    with pytest.raises(ValidationError):
        ScenarioSpec(fork_ts=NOW, horizon_minutes=0, perturbations=[_valid_runway_close()])


def test_rejects_empty_perturbations():
    with pytest.raises(ValidationError):
        ScenarioSpec(fork_ts=NOW, horizon_minutes=60, perturbations=[])


def test_rejects_more_than_max_perturbations():
    perturbations = [_valid_runway_close() for _ in range(MAX_PERTURBATIONS + 1)]
    with pytest.raises(ValidationError):
        ScenarioSpec(fork_ts=NOW, horizon_minutes=60, perturbations=perturbations)


def test_accepts_exactly_max_perturbations():
    perturbations = [_valid_runway_close() for _ in range(MAX_PERTURBATIONS)]
    spec = ScenarioSpec(fork_ts=NOW, horizon_minutes=60, perturbations=perturbations)
    assert len(spec.perturbations) == MAX_PERTURBATIONS


def test_rejects_unknown_perturbation_type():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[{"type": "delete_database", "target": "*"}],
        )


def test_capacity_scale_rejects_factor_over_one():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {"type": "capacity.scale", "target": "KEWR", "factor": 1.5, "duration_minutes": 60}
            ],
        )


def test_capacity_scale_rejects_zero_factor():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {"type": "capacity.scale", "target": "KEWR", "factor": 0, "duration_minutes": 60}
            ],
        )


def test_weather_inject_rejects_too_few_vertices():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {
                    "type": "weather.inject",
                    "polygon": [(-74.0, 40.6), (-73.9, 40.7)],  # only 2 points
                    "severity": "severe",
                    "duration_minutes": 60,
                }
            ],
        )


def test_weather_inject_rejects_out_of_range_coordinates():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {
                    "type": "weather.inject",
                    "polygon": [(-74.0, 40.6), (-73.9, 40.7), (200.0, 40.5)],
                    "severity": "severe",
                    "duration_minutes": 60,
                }
            ],
        )


def test_weather_inject_rejects_self_intersecting_polygon():
    # A classic bowtie / figure-eight polygon - self-intersecting.
    bowtie = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {
                    "type": "weather.inject",
                    "polygon": bowtie,
                    "severity": "severe",
                    "duration_minutes": 60,
                }
            ],
        )


def test_weather_inject_rejects_too_many_vertices():
    huge_polygon = [(i * 0.001, i * 0.001) for i in range(600)]
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {
                    "type": "weather.inject",
                    "polygon": huge_polygon,
                    "severity": "severe",
                    "duration_minutes": 60,
                }
            ],
        )


def test_weather_inject_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[
                {
                    "type": "weather.inject",
                    "polygon": [(-74.0, 40.6), (-73.9, 40.7), (-73.8, 40.6)],
                    "severity": "catastrophic",  # not in the allowed enum
                    "duration_minutes": 60,
                }
            ],
        )


def test_flight_cancel_rejects_empty_list():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[{"type": "flight.cancel", "flight_ids": []}],
        )


def test_flight_cancel_rejects_over_200_ids():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=60,
            perturbations=[{"type": "flight.cancel", "flight_ids": [f"F{i}" for i in range(201)]}],
        )


def test_duration_over_24h_rejected_per_perturbation():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            fork_ts=NOW,
            horizon_minutes=1440,
            perturbations=[_valid_runway_close(duration_minutes=1441)],
        )


def test_canonical_json_is_stable_across_key_order():
    """spec_hash depends on canonical_json being order-independent - this
    guards the reproducibility contract: two logically identical specs built
    from differently-ordered dicts must hash the same.
    """
    spec_a = ScenarioSpec(fork_ts=NOW, horizon_minutes=60, perturbations=[_valid_runway_close()])
    spec_b = ScenarioSpec(perturbations=[_valid_runway_close()], horizon_minutes=60, fork_ts=NOW)
    assert spec_a.canonical_json() == spec_b.canonical_json()
