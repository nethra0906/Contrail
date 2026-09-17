"""Turns takeoff/landing transitions (from track_state.py) into opened and
closed flight legs. Pure logic, no I/O - the caller (the live assembler, or
an offline backfill job) is responsible for persisting the Flight rows this
produces and for looking up the previous leg to set `prev_leg_flight_id`
(the aircraft-rotation edge the delay-propagation GNN and the DES simulator
both depend on).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from services.assembler.track_state import TrackState


@dataclass(frozen=True)
class OpenLeg:
    """A flight leg observed to have started (takeoff detected) but not yet
    closed. Held in the caller's in-memory/Redis state until landing.
    """

    flight_id: uuid.UUID
    icao24: str
    actual_dep: dt.datetime
    origin_icao: str | None  # nearest airport at takeoff, if resolvable


@dataclass(frozen=True)
class ClosedLeg:
    flight_id: uuid.UUID
    icao24: str
    actual_dep: dt.datetime
    actual_arr: dt.datetime
    origin_icao: str | None
    dest_icao: str | None


def on_takeoff(state: TrackState, ts: dt.datetime, nearest_airport_icao: str | None) -> OpenLeg:
    """Called when track_state.advance() reports just_took_off=True."""
    return OpenLeg(
        flight_id=uuid.uuid4(),
        icao24=state.icao24,
        actual_dep=ts,
        origin_icao=nearest_airport_icao,
    )


def on_landing(open_leg: OpenLeg, ts: dt.datetime, nearest_airport_icao: str | None) -> ClosedLeg:
    """Called when track_state.advance() reports just_landed=True for an
    aircraft that has a matching OpenLeg. If no OpenLeg exists (e.g. the
    aircraft was already airborne when ingestion started), the caller should
    not call this - that landing has no matching takeoff and is logged, not
    synthesized into a leg with a fabricated origin.
    """
    return ClosedLeg(
        flight_id=open_leg.flight_id,
        icao24=open_leg.icao24,
        actual_dep=open_leg.actual_dep,
        actual_arr=ts,
        origin_icao=open_leg.origin_icao,
        dest_icao=nearest_airport_icao,
    )


def is_plausible_leg_duration(open_leg: OpenLeg, landing_ts: dt.datetime) -> bool:
    """Sanity bound used to reject landing/takeoff pairs that clearly aren't
    the same leg (e.g. a brief loss of the on_ground flag from noisy data
    that "took off" and "landed" a few seconds later). Real CONUS flights
    range from a few minutes (short hops) to ~6 hours (coast to coast plus
    padding); this is intentionally generous rather than tuned to reject
    edge cases we haven't observed yet.
    """
    duration = landing_ts - open_leg.actual_dep
    return dt.timedelta(minutes=2) <= duration <= dt.timedelta(hours=8)
