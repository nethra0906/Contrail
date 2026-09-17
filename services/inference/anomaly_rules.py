"""M4's deterministic rules layer (see docs/CONTRAIL_MASTER_SPEC.md §7,
"Anomaly detection"). This runs on every state vector with no model and no
training data, so it's live from Stage 3 onward; the learned autoencoder
layer (Stage 5) adds a second, complementary score on top of this, and the
spec requires reporting the learned layer's *lift* over rules alone - which
means this rules layer has to exist, standalone and measurable, first.

Each rule is intentionally simple and named after what a human would call it,
because these labels are what the live anomaly feed shows a viewer and what
partially seeds the M4 evaluation's positive examples.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.assembler.track_state import Phase, TrackState
from services.common.schemas.aircraft import StateVectorIn

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}  # hijack, radio failure, general emergency
RAPID_DESCENT_VRATE_FPM = -4000
RAPID_DESCENT_MAX_ALT_FT = 10000
GO_AROUND_DESCENT_ALT_FT = 3000
GO_AROUND_CLIMB_VRATE_FPM = 500


class AnomalyKind(StrEnum):
    SQUAWK_EMERGENCY = "squawk_emergency"
    RAPID_DESCENT = "rapid_descent"
    GO_AROUND = "go_around"


@dataclass(frozen=True)
class AnomalyEvent:
    kind: AnomalyKind
    score: float  # rules layer always emits 1.0 - binary detections, not calibrated probabilities
    evidence: dict


def check_emergency_squawk(sv: StateVectorIn) -> AnomalyEvent | None:
    if sv.squawk in EMERGENCY_SQUAWKS:
        return AnomalyEvent(
            kind=AnomalyKind.SQUAWK_EMERGENCY,
            score=1.0,
            evidence={"squawk": sv.squawk},
        )
    return None


def check_rapid_descent(sv: StateVectorIn) -> AnomalyEvent | None:
    if (
        not sv.on_ground
        and sv.vert_rate_fpm is not None
        and sv.vert_rate_fpm <= RAPID_DESCENT_VRATE_FPM
        and sv.baro_alt_ft is not None
        and sv.baro_alt_ft <= RAPID_DESCENT_MAX_ALT_FT
    ):
        return AnomalyEvent(
            kind=AnomalyKind.RAPID_DESCENT,
            score=1.0,
            evidence={"vert_rate_fpm": sv.vert_rate_fpm, "alt_ft": sv.baro_alt_ft},
        )
    return None


def check_go_around(
    previous: TrackState | None, current: TrackState, sv: StateVectorIn
) -> AnomalyEvent | None:
    """A go-around: the aircraft was descending toward a landing (APPROACH
    phase, low altitude) and then climbs again rather than landing. Detected
    as an APPROACH -> CLIMB phase transition below GO_AROUND_DESCENT_ALT_FT.
    """
    if previous is None:
        return None
    if (
        previous.phase == Phase.APPROACH
        and current.phase == Phase.CLIMB
        and sv.baro_alt_ft is not None
        and sv.baro_alt_ft <= GO_AROUND_DESCENT_ALT_FT
        and sv.vert_rate_fpm is not None
        and sv.vert_rate_fpm >= GO_AROUND_CLIMB_VRATE_FPM
    ):
        return AnomalyEvent(
            kind=AnomalyKind.GO_AROUND,
            score=1.0,
            evidence={"alt_ft": sv.baro_alt_ft, "vert_rate_fpm": sv.vert_rate_fpm},
        )
    return None


def check_all_rules(
    sv: StateVectorIn, previous: TrackState | None, current: TrackState
) -> list[AnomalyEvent]:
    """Every rule runs independently - a single state vector can trigger more
    than one (e.g. an emergency squawk during a rapid descent), and each is
    reported as its own AnomalyEvent rather than collapsed into one.
    """
    events = []
    for check in (check_emergency_squawk, check_rapid_descent):
        event = check(sv)
        if event is not None:
            events.append(event)
    go_around = check_go_around(previous, current, sv)
    if go_around is not None:
        events.append(go_around)
    return events
