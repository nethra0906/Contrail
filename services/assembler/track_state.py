"""Per-aircraft track state machine: turns a stream of raw position reports
into phase-labeled state (ground / climb / cruise / descent / approach) and
detects takeoff/landing transitions that `leg_detect.py` uses to open/close
flight legs.

Deliberately a pure, synchronous state machine with no I/O - that's what
makes it unit-testable without Kafka/Postgres and reusable identically from
both the live assembler and offline BTS/historical backfill.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from services.common.schemas.aircraft import StateVectorIn

# A gap longer than this between two reports for the same aircraft is treated
# as a coverage dropout, not a continuous track - the phase classifier resets
# rather than inferring a phase transition across a gap it didn't observe
# (e.g. an oceanic aircraft disappearing from CONUS coverage for 8 minutes
# should not be seen as "aggressively climbed/descended instantly").
COVERAGE_GAP_SECONDS = 120

# Thresholds used for phase classification - deliberately simple and
# explainable (a rules-based classifier, not a model) because this feeds
# ground-truth phase labels that the trajectory model (M1) trains and
# evaluates against per-phase; the classifier must be inspectable, not a
# black box grading its own homework.
CLIMB_VRATE_FPM = 300
DESCENT_VRATE_FPM = -300
APPROACH_ALT_FT = 3000
CRUISE_ALT_FT = 18000


class Phase(StrEnum):
    GROUND = "ground"
    CLIMB = "climb"
    CRUISE = "cruise"
    DESCENT = "descent"
    APPROACH = "approach"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrackState:
    icao24: str
    phase: Phase
    last_ts: object  # datetime, kept loosely typed to avoid importing dt here twice
    last_on_ground: bool
    just_took_off: bool = False
    just_landed: bool = False


def classify_phase(sv: StateVectorIn, previous: TrackState | None) -> Phase:
    if sv.on_ground:
        return Phase.GROUND

    alt = sv.baro_alt_ft
    vrate = sv.vert_rate_fpm

    if alt is None:
        return Phase.UNKNOWN

    if (
        alt < APPROACH_ALT_FT
        and previous is not None
        and previous.phase
        in (
            Phase.DESCENT,
            Phase.APPROACH,
        )
    ):
        return Phase.APPROACH

    if vrate is not None and vrate >= CLIMB_VRATE_FPM:
        return Phase.CLIMB
    if vrate is not None and vrate <= DESCENT_VRATE_FPM:
        return Phase.DESCENT
    if alt >= CRUISE_ALT_FT:
        return Phase.CRUISE

    # Level flight below cruise altitude with no strong vertical rate: most
    # often the tail end of a climb or a low-altitude cruise segment. Fall
    # back to continuity with the previous phase rather than guessing.
    if previous is not None and previous.phase != Phase.UNKNOWN:
        return previous.phase
    return Phase.UNKNOWN


def advance(sv: StateVectorIn, previous: TrackState | None) -> TrackState:
    """Consume one new state vector, returning the updated track state.

    `previous` is None for an aircraft's first-ever observed report, or when
    the gap since its last report exceeded COVERAGE_GAP_SECONDS (the caller
    is responsible for that reset - see should_reset_for_gap below - because
    only the caller has access to real wall-clock/DB context this pure
    function deliberately avoids).
    """
    phase = classify_phase(sv, previous)

    just_took_off = bool(previous is not None and previous.last_on_ground and not sv.on_ground)
    just_landed = bool(previous is not None and not previous.last_on_ground and sv.on_ground)

    return TrackState(
        icao24=sv.icao24,
        phase=phase,
        last_ts=sv.ts,
        last_on_ground=sv.on_ground,
        just_took_off=just_took_off,
        just_landed=just_landed,
    )


def should_reset_for_gap(previous_ts, new_ts) -> bool:
    gap_seconds = (new_ts - previous_ts).total_seconds()
    return gap_seconds > COVERAGE_GAP_SECONDS


def with_reset(state: TrackState) -> TrackState:
    """Used when a coverage gap is detected: keep identity but drop phase
    continuity, since we can't vouch for what happened during the gap.
    """
    return replace(state, phase=Phase.UNKNOWN, just_took_off=False, just_landed=False)
