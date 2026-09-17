from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from services.common.config import get_settings
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import INGEST_ERRORS_TOTAL, INGEST_MESSAGES_TOTAL, get_logger
from services.ingest.normalizer import normalize_adsb_lol
from services.ingest.ratelimit import CircuitBreaker, TokenBucket
from services.ingest.sources.base import Source
from services.ingest.sources.tiling import Tile, core_hub_tiles

logger = get_logger(__name__)

# adsb.lol publishes no formal SLA/rate limit (community-run, ADS-B-Exchange
# compatible API). Measured live on 2026-09-17 across two separate sessions:
# even a fully serialized sweep at 0.5-1 req/s, and even with 3s spacing
# between requests, draws 429s starting from roughly the second or third
# never-before-queried coordinate in a sweep - a fresh point succeeds once,
# then further fresh points fail regardless of how far apart they're spaced.
# That pattern looks like a budget/quota counter close to exhausted (from
# cumulative development-session testing), not a steady-state req/s limit -
# see docs/adr/0003-hub-tiling.md for the full investigation. Until that
# budget's actual size/reset window is known, defaults are conservative:
# a smaller 8-hub tile set (core_hub_tiles(), not the full 30-hub
# hub_tiles()) and generous spacing.
DEFAULT_RATE_PER_SEC = 0.33
DEFAULT_BURST = 1


class RetryableError(Exception):
    pass


class AdsbLolSource(Source):
    name = "adsb_lol"

    def __init__(
        self,
        tiles: list[Tile] | None = None,
        rate_per_sec: float = DEFAULT_RATE_PER_SEC,
        burst: int = DEFAULT_BURST,
        max_concurrent: int = 1,
    ):
        settings = get_settings()
        self.base_url = settings.adsb_lol_base_url.rstrip("/")
        self.tiles = tiles or core_hub_tiles()
        self.bucket = TokenBucket(rate_per_sec, burst)
        # A longer cooldown than the default 30s: if we ARE being throttled
        # for a budget that resets on the order of minutes, retrying every
        # 30s just keeps failing and keeps counting against whatever budget
        # remains. 5 minutes is a more patient default; tune down once the
        # real limit is characterized.
        self.breaker = CircuitBreaker(name=self.name, cooldown_seconds=300.0, failure_threshold=2)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # adsb.lol sits behind Cloudflare, which 403s any request with no
        # User-Agent header (verified during development - httpx sends none
        # by default). A descriptive UA is also just good manners against a
        # free, community-run API.
        self._client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "contrail/0.1 (student flagship project; github.com/contrail)"},
        )

    @retry(
        # Fewer, shorter retries than before: if the 429s really are a
        # budget/quota issue rather than a transient rate spike, hammering
        # with 4 retries per tile just burns more of whatever budget exists
        # without ever succeeding. 2 attempts fails faster and lets the
        # circuit breaker (not tenacity) own the "back off for a while"
        # decision at the poll-cycle level.
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(2),
        wait=wait_exponential_jitter(initial=1.0, max=10),
        reraise=True,
    )
    async def _fetch_tile(self, tile: Tile) -> list[dict]:
        await self.bucket.acquire()
        url = f"{self.base_url}/lat/{tile.lat}/lon/{tile.lon}/dist/{int(tile.radius_nm)}"
        try:
            resp = await self._client.get(url)
        except httpx.TransportError as exc:
            raise RetryableError(str(exc)) from exc

        if resp.status_code == 429:
            raise RetryableError("rate limited (429)")
        if resp.status_code >= 500:
            raise RetryableError(f"server error {resp.status_code}")
        resp.raise_for_status()
        return resp.json().get("ac", [])

    async def poll_once(self) -> AsyncIterator[StateVectorIn]:
        if self.breaker.is_open:
            logger.warning("skipping_poll_circuit_open", source=self.name)
            return

        async def bounded_fetch(tile: Tile) -> list[dict]:
            async with self._semaphore:
                return await self._fetch_tile(tile)

        results = await asyncio.gather(
            *(bounded_fetch(t) for t in self.tiles), return_exceptions=True
        )

        seen_icao24: set[str] = set()
        any_success = False
        for tile, result in zip(self.tiles, results, strict=True):
            if isinstance(result, Exception):
                INGEST_ERRORS_TOTAL.labels(source=self.name, kind=type(result).__name__).inc()
                logger.warning("tile_fetch_failed", tile=tile, error=str(result))
                continue
            any_success = True
            for raw in result:
                sv = normalize_adsb_lol(raw)
                if sv is None or sv.icao24 in seen_icao24:
                    continue
                seen_icao24.add(sv.icao24)
                INGEST_MESSAGES_TOTAL.labels(source=self.name).inc()
                yield sv

        if any_success:
            self.breaker.record_success()
        else:
            self.breaker.record_failure()

    async def close(self) -> None:
        await self._client.aclose()
