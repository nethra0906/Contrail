"""Stage 7 scope decision (ADR 0005): this project has no live scheduled-
flight timetable (see services/inference/network.py's docstring - the live
pipeline only observes actual aircraft movement, never a published
schedule), so a counterfactual "what if we close this runway" scenario has
nothing real to replay against if it only forks from the live snapshot the
rest of the master spec assumes. Instead, this scoped-down simulator forks
from the one real, already-cached, schedule-bearing dataset this project
has: a BTS month (the same January 2024 file M2/M3 already train on).

This module adapts that BTS month into the `ReferenceData` Protocol
services/simulator/spec.py's validator expects, so the exact same two-stage
ScenarioSpec validation (pure-Pydantic bounds, then reference-data facts)
gates a historical scenario the same way it would gate a live one - nothing
about spec.py changes for this to work, which is the whole point of that
module being a Protocol rather than a concrete DB dependency.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from ml.data.loaders.bts import load_month

# The single representative runway every airport in this scoped simulator
# is modeled as having - see services/simulator/engine.py's module docstring
# for why real per-airport runway idents aren't used.
SIMULATED_RUNWAY_IDENT = "SIM"

BTS_YEAR = 2024
BTS_MONTH = 1


@lru_cache(maxsize=1)
def load_simulation_month() -> pd.DataFrame:
    """Loads (from the cache M2/M3 training already populated - no network
    call on a cache hit) the BTS month this simulator replays against.
    Cached for the process lifetime: the DataFrame is ~100K+ rows and every
    simulation request would otherwise re-parse the same ZIP.
    """
    return load_month(BTS_YEAR, BTS_MONTH)


@dataclass(frozen=True)
class BtsReferenceData:
    """`ReferenceData` over a BTS month instead of live Postgres. `airports`
    is every station BTS reports for the month (IATA, e.g. "ATL") - this
    simulator's airport identifiers are BTS/M3-graph codes throughout, not
    the live ingest pipeline's ICAO ones, since there is no real crosswalk
    wired between the two for this scoped-down feature (documented in
    ADR 0005 rather than silently assumed).
    """

    airports: frozenset[str]
    flight_ids: frozenset[str]
    month_start: dt.datetime
    month_end: dt.datetime

    @classmethod
    def from_month(cls, df: pd.DataFrame) -> BtsReferenceData:
        airports = frozenset(df["origin"]).union(df["dest"])
        # Vectorized, not a per-row itertuples loop (build_flight_id's own
        # per-row form) - this project already learned that lesson the hard
        # way building M1's dataset at a similar row count (ml/datasets/
        # trajectory.py's docstring), and a 547K-row BTS month makes the
        # per-row version a multi-second cost on every single request.
        flight_ids = frozenset(
            df["carrier"].astype(str)
            + df["flight_number"].astype(str)
            + "_"
            + df["flight_date"].dt.strftime("%Y-%m-%d")
        )
        month_start = pd.Timestamp(df["flight_date"].min(), tz="UTC").to_pydatetime()
        month_end = pd.Timestamp(df["flight_date"].max(), tz="UTC").to_pydatetime() + dt.timedelta(
            days=1
        )
        return cls(
            airports=airports,
            flight_ids=flight_ids,
            month_start=month_start,
            month_end=month_end,
        )

    def airport_exists(self, icao: str) -> bool:
        return icao.upper() in self.airports

    def runway_exists(self, airport_icao: str, runway_ident: str) -> bool:
        return self.airport_exists(airport_icao) and runway_ident == SIMULATED_RUNWAY_IDENT

    def flight_exists(self, flight_id: str) -> bool:
        return flight_id in self.flight_ids

    def snapshot_retention_window(self) -> tuple[dt.datetime, dt.datetime]:
        return self.month_start, self.month_end


@lru_cache(maxsize=1)
def load_simulation_reference_data() -> BtsReferenceData:
    """Builds `BtsReferenceData` once per process and reuses it - building
    it touches every row of a 547K-row month (even vectorized, it's real
    work), and every `POST /api/v1/simulations` call would otherwise redo
    it from scratch.
    """
    return BtsReferenceData.from_month(load_simulation_month())


def build_flight_id(row) -> str:
    """One stable, deterministic identifier per BTS row - carrier + flight
    number + date, since BTS has no single surrogate key column already in
    `_USE_COLS`. Shared by demand-building and FlightCancel-style reference
    lookups so both sides agree on the same identifier for the same flight.
    """
    date_str = pd.Timestamp(row.flight_date).strftime("%Y-%m-%d")
    return f"{row.carrier}{row.flight_number}_{date_str}"
