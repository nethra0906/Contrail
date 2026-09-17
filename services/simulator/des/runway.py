"""A runway modeled as a single-server queue: exactly one aircraft can be
served (landing or departing) at a time, service takes some duration, and a
`runway.close` scenario perturbation (see
services/common/schemas/scenario.py) simply adds a closed interval that no
service may be scheduled inside.

Pure, immutable state-transition functions - no SimClock dependency, no I/O.
The simulator's DES driver (Stage 7) will be the thing that actually calls
these from inside SimClock event handlers; keeping this layer pure is what
makes "closing a runway must increase delay monotonically" (a Stage 7 DoD
test named explicitly in the master spec) something you can test directly
against this module today, without a running simulator around it.

Per the spec: real service-time distributions are fitted from historical
data per (airport, runway, configuration, wtc-pair) - this module takes a
`service_duration` as a parameter rather than assuming one, precisely so the
fitted distribution can be plugged in later without this module changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Closure:
    start: float
    end: float

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError(f"closure end ({self.end}) must be after start ({self.start})")

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class RunwayState:
    """`busy_until`: the runway is committed to whatever is currently being
    serviced up to this sim-time. `closures`: scenario-injected closed
    intervals, sorted by start time (callers of `close()` don't need to
    maintain that themselves).
    """

    busy_until: float = 0.0
    closures: tuple[Closure, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ServiceResult:
    start_time: float
    finish_time: float
    wait_time: float  # start_time - arrival_time - the delay this request incurred
    new_state: RunwayState


def close_runway(state: RunwayState, start: float, duration: float) -> RunwayState:
    closure = Closure(start, start + duration)
    closures = tuple(sorted((*state.closures, closure), key=lambda c: c.start))
    return RunwayState(busy_until=state.busy_until, closures=closures)


def is_closed_at(state: RunwayState, t: float) -> bool:
    return any(c.contains(t) for c in state.closures)


def _next_open_time(state: RunwayState, t: float) -> float:
    """Earliest time >= t that is not inside any closure. Closures are
    sorted, so a single forward pass suffices - no need to re-scan from the
    start after each nudge.
    """
    for closure in state.closures:
        if closure.contains(t):
            t = closure.end
    return t


def request_service(
    state: RunwayState, arrival_time: float, service_duration: float
) -> ServiceResult:
    """Compute when a request arriving at `arrival_time` and needing
    `service_duration` can actually be served, given the runway's current
    commitments and any closures - and return the new state reflecting that
    commitment.

    The runway can't start service earlier than max(arrival_time,
    busy_until), and once a candidate start is found it must be checked
    against closures TWICE: the start itself might fall in a closure, and
    even a start that's clear might have the closure begin again mid-service
    if a later closure starts before the service would finish - in which
    case the whole service window has to be pushed past that closure and
    re-checked from there. The loop below fixes both cases to a stable point.
    """
    if service_duration <= 0:
        raise ValueError("service_duration must be positive")

    candidate_start = max(arrival_time, state.busy_until)
    while True:
        opened_start = _next_open_time(state, candidate_start)
        candidate_finish = opened_start + service_duration
        # Does any closure begin inside [opened_start, candidate_finish)?
        # If so, service can't run through it - push start to that closure's
        # end and recheck from there.
        conflicting = next(
            (c for c in state.closures if opened_start < c.start < candidate_finish), None
        )
        if conflicting is None:
            start_time = opened_start
            finish_time = candidate_finish
            break
        candidate_start = conflicting.end

    return ServiceResult(
        start_time=start_time,
        finish_time=finish_time,
        wait_time=start_time - arrival_time,
        new_state=RunwayState(busy_until=finish_time, closures=state.closures),
    )
