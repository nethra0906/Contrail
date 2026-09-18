"""Pure per-message decision logic for the live assembler.

Given one new state vector and the assembler's in-memory per-aircraft state,
this decides what phase-classification and leg-detection work happened. It
never touches Kafka, Redis, or Postgres - all of that lives in main.py - so
it's unit-testable with plain dicts and no running infrastructure, the same
philosophy as track_state.py and leg_detect.py that it composes.

Nearest-airport resolution is a DB lookup, so it's the caller's job: call
`advance_track` first, check whether it reports a takeoff/landing, resolve
the nearest airport only in that case, then call `open_leg_if_takeoff` /
`close_leg_if_landing` with the result.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import cast

from services.assembler.leg_detect import (
    ClosedLeg,
    OpenLeg,
    is_plausible_leg_duration,
    on_landing,
    on_takeoff,
)
from services.assembler.track_state import TrackState, advance, should_reset_for_gap, with_reset
from services.common.schemas.aircraft import StateVectorIn


@dataclass
class AssemblerState:
    """Caller-owned, per-process in-memory state. A process restart drops it
    - acceptable for a single assembler replica; externalizing it (e.g. to
    Redis) only matters once Stage 3 needs more than one replica running.
    """

    tracks: dict[str, TrackState] = field(default_factory=dict)
    open_legs: dict[str, OpenLeg] = field(default_factory=dict)


def advance_track(sv: StateVectorIn, state: AssemblerState) -> TrackState:
    previous = state.tracks.get(sv.icao24)
    if previous is not None and should_reset_for_gap(previous.last_ts, sv.ts):
        previous = with_reset(previous)

    track = advance(sv, previous)
    state.tracks[sv.icao24] = track
    return track


def _ts(track: TrackState) -> dt.datetime:
    # TrackState.last_ts is loosely typed as `object` to avoid a datetime
    # import in track_state.py; every value ever stored there is in fact a
    # datetime, set from StateVectorIn.ts.
    return cast(dt.datetime, track.last_ts)


def open_leg_if_takeoff(
    track: TrackState, state: AssemblerState, nearest_airport_icao: str | None
) -> OpenLeg | None:
    if not track.just_took_off:
        return None
    leg = on_takeoff(track, _ts(track), nearest_airport_icao)
    state.open_legs[track.icao24] = leg
    return leg


def close_leg_if_landing(
    track: TrackState, state: AssemblerState, nearest_airport_icao: str | None
) -> tuple[ClosedLeg | None, bool]:
    """Returns (closed_leg, rejected). `rejected` is True when a landing was
    observed with no plausible matching open leg (no takeoff seen, e.g. the
    aircraft was already airborne when the assembler started, or the gap
    between them fails the sanity bound) - logged by the caller, not synced
    into a fabricated leg.
    """
    if not track.just_landed:
        return None, False

    open_leg = state.open_legs.pop(track.icao24, None)
    if open_leg is None or not is_plausible_leg_duration(open_leg, _ts(track)):
        return None, True

    return on_landing(open_leg, _ts(track), nearest_airport_icao), False
