"""Token-bucket rate limiter + circuit breaker, shared by every source
adapter. External ADS-B feeds have no SLA and no contractual rate limit
(they're community-run) - treating them politely is both an engineering
requirement (don't get IP-banned) and a correctness one (a dead feed must
fail over, not silently stop the pipeline).
"""

from __future__ import annotations

import asyncio
import time

from services.common.telemetry import get_logger

logger = get_logger(__name__)


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self.tokens = 0
            else:
                self.tokens -= 1


class CircuitBreaker:
    """Simple three-state breaker: closed -> open (after N consecutive
    failures) -> half-open (after a cooldown, one trial call) -> closed/open.
    """

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0, name: str = ""):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.name = name
        self._consecutive_failures = 0
        self._state = "closed"
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            assert self._opened_at is not None
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = "half_open"
                return False
            return True
        return False

    def record_success(self) -> None:
        if self._state == "half_open":
            logger.info("circuit_closed", source=self.name)
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold and self._state != "open":
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit_opened", source=self.name, consecutive_failures=self._consecutive_failures
            )
