"""The second half of ScenarioSpec validation - the checks that need
reference data (does this airport/runway actually exist? is fork_ts inside
the retention window we can actually snapshot from?) which
services/common/schemas/scenario.py deliberately can't do on its own (it's
pure Pydantic, no DB access). See that module's docstring for why the split
exists, and docs/CONTRAIL_MASTER_SPEC.md §10 ("Validation happens in
simulator/spec.py - NOT in the LLM layer").

`ReferenceData` is a small Protocol rather than a direct DB dependency, so
this module - the actual security boundary the simulation engine trusts -
is fully unit-testable with an in-memory fake today, without Postgres, and
without changing a line when Stage 7 wires it to the real database.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from services.common.schemas.scenario import (
    CapacityScale,
    FlightCancel,
    GroundStop,
    RunwayClose,
    ScenarioSpec,
    ScenarioValidationError,
    WeatherInject,
)


class ReferenceData(Protocol):
    """What simulator validation needs to know about the world. The real
    implementation queries Postgres (services.common.models.Airport/Runway);
    tests implement this with a plain dict-backed fake.
    """

    def airport_exists(self, icao: str) -> bool: ...
    def runway_exists(self, airport_icao: str, runway_ident: str) -> bool: ...
    def flight_exists(self, flight_id: str) -> bool: ...
    def snapshot_retention_window(self) -> tuple[dt.datetime, dt.datetime]:
        """(earliest, latest) timestamps a snapshot can currently be
        materialized from - bounded by the hot-tier retention policy
        (see migrations/versions/0001_initial_schema.py's retention
        policies on state_vectors and its continuous aggregates).
        """
        ...


@dataclass(frozen=True)
class InMemoryReferenceData:
    """Test double / offline-tooling implementation of ReferenceData."""

    airports: frozenset[str]
    runways: frozenset[tuple[str, str]]  # (airport_icao, runway_ident)
    flights: frozenset[str]
    retention_start: dt.datetime
    retention_end: dt.datetime

    def airport_exists(self, icao: str) -> bool:
        return icao.upper() in self.airports

    def runway_exists(self, airport_icao: str, runway_ident: str) -> bool:
        return (airport_icao.upper(), runway_ident) in self.runways

    def flight_exists(self, flight_id: str) -> bool:
        return flight_id in self.flights

    def snapshot_retention_window(self) -> tuple[dt.datetime, dt.datetime]:
        return self.retention_start, self.retention_end


def validate_against_reference_data(spec: ScenarioSpec, ref: ReferenceData) -> None:
    """Raises ScenarioValidationError on the first violation found. Called
    AFTER the pure-Pydantic ScenarioSpec has already validated successfully
    - this function assumes structural validity and only checks facts about
    the world.
    """
    retention_start, retention_end = ref.snapshot_retention_window()
    if not (retention_start <= spec.fork_ts <= retention_end):
        raise ScenarioValidationError(
            f"fork_ts {spec.fork_ts} is outside the current snapshot retention "
            f"window [{retention_start}, {retention_end}]"
        )

    for p in spec.perturbations:
        _validate_perturbation(p, ref)


def _validate_perturbation(p, ref: ReferenceData) -> None:
    if isinstance(p, RunwayClose):
        if not ref.airport_exists(p.airport):
            raise ScenarioValidationError(f"unknown airport {p.airport}")
        if not ref.runway_exists(p.airport, p.runway):
            raise ScenarioValidationError(f"unknown runway {p.runway} at {p.airport}")

    elif isinstance(p, CapacityScale):
        if not ref.airport_exists(p.target):
            raise ScenarioValidationError(f"unknown airport {p.target}")

    elif isinstance(p, GroundStop):
        if not ref.airport_exists(p.airport):
            raise ScenarioValidationError(f"unknown airport {p.airport}")

    elif isinstance(p, WeatherInject):
        pass  # geometry already validated at the Pydantic layer; no reference-data check needed

    elif isinstance(p, FlightCancel):
        unknown = [fid for fid in p.flight_ids if not ref.flight_exists(fid)]
        if unknown:
            raise ScenarioValidationError(f"unknown flight_id(s): {unknown[:5]}")

    else:  # pragma: no cover - unreachable if ScenarioSpec's discriminated union is exhaustive
        raise ScenarioValidationError(
            f"no reference-data validator for perturbation type {type(p)}"
        )
