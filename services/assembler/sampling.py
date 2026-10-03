"""Adaptive sampling for the assembler's volume-sensitive sinks: the live WS
fanout (bandwidth to every connected browser) and the Parquet cold-storage
sink (long-term storage volume, and redundant rows in ML training data).

Deliberately NOT applied to `adsb.raw`/`adsb.enriched` (the Kafka log) or the
direct Timescale write in `services/ingest` - those stay the complete,
unthinned record, consistent with "the event log is meant to be the full,
at-least-once replay record" (see ingest/main.py's publish_batch docstring).
Sampling only thins what gets broadcast live and what gets archived to
Parquet.

Every report is sampled during ground/climb/descent/approach - a
maneuvering aircraft is exactly when position resolution matters most, both
for the live map and for trajectory-model training data. In steady cruise,
where a track is close to a straight line for minutes at a time, reports are
thinned to one every CRUISE_SAMPLE_INTERVAL_SECONDS: fewer points carrying
much the same information.
"""

from __future__ import annotations

import datetime as dt

from services.assembler.track_state import Phase

CRUISE_SAMPLE_INTERVAL_SECONDS = 15.0


def should_sample(
    phase: Phase, last_sampled_ts: dt.datetime | None, ts: dt.datetime
) -> bool:
    """Whether this tick should go to the live fanout / Parquet sinks.

    `last_sampled_ts` is the ts of this aircraft's last *sampled* report
    (None if it has never been sampled, e.g. first-ever report) - not its
    last report overall, so the interval is measured between kept points.
    """
    if phase != Phase.CRUISE:
        return True
    if last_sampled_ts is None:
        return True
    return (ts - last_sampled_ts).total_seconds() >= CRUISE_SAMPLE_INTERVAL_SECONDS
