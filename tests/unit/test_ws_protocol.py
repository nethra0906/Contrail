"""Round-trip property tests for the binary WS protocol - the wire format
the live map depends on for 60fps rendering of 10k+ aircraft. See
services/api/ws/protocol.py for the frame layout.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.api.ws.protocol import (
    FRAME_TYPE_DELTA,
    FRAME_TYPE_FULL,
    AircraftRecord,
    decode_frame,
    encode_frame,
)

icao24_strategy = st.integers(min_value=0, max_value=0xFFFFFF).map(lambda i: format(i, "06x"))

record_strategy = st.builds(
    AircraftRecord,
    icao24=icao24_strategy,
    lat=st.floats(min_value=-90, max_value=90, allow_nan=False),
    lon=st.floats(min_value=-180, max_value=180, allow_nan=False),
    alt_ft=st.one_of(st.none(), st.floats(min_value=0, max_value=50000, allow_nan=False)),
    heading_deg=st.one_of(st.none(), st.floats(min_value=0, max_value=359.9, allow_nan=False)),
    vert_rate_fpm=st.one_of(st.none(), st.floats(min_value=-6000, max_value=6000, allow_nan=False)),
    velocity_kt=st.one_of(st.none(), st.floats(min_value=0, max_value=600, allow_nan=False)),
    on_ground=st.booleans(),
)


@given(st.lists(record_strategy, max_size=50), st.integers(min_value=0, max_value=100_000))
@settings(max_examples=200)
def test_encode_decode_roundtrip(records: list[AircraftRecord], ts_delta_ms: int):
    frame = encode_frame(records, FRAME_TYPE_DELTA, ts_delta_ms)
    version, frame_type, decoded_ts, decoded = decode_frame(frame)

    assert version == 1
    assert frame_type == FRAME_TYPE_DELTA
    assert decoded_ts == ts_delta_ms
    assert len(decoded) == len(records)

    for original, rt in zip(records, decoded, strict=True):
        assert rt.icao24 == original.icao24
        # Fixed-point lat/lon: 1e-7 deg resolution.
        assert abs(rt.lat - original.lat) < 1e-6
        assert abs(rt.lon - original.lon) < 1e-6
        assert rt.on_ground == original.on_ground
        # Altitude quantized to 8ft buckets.
        if original.alt_ft is not None:
            assert abs(rt.alt_ft - original.alt_ft) <= 8
        # Heading quantized to 0.1deg buckets.
        if original.heading_deg is not None:
            assert abs(rt.heading_deg - original.heading_deg) <= 0.1


def test_empty_frame_roundtrips():
    frame = encode_frame([], FRAME_TYPE_FULL, 0)
    version, frame_type, ts_delta, records = decode_frame(frame)
    assert frame_type == FRAME_TYPE_FULL
    assert records == []


def test_rejects_truncated_frame():
    frame = encode_frame(
        [
            AircraftRecord(
                icao24="abc123",
                lat=40.0,
                lon=-74.0,
                alt_ft=10000,
                heading_deg=90,
                vert_rate_fpm=0,
                velocity_kt=250,
                on_ground=False,
            )
        ],
        FRAME_TYPE_DELTA,
        0,
    )
    with pytest.raises(ValueError, match="frame length"):
        decode_frame(frame[:-1])  # truncate by one byte


def test_rejects_unknown_frame_type():
    with pytest.raises(ValueError, match="frame_type"):
        encode_frame([], 99, 0)


def test_on_ground_flag_roundtrips_correctly():
    r = AircraftRecord(
        icao24="000001",
        lat=0,
        lon=0,
        alt_ft=None,
        heading_deg=None,
        vert_rate_fpm=None,
        velocity_kt=None,
        on_ground=True,
    )
    frame = encode_frame([r], FRAME_TYPE_FULL, 0)
    _, _, _, decoded = decode_frame(frame)
    assert decoded[0].on_ground is True
