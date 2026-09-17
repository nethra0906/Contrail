"""Binary WebSocket wire protocol for the live aircraft feed.

Why binary at all: JSON for 10k+ aircraft at 1-2Hz is a real bandwidth and
parse-cost problem in the browser. A fixed-size binary record lets the
frontend read straight into typed arrays without a JSON.parse pass over
thousands of objects every tick.

Frame layout (little-endian, matches struct format strings below):

    Header:  u8 version | u8 frame_type | u32 ts_delta_ms | u16 record_count
    Record:  u32 icao24_int | i32 lat_e7 | i32 lon_e7 | u16 alt_ft_div8
             | u16 heading_deci | i16 vert_rate_div8 | u16 velocity_kt | u8 flags

icao24 is a 24-bit hex string in the domain model; on the wire it's packed
into a u32 (fits with room to spare) via int(icao24, 16). Lat/lon are
fixed-point at 1e-7 degrees (~1.1 cm resolution, matches ADS-B's own
precision). Altitude is stored as feet/8 in a u16 (range: 0-524,280 ft,
plenty). flags bit 0 = on_ground.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PROTOCOL_VERSION = 1

FRAME_TYPE_FULL = 0  # full snapshot - sent on subscribe / reconnect-after-gap
FRAME_TYPE_DELTA = 1  # incremental update

_HEADER_FMT = "<BBIH"  # version, frame_type, ts_delta_ms, record_count
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

_RECORD_FMT = (
    "<IiiHHhHB"  # icao24, lat_e7, lon_e7, alt_div8, heading_deci, vrate_div8, gs_kt, flags
)
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)

FLAG_ON_GROUND = 0b0000_0001


@dataclass(frozen=True)
class AircraftRecord:
    icao24: str
    lat: float
    lon: float
    alt_ft: float | None
    heading_deg: float | None
    vert_rate_fpm: float | None
    velocity_kt: float | None
    on_ground: bool


def _clamp_u16(v: float) -> int:
    return max(0, min(65535, round(v)))


def _clamp_i16(v: float) -> int:
    return max(-32768, min(32767, round(v)))


def encode_frame(records: list[AircraftRecord], frame_type: int, ts_delta_ms: int) -> bytes:
    if frame_type not in (FRAME_TYPE_FULL, FRAME_TYPE_DELTA):
        raise ValueError(f"unknown frame_type {frame_type}")
    if not (0 <= ts_delta_ms <= 0xFFFFFFFF):
        raise ValueError("ts_delta_ms out of range for u32")

    header = struct.pack(_HEADER_FMT, PROTOCOL_VERSION, frame_type, ts_delta_ms, len(records))

    body = bytearray()
    for r in records:
        flags = FLAG_ON_GROUND if r.on_ground else 0
        body += struct.pack(
            _RECORD_FMT,
            int(r.icao24, 16) & 0xFFFFFFFF,
            round(r.lat * 1e7),
            round(r.lon * 1e7),
            _clamp_u16((r.alt_ft or 0) / 8),
            _clamp_u16((r.heading_deg or 0) * 10) % 3600,
            _clamp_i16((r.vert_rate_fpm or 0) / 8),
            _clamp_u16(r.velocity_kt or 0),
            flags,
        )
    return bytes(header) + bytes(body)


def decode_frame(data: bytes) -> tuple[int, int, int, list[AircraftRecord]]:
    """Returns (version, frame_type, ts_delta_ms, records)."""
    if len(data) < _HEADER_SIZE:
        raise ValueError("frame shorter than header")

    version, frame_type, ts_delta_ms, count = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
    expected_len = _HEADER_SIZE + count * _RECORD_SIZE
    if len(data) != expected_len:
        raise ValueError(f"frame length {len(data)} != expected {expected_len} for {count} records")

    records = []
    offset = _HEADER_SIZE
    for _ in range(count):
        chunk = data[offset : offset + _RECORD_SIZE]
        icao24_int, lat_e7, lon_e7, alt_div8, heading_deci, vrate_div8, gs_kt, flags = (
            struct.unpack(_RECORD_FMT, chunk)
        )
        records.append(
            AircraftRecord(
                icao24=format(icao24_int, "06x"),
                lat=lat_e7 / 1e7,
                lon=lon_e7 / 1e7,
                alt_ft=alt_div8 * 8,
                heading_deg=heading_deci / 10,
                vert_rate_fpm=vrate_div8 * 8,
                velocity_kt=float(gs_kt),
                on_ground=bool(flags & FLAG_ON_GROUND),
            )
        )
        offset += _RECORD_SIZE

    return version, frame_type, ts_delta_ms, records
