"""Tests for the reference-data half of scenario validation
(services/simulator/spec.py) - the DB-aware checks that pure Pydantic can't
do on its own. Uses InMemoryReferenceData rather than a real database, so
these run without Docker; the real Postgres-backed implementation is a
thin adapter Stage 7 adds behind the same ReferenceData protocol.
"""

from __future__ import annotations

import datetime as dt

import pytest

from services.common.schemas.scenario import ScenarioSpec, ScenarioValidationError
from services.simulator.spec import InMemoryReferenceData, validate_against_reference_data

NOW = dt.datetime(2026, 8, 14, 18, 40, tzinfo=dt.UTC)


def _ref(**overrides) -> InMemoryReferenceData:
    base = dict(
        airports=frozenset({"KJFK", "KEWR", "KORD"}),
        runways=frozenset({("KJFK", "22L"), ("KJFK", "04R")}),
        flights=frozenset({"flight-1", "flight-2"}),
        retention_start=NOW - dt.timedelta(hours=6),
        retention_end=NOW + dt.timedelta(hours=1),
    )
    base.update(overrides)
    return InMemoryReferenceData(**base)


def _spec(**overrides) -> ScenarioSpec:
    base = dict(
        fork_ts=NOW,
        horizon_minutes=60,
        perturbations=[
            {
                "type": "runway.close",
                "airport": "KJFK",
                "runway": "22L",
                "from": NOW.isoformat(),
                "duration_minutes": 90,
            }
        ],
    )
    base.update(overrides)
    return ScenarioSpec(**base)


def test_valid_scenario_passes():
    validate_against_reference_data(_spec(), _ref())  # must not raise


def test_rejects_unknown_airport_in_runway_close():
    spec = _spec(
        perturbations=[
            {
                "type": "runway.close",
                "airport": "KXXX",
                "runway": "22L",
                "from": NOW.isoformat(),
                "duration_minutes": 90,
            }
        ]
    )
    with pytest.raises(ScenarioValidationError, match="unknown airport"):
        validate_against_reference_data(spec, _ref())


def test_rejects_unknown_runway_at_a_real_airport():
    spec = _spec(
        perturbations=[
            {
                "type": "runway.close",
                "airport": "KJFK",
                "runway": "99Z",  # KJFK exists, this runway does not
                "from": NOW.isoformat(),
                "duration_minutes": 90,
            }
        ]
    )
    with pytest.raises(ScenarioValidationError, match="unknown runway"):
        validate_against_reference_data(spec, _ref())


def test_rejects_fork_ts_before_retention_window():
    spec = _spec(fork_ts=NOW - dt.timedelta(days=2))
    with pytest.raises(ScenarioValidationError, match="retention window"):
        validate_against_reference_data(spec, _ref())


def test_rejects_fork_ts_after_retention_window():
    spec = _spec(fork_ts=NOW + dt.timedelta(hours=5))
    with pytest.raises(ScenarioValidationError, match="retention window"):
        validate_against_reference_data(spec, _ref())


def test_accepts_fork_ts_at_exact_retention_boundary():
    ref = _ref()
    spec = _spec(fork_ts=ref.retention_start)
    validate_against_reference_data(spec, ref)  # must not raise


def test_capacity_scale_validates_target_airport():
    spec = _spec(
        perturbations=[
            {"type": "capacity.scale", "target": "KXXX", "factor": 0.5, "duration_minutes": 60}
        ]
    )
    with pytest.raises(ScenarioValidationError, match="unknown airport"):
        validate_against_reference_data(spec, _ref())


def test_ground_stop_validates_airport():
    spec = _spec(perturbations=[{"type": "ground_stop", "airport": "KXXX", "duration_minutes": 60}])
    with pytest.raises(ScenarioValidationError, match="unknown airport"):
        validate_against_reference_data(spec, _ref())


def test_weather_inject_requires_no_reference_data():
    """Geometry-only perturbation - validated entirely at the Pydantic
    layer, nothing here should reject it for reference-data reasons.
    """
    spec = _spec(
        perturbations=[
            {
                "type": "weather.inject",
                "polygon": [(-74.0, 40.6), (-73.9, 40.7), (-73.8, 40.6)],
                "severity": "severe",
                "duration_minutes": 60,
            }
        ]
    )
    validate_against_reference_data(spec, _ref())  # must not raise


def test_flight_cancel_validates_all_flight_ids():
    spec = _spec(
        perturbations=[
            {"type": "flight.cancel", "flight_ids": ["flight-1", "flight-does-not-exist"]}
        ]
    )
    with pytest.raises(ScenarioValidationError, match="unknown flight_id"):
        validate_against_reference_data(spec, _ref())


def test_flight_cancel_accepts_all_known_flight_ids():
    spec = _spec(perturbations=[{"type": "flight.cancel", "flight_ids": ["flight-1", "flight-2"]}])
    validate_against_reference_data(spec, _ref())  # must not raise


def test_multiple_perturbations_all_validated_first_failure_reported():
    spec = _spec(
        perturbations=[
            {
                "type": "runway.close",
                "airport": "KJFK",
                "runway": "22L",
                "from": NOW.isoformat(),
                "duration_minutes": 90,
            },
            {"type": "ground_stop", "airport": "KXXX", "duration_minutes": 60},
        ]
    )
    with pytest.raises(ScenarioValidationError, match="unknown airport"):
        validate_against_reference_data(spec, _ref())
