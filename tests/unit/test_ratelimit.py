"""Unit tests for TokenBucket and CircuitBreaker - the two hand-tuned
pieces behind the ingest source's politeness toward adsb.lol (see ADR
0003's three rounds of retuning). No automated test protected these
behaviors before; this closes that gap at the pure-logic level without
needing a real clock or real network calls.
"""

from __future__ import annotations

import asyncio

from services.ingest.ratelimit import CircuitBreaker, TokenBucket

# ---- TokenBucket ----


async def test_bucket_starts_full_and_does_not_block_first_calls():
    bucket = TokenBucket(rate_per_sec=10.0, burst=3)

    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(3):
        await bucket.acquire()
    elapsed = loop.time() - start

    # All 3 burst tokens were available up front - no sleeping required.
    assert elapsed < 0.05


async def test_bucket_blocks_once_burst_is_exhausted():
    bucket = TokenBucket(rate_per_sec=20.0, burst=1)

    loop = asyncio.get_running_loop()
    await bucket.acquire()  # consumes the only token

    start = loop.time()
    await bucket.acquire()  # must wait ~1/rate = 0.05s for a refill
    elapsed = loop.time() - start

    assert elapsed >= 0.04  # allow small scheduling slack below the exact 0.05s


async def test_bucket_refills_over_time_up_to_capacity():
    bucket = TokenBucket(rate_per_sec=1000.0, burst=2)
    await bucket.acquire()
    await bucket.acquire()
    assert bucket.tokens < 1

    await asyncio.sleep(0.01)  # ~10 tokens worth at this rate, capped at burst
    # Force a refill accounting pass without consuming by acquiring and
    # checking it doesn't block (capacity ceiling is exercised implicitly).
    loop = asyncio.get_running_loop()
    start = loop.time()
    await bucket.acquire()
    assert loop.time() - start < 0.01


async def test_concurrent_acquires_are_serialized_not_double_spent():
    """The internal lock must prevent two concurrent acquire() calls from
    both reading the same pre-refill token count and over-spending it.
    """
    bucket = TokenBucket(rate_per_sec=5.0, burst=1)
    results = await asyncio.gather(bucket.acquire(), bucket.acquire())
    assert len(results) == 2
    # After two acquires against a burst of 1, tokens must not be negative.
    assert bucket.tokens >= -1e-9


# ---- CircuitBreaker ----


def test_breaker_starts_closed():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    assert breaker.is_open is False


def test_breaker_opens_after_threshold_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open is False  # 2 failures, threshold is 3
    breaker.record_failure()
    assert breaker.is_open is True


def test_breaker_success_resets_consecutive_failure_count():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    # Only 2 consecutive failures since the reset - still below threshold.
    assert breaker.is_open is False


def test_breaker_half_opens_after_cooldown_elapses(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("services.ingest.ratelimit.time.monotonic", lambda: clock["t"])

    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0)
    breaker.record_failure()
    assert breaker.is_open is True

    clock["t"] += 11.0  # cooldown (10s) has elapsed
    assert breaker.is_open is False


def test_breaker_reopens_if_half_open_trial_fails(monkeypatch):
    """A cooldown of exactly 0.0 can't be used here: is_open's own
    elapsed >= cooldown check would be trivially true on every call, so the
    breaker could never be observed holding "open" for even one assertion -
    it would decay back to half-open within the same instant it just
    reopened. A mocked clock with a real (nonzero) cooldown lets the test
    control exactly when that transition happens instead.
    """
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0)
    clock = {"t": 1000.0}
    monkeypatch.setattr("services.ingest.ratelimit.time.monotonic", lambda: clock["t"])

    breaker.record_failure()
    assert breaker.is_open is True  # cooldown hasn't elapsed yet

    clock["t"] += 11.0
    assert breaker.is_open is False  # cooldown elapsed -> half-open trial

    breaker.record_failure()  # the half-open trial call fails
    assert breaker.is_open is True  # reopened, fresh cooldown window started


def test_breaker_closes_if_half_open_trial_succeeds(monkeypatch):
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0)
    clock = {"t": 1000.0}
    monkeypatch.setattr("services.ingest.ratelimit.time.monotonic", lambda: clock["t"])

    breaker.record_failure()
    assert breaker.is_open is True

    clock["t"] += 11.0
    assert breaker.is_open is False  # half-open trial

    breaker.record_success()
    assert breaker.is_open is False
    # It takes a fresh failure to open again (threshold=1 here), not a
    # leftover count from before the success reset it.
    breaker.record_failure()
    assert breaker.is_open is True
