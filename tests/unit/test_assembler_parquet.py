"""Unit tests for the Parquet sink's buffering logic and row schema
(services/assembler/sinks/parquet.py). No MinIO/S3 involved - `flush_rows`
itself needs a real object store and belongs in an integration test, but the
buffer threshold logic and the row-to-Arrow-table schema mapping are pure
and worth covering here, especially the schema: a silent mismatch there
would only surface the next time someone tries to read the Parquet files
back.
"""

from __future__ import annotations

import datetime as dt

import pyarrow as pa

from services.assembler.sinks.parquet import (
    FLUSH_RECORD_THRESHOLD,
    ParquetBuffer,
    rows_to_table,
)
from services.common.schemas.aircraft import StateVectorIn


def _sv(icao24: str = "abc123") -> StateVectorIn:
    return StateVectorIn(
        icao24=icao24,
        ts=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        lat=40.0,
        lon=-74.0,
        baro_alt_ft=35000,
        velocity_kt=450,
        heading_deg=90,
        vert_rate_fpm=0,
        on_ground=False,
        source="test",
    )


def test_buffer_does_not_flush_when_empty():
    buf = ParquetBuffer()
    assert not buf.should_flush()
    assert buf.take() == []


def test_buffer_flushes_at_record_threshold():
    buf = ParquetBuffer()
    for i in range(FLUSH_RECORD_THRESHOLD - 1):
        buf.add(_sv(f"{i:06x}"), "852a1073fffffff")
    assert not buf.should_flush()

    buf.add(_sv("ffffff"), "852a1073fffffff")
    assert buf.should_flush()
    assert len(buf) == FLUSH_RECORD_THRESHOLD


def test_take_empties_the_buffer_and_resets_flush_clock():
    buf = ParquetBuffer()
    buf.add(_sv(), "852a1073fffffff")

    rows = buf.take()
    assert len(rows) == 1
    assert len(buf) == 0
    assert buf.take() == []


def test_add_attaches_h3_cell_to_the_row():
    buf = ParquetBuffer()
    buf.add(_sv(), "852a1073fffffff")
    [row] = buf.take()
    assert row["h3_r5"] == "852a1073fffffff"
    assert row["icao24"] == "abc123"


def test_rows_to_table_matches_the_declared_schema():
    buf = ParquetBuffer()
    buf.add(_sv(), "852a1073fffffff")
    buf.add(_sv("def456"), None)

    table = rows_to_table(buf.take())

    assert isinstance(table, pa.Table)
    assert table.num_rows == 2
    assert table.column("icao24").to_pylist() == ["abc123", "def456"]
    assert table.column("h3_r5").to_pylist() == ["852a1073fffffff", None]
