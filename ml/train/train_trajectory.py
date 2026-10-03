"""M1 (trajectory forecasting) training entrypoint.

The shared feature implementation already exists
(services/common/features/trajectory.py, built ahead of schedule as Stage 3
prep). What M1 needs that doesn't exist yet is a real training set: hours of
continuous historical ADS-B track data, windowed by an
`ml/datasets/trajectory.py` this repo doesn't have yet, built from either
`state_vectors` (6-hour raw retention in Timescale - rarely enough on its
own) or the assembler's Parquet archive
(services/assembler/sinks/parquet.py), which only starts accumulating once
the live stack has actually been running.

Until then, training on what's available would mean training on a few hours
of data at best - or, worse, substituting synthetic trajectories, which
execution rule 10 forbids outright: "If ADS-B coverage or a data source
proves unworkable, STOP and report it rather than substituting synthetic
data silently."

So this checks whether `state_vectors` holds enough history to be worth
training on, and if not, logs exactly why and exits cleanly - not a
failure, since `make train`'s DoD is about producing real numbers, which
this honestly cannot do yet - rather than either breaking the `make train`
target or fabricating a report. See M2's train_eta.py for the shape this
should take (loader -> dataset -> baseline -> model -> ml/eval -> report)
once there's real data to point it at.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from services.common.db import get_sessionmaker
from services.common.models import StateVector
from services.common.telemetry import configure_logging, get_logger

logger = get_logger(__name__)

# A conservative floor: M1 trains on 12-point, 60-second windows (see
# features/trajectory.py's WINDOW_SIZE) - a useful training set needs many
# thousands of distinct windows across many aircraft and flight phases, not
# just "some rows exist."
MIN_HISTORY_HOURS = 6.0
MIN_DISTINCT_AIRCRAFT = 50


async def _check_available_history() -> tuple[bool, str]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        span = await session.execute(select(func.min(StateVector.ts), func.max(StateVector.ts)))
        min_ts, max_ts = span.one()
        if min_ts is None or max_ts is None:
            return False, "state_vectors is empty - no ADS-B history has been ingested yet"

        hours = (max_ts - min_ts).total_seconds() / 3600
        aircraft_count = await session.execute(
            select(func.count(func.distinct(StateVector.icao24)))
        )
        n_aircraft = aircraft_count.scalar_one()

        if hours < MIN_HISTORY_HOURS or n_aircraft < MIN_DISTINCT_AIRCRAFT:
            return False, (
                f"only {hours:.1f}h of history across {n_aircraft} aircraft - need at least "
                f"{MIN_HISTORY_HOURS}h across {MIN_DISTINCT_AIRCRAFT} aircraft"
            )
        return True, f"{hours:.1f}h of history across {n_aircraft} aircraft"


def train() -> None:
    try:
        ready, detail = asyncio.run(_check_available_history())
    except Exception as exc:
        logger.warning("m1_training_skipped", reason="database_unreachable", detail=str(exc))
        return

    if not ready:
        logger.warning("m1_training_skipped", reason="insufficient_historical_data", detail=detail)
        return

    # TODO(Stage 4): build ml/datasets/trajectory.py (windowing over
    # state_vectors / the Parquet archive via features/trajectory.py), the
    # constant-velocity dead-reckoning baseline, and the GRU model -
    # mirroring train_eta.py's shape.
    logger.info("m1_training_not_yet_implemented", available_history=detail)


if __name__ == "__main__":
    configure_logging()
    train()
