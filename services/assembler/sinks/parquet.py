"""Buffers state vectors in memory and periodically flushes them to MinIO as
Parquet files - the assembler's third sink alongside Timescale (written
directly by `services/ingest`, see that module's main.py for why the direct
write stays even with Kafka in front of it) and Redis hot state
(live_fanout.py).

Why this sink exists: `state_vectors`' raw retention is 6 hours (see
migrations/versions/0001_initial_schema.py) - past that, only the 15s/60s
continuous aggregates survive. Full-resolution history beyond that window
lives only here, which is what Stage 4's historical training loaders and any
longer-range backfill depend on.

Deliberately simple: an in-memory buffer flushed by record count or time,
written as one Parquet file per flush via pyarrow's built-in S3-compatible
filesystem (no extra S3 client dependency needed - MinIO speaks the S3 API).
A flush failure drops that batch rather than blocking the live pipeline:
acceptable because this is a secondary, catch-up-able sink, not a system of
record - Kafka's `adsb.raw` (7-day retention) and Timescale's 6-hour window
remain authoritative for anything a dropped batch would have carried.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow.fs import S3FileSystem

from services.common.config import get_settings
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import PARQUET_FLUSH_ROWS_TOTAL, PARQUET_FLUSH_TOTAL, get_logger

logger = get_logger(__name__)

FLUSH_RECORD_THRESHOLD = 5000
FLUSH_INTERVAL_SECONDS = 60.0

_SCHEMA = pa.schema(
    [
        ("icao24", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("baro_alt_ft", pa.float64()),
        ("geo_alt_ft", pa.float64()),
        ("velocity_kt", pa.float64()),
        ("heading_deg", pa.float64()),
        ("vert_rate_fpm", pa.float64()),
        ("on_ground", pa.bool_()),
        ("squawk", pa.string()),
        ("h3_r5", pa.string()),
        ("source", pa.string()),
    ]
)


class ParquetBuffer:
    """Caller-owned, per-process buffer - like AssemblerState, dropped on a
    restart (see pipeline.py's AssemblerState docstring for why that's an
    acceptable loss at this stage).
    """

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._last_flush = dt.datetime.now(dt.UTC)

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, sv: StateVectorIn, h3_cell: str | None) -> None:
        row = sv.model_dump()
        row["h3_r5"] = h3_cell
        self._rows.append(row)

    def should_flush(self) -> bool:
        if len(self._rows) >= FLUSH_RECORD_THRESHOLD:
            return True
        if not self._rows:
            return False
        elapsed = (dt.datetime.now(dt.UTC) - self._last_flush).total_seconds()
        return elapsed >= FLUSH_INTERVAL_SECONDS

    def take(self) -> list[dict]:
        rows, self._rows = self._rows, []
        self._last_flush = dt.datetime.now(dt.UTC)
        return rows


def rows_to_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=_SCHEMA)


def _filesystem() -> S3FileSystem:
    settings = get_settings()
    return S3FileSystem(
        endpoint_override=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        scheme="https" if settings.minio_secure else "http",
        allow_bucket_creation=True,
    )


def flush_rows(rows: list[dict]) -> str | None:
    """Writes one Parquet file for this batch, partitioned by UTC hour.
    Synchronous (pyarrow's S3 filesystem has no async API) - callers on the
    event loop should run this in a thread. Returns the written path, or
    None if there was nothing to write or the flush failed.
    """
    if not rows:
        return None

    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    path = (
        f"{settings.minio_bucket}/state_vectors/"
        f"dt={now:%Y-%m-%d}/hour={now:%H}/{uuid.uuid4()}.parquet"
    )

    try:
        table = rows_to_table(rows)
        fs = _filesystem()
        fs.create_dir(settings.minio_bucket)  # no-op if it already exists
        with fs.open_output_stream(path) as f:
            pq.write_table(table, f)
    except Exception:
        logger.exception("parquet_flush_failed", rows=len(rows))
        PARQUET_FLUSH_TOTAL.labels(outcome="error").inc()
        return None

    logger.info("parquet_flush_complete", rows=len(rows), path=path)
    PARQUET_FLUSH_TOTAL.labels(outcome="success").inc()
    PARQUET_FLUSH_ROWS_TOTAL.inc(len(rows))
    return path
