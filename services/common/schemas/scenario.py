"""ScenarioSpec - the ONLY shape a counterfactual scenario is allowed to take.

This is the security boundary of the simulator: an LLM, a UI form, or a raw
API call all funnel through this validator before anything reaches the
simulation engine (services/simulator/spec.py re-uses these models). Nothing
here is optional politeness - every bound exists to stop a scenario from being
a resource-exhaustion or nonsense-input vector, and every rejection rule has a
test in tests/unit/test_scenario_validation.py.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_PERTURBATIONS = 10
MAX_HORIZON_MINUTES = 720
MAX_POLYGON_VERTICES = 500
MIN_POLYGON_VERTICES = 3


class RunwayClose(BaseModel):
    type: Literal["runway.close"]
    airport: str = Field(min_length=3, max_length=4)
    runway: str = Field(min_length=1, max_length=8)
    from_: dt.datetime = Field(alias="from")
    duration_minutes: int = Field(gt=0, le=1440)

    model_config = {"populate_by_name": True}


class CapacityScale(BaseModel):
    type: Literal["capacity.scale"]
    target: str = Field(min_length=3, max_length=4)
    factor: float = Field(gt=0, le=1.0)
    duration_minutes: int = Field(gt=0, le=1440)


class WeatherInject(BaseModel):
    type: Literal["weather.inject"]
    polygon: list[tuple[float, float]] = Field(min_length=MIN_POLYGON_VERTICES)
    severity: Literal["moderate", "severe", "extreme"]
    duration_minutes: int = Field(gt=0, le=1440)

    @field_validator("polygon")
    @classmethod
    def bounded_and_valid(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(v) > MAX_POLYGON_VERTICES:
            raise ValueError(f"polygon exceeds {MAX_POLYGON_VERTICES} vertices")
        for lon, lat in v:
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"polygon vertex ({lon}, {lat}) out of range")
        _reject_self_intersecting(v)
        return v


class GroundStop(BaseModel):
    type: Literal["ground_stop"]
    carrier: str | None = Field(default=None, max_length=4)
    airport: str = Field(min_length=3, max_length=4)
    duration_minutes: int = Field(gt=0, le=1440)


class FlightCancel(BaseModel):
    type: Literal["flight.cancel"]
    flight_ids: list[str] = Field(min_length=1, max_length=200)


Perturbation = Annotated[
    RunwayClose | CapacityScale | WeatherInject | GroundStop | FlightCancel,
    Field(discriminator="type"),
]


class ScenarioSpec(BaseModel):
    fork_ts: dt.datetime
    horizon_minutes: int = Field(gt=0, le=MAX_HORIZON_MINUTES)
    perturbations: list[Perturbation] = Field(min_length=1, max_length=MAX_PERTURBATIONS)

    @model_validator(mode="after")
    def fork_ts_not_in_future_relative_to_call(self) -> ScenarioSpec:
        # Retention-window bound (data must exist to fork from) is enforced at
        # the API layer against the actual snapshot retention config, not here
        # - this model has no DB access. This validator only catches the
        # structurally-nonsensical case of an unset/naive timestamp.
        if self.fork_ts.tzinfo is None:
            raise ValueError("fork_ts must be timezone-aware")
        return self

    def canonical_json(self) -> str:
        """Stable serialization used to compute spec_hash - sorted keys, no
        whitespace, so the same logical spec always hashes the same.
        """
        return json.dumps(
            self.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )


class ScenarioValidationError(Exception):
    """Raised by services/simulator/spec.py for rejections that need DB
    context (unknown airport/runway, fork_ts outside retention) which this
    pure-pydantic layer can't check on its own.
    """


def _reject_self_intersecting(polygon: list[tuple[float, float]]) -> None:
    """Shoelace-based simple self-intersection check via shapely, imported
    lazily so this module has no hard dependency for callers that only need
    the pydantic bounds checks (e.g. frontend-adjacent tooling).
    """
    from shapely.geometry import Polygon

    poly = Polygon(polygon)
    if not poly.is_valid:
        raise ValueError("polygon is self-intersecting or degenerate")
