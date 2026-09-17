"""Ingest service entrypoint - Stage 2 form: poll adsb.lol, normalize,
idempotently upsert directly into `state_vectors`. Stage 3 adds a Kafka
producer in front of this write so the event log becomes the source of
truth; the upsert logic here is unchanged either way since it's already
idempotent on (icao24, ts).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from sqlalchemy.dialects.postgresql import insert

from services.common.config import get_settings
from services.common.db import get_sessionmaker
from services.common.models import StateVector
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import configure_logging, get_logger
from services.ingest.normalizer import dedupe_latest
from services.ingest.sources.adsb_lol import AdsbLolSource

logger = get_logger(__name__)


async def write_batch(records: list[StateVectorIn]) -> int:
    if not records:
        return 0
    sessionmaker = get_sessionmaker()
    rows = [r.model_dump() for r in dedupe_latest(records)]
    async with sessionmaker() as session:
        stmt = insert(StateVector).values(rows)
        # (icao24, ts) is the natural idempotency key: ADS-B feeds redeliver
        # the same position report across overlapping tiles and across polls
        # that race a slow response. DO UPDATE rather than DO NOTHING so a
        # second, more complete report for the same instant still wins.
        stmt = stmt.on_conflict_do_update(
            index_elements=["icao24", "ts"],
            set_={c: stmt.excluded[c] for c in rows[0] if c not in ("icao24", "ts")},
        )
        await session.execute(stmt)
        await session.commit()
    return len(rows)


async def run() -> None:
    configure_logging()
    settings = get_settings()
    # NOTE: default poll interval in .env.example is 5s, inherited from the
    # original spec's assumption of a single fast bbox query. In practice a
    # serialized 30-tile hub sweep against adsb.lol's real rate limit takes
    # ~30s end to end (measured during development) - set
    # INGEST_POLL_INTERVAL_SECONDS=30 in .env for hub mode, or reduce to a
    # handful of tiles if you want sub-30s cadence.
    poll_interval = settings.ingest_poll_interval_seconds
    source = AdsbLolSource()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows dev shells often have no running-loop signal handler support.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal)

    logger.info("ingest_starting", tiles=len(source.tiles))
    try:
        while not stop_event.is_set():
            cycle_start = loop.time()
            batch: list[StateVectorIn] = []
            async for sv in source.poll_once():
                batch.append(sv)

            written = await write_batch(batch)
            logger.info("poll_cycle_complete", fetched=len(batch), written=written)

            elapsed = loop.time() - cycle_start
            remaining = max(0.0, poll_interval - elapsed)
            if remaining > 0:
                # Sleep for the rest of the interval, but wake early on shutdown.
                # TimeoutError here is the normal case (interval elapsed).
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=remaining)
    finally:
        await source.close()
        logger.info("ingest_stopped")


if __name__ == "__main__":
    asyncio.run(run())
