"""Builds a real runway-demand sequence - every scheduled arrival or
departure at one airport on one day, in scheduled-time order - from a BTS
month (services/simulator/historical.py). This is the "aircraft that want
the runway" input services/simulator/engine.py's DES driver replays through
runway.py's single-server queue; no synthetic demand is ever fabricated,
per master-spec rule 10.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from services.simulator.historical import build_flight_id


@dataclass(frozen=True)
class DemandEvent:
    flight_id: str
    scheduled_minute: float  # minutes after midnight UTC on the simulated day
    operation: str  # "departure" | "arrival"


def _hhmm_to_minutes(hhmm) -> float | None:
    if pd.isna(hhmm):
        return None
    v = int(hhmm)
    return float((v // 100) * 60 + (v % 100))


def build_runway_demand(df: pd.DataFrame, airport: str, day: dt.date) -> list[DemandEvent]:
    """Filters to real BTS rows for `airport` on `day` (as either origin or
    destination; cancelled flights never occupied a runway so they're
    excluded - they generated no real demand, not demand this simulator is
    choosing to ignore), and returns one DemandEvent per real scheduled
    operation, sorted by scheduled time.

    A flight where this airport is both origin and dest (shouldn't happen
    in real BTS data but isn't assumed impossible) would otherwise collide
    on the shared flight_id for its departure and arrival events - the
    `operation` suffix keeps them distinct.
    """
    day_df = df[(df["flight_date"].dt.date == day) & (df["cancelled"] != 1)]
    events: list[DemandEvent] = []

    departures = day_df[day_df["origin"] == airport]
    for row in departures.itertuples():
        minute = _hhmm_to_minutes(row.crs_dep_hhmm)
        if minute is not None:
            events.append(
                DemandEvent(
                    flight_id=f"{build_flight_id(row)}_dep",
                    scheduled_minute=minute,
                    operation="departure",
                )
            )

    arrivals = day_df[day_df["dest"] == airport]
    for row in arrivals.itertuples():
        minute = _hhmm_to_minutes(row.crs_arr_hhmm)
        if minute is not None:
            events.append(
                DemandEvent(
                    flight_id=f"{build_flight_id(row)}_arr",
                    scheduled_minute=minute,
                    operation="arrival",
                )
            )

    events.sort(key=lambda e: (e.scheduled_minute, e.flight_id))
    return events
