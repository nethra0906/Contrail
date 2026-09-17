"""The discrete-event core: a deterministic priority queue of (time,
sequence) ordered events, plus the simulation clock that drives them.

This is the single highest-risk piece of the whole product - the flagship
feature's entire credibility rests on "the same (snapshot, spec, seed) always
produces byte-identical output" (docs/CONTRAIL_MASTER_SPEC.md §8, and the
Stage 7 golden determinism test it requires). That claim is only as strong as
this module, so the determinism rules are enforced structurally, not by
convention:

  - Events are ordered by (sim_time, insertion_sequence) - insertion_sequence
    is a monotonic counter assigned at schedule() time, so two events at the
    exact same sim_time always pop in the order they were scheduled, never in
    whatever order a hash or heap happened to compare equal ties. No part of
    the ordering depends on object identity, dict/set iteration, or wall
    clock.
  - SimClock.now is the ONLY notion of "time" anything in the simulator is
    allowed to use. There is no datetime.now() anywhere in this module or
    meant to be anywhere in the rest of services/simulator - real time never
    enters the loop.
  - Randomness is not used here at all; component code that needs it takes a
    services.common.determinism.Rng explicitly (see that module's docstring).
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class _QueueItem:
    sim_time: float
    sequence: int
    event: Event = field(compare=False)


@dataclass(frozen=True)
class Event:
    sim_time: float
    sequence: int
    entity_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[Event], None]


class SimClock:
    """Owns the event queue and the current simulation time. `now` only
    advances when `step()` or `run_until()` pops an event - there is no
    independent time source.
    """

    def __init__(self, start_time: float = 0.0):
        self.now: float = start_time
        self._queue: list[_QueueItem] = []
        self._sequence_counter = itertools.count()
        self._event_log: list[Event] = []
        self._cancelled: set[int] = set()  # sequence numbers, lazy-deleted on pop

    def schedule(self, delay: float, entity_id: str, event_type: str, **payload: Any) -> Event:
        """Schedule an event `delay` seconds after the current sim time.
        Returns the Event so callers can reference its identity (e.g. to
        cancel it via `cancel(event)`), but the queue itself is the source
        of truth for what will actually fire.
        """
        if delay < 0:
            raise ValueError(f"cannot schedule an event {delay}s in the past")
        sim_time = self.now + delay
        sequence = next(self._sequence_counter)
        event = Event(
            sim_time=sim_time,
            sequence=sequence,
            entity_id=entity_id,
            event_type=event_type,
            payload=payload,
        )
        heapq.heappush(self._queue, _QueueItem(sim_time, sequence, event))
        return event

    def cancel(self, event: Event) -> None:
        """Mark a previously scheduled event as cancelled. heapq has no O(log
        n) removal, so this is lazy deletion: the event stays physically in
        the queue and is skipped when step()/run_until() would otherwise pop
        it. Cancelling an event already dispatched is a silent no-op.
        """
        self._cancelled.add(event.sequence)

    def peek_next_time(self) -> float | None:
        self._drop_cancelled_head()
        return self._queue[0].sim_time if self._queue else None

    def _drop_cancelled_head(self) -> None:
        while self._queue and self._queue[0].sequence in self._cancelled:
            heapq.heappop(self._queue)

    def step(self, handler: EventHandler) -> Event | None:
        """Pop and dispatch exactly one (non-cancelled) event, advancing
        `now` to its time. Returns None if the queue is empty (the
        simulation has quiesced).
        """
        self._drop_cancelled_head()
        if not self._queue:
            return None
        item = heapq.heappop(self._queue)
        self.now = item.sim_time
        self._event_log.append(item.event)
        handler(item.event)
        return item.event

    def run_until(self, end_time: float, handler: EventHandler) -> list[Event]:
        """Dispatch events in order until either the queue is empty or the
        next event's time would exceed end_time (that event is left in the
        queue, un-dispatched, rather than clamped - the caller decides what
        "ran out of horizon" means for their metrics).
        """
        dispatched: list[Event] = []
        while True:
            next_time = self.peek_next_time()
            if next_time is None or next_time > end_time:
                break
            event = self.step(handler)
            assert event is not None  # peek just confirmed the queue is non-empty
            dispatched.append(event)
        return dispatched

    @property
    def event_log(self) -> tuple[Event, ...]:
        """Full ordered history of every event actually dispatched so far -
        this is what the golden determinism test compares byte-for-byte
        across repeated runs of the same scenario.
        """
        return tuple(self._event_log)
