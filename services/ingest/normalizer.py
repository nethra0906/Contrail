"""Source-specific raw-JSON -> StateVectorIn normalization, pulled out of the
source adapters so it can be unit-tested against recorded fixtures without a
network call. Each function is pure: raw dict in, StateVectorIn or None out.
"""

from __future__ import annotations

import datetime as dt

from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import get_logger

logger = get_logger(__name__)


def normalize_adsb_lol(raw: dict, *, now: dt.datetime | None = None) -> StateVectorIn | None:
    """Normalize one record from adsb.lol's `/v2/lat/.../lon/.../dist/...`
    response. `now` is injectable for deterministic tests; defaults to the
    real clock in production.
    """
    now = now or dt.datetime.now(dt.UTC)

    hexcode = raw.get("hex")
    lat, lon = raw.get("lat"), raw.get("lon")
    if not hexcode or lat is None or lon is None:
        return None

    # adsb.lol prefixes non-Mode-S-derived tracks (TIS-B relays, MLAT-only
    # contacts without a real transponder ICAO address) with "~". These
    # aren't valid 24-bit ICAO addresses and aren't reliable enough for the
    # trajectory/flight-matching pipeline (no stable identity across
    # sightings), so we deliberately exclude them rather than mangling a
    # fake icao24 into the primary key space. Discovered by running this
    # against live traffic during development.
    if hexcode.startswith("~"):
        return None

    alt_baro = raw.get("alt_baro")
    on_ground = alt_baro == "ground"
    baro_alt_ft = None if on_ground or alt_baro is None else _safe_float(alt_baro)

    seen_pos_s = raw.get("seen_pos", 0.0) or 0.0
    ts = now - dt.timedelta(seconds=float(seen_pos_s))

    try:
        return StateVectorIn(
            icao24=hexcode,
            ts=ts,
            lat=float(lat),
            lon=float(lon),
            baro_alt_ft=baro_alt_ft,
            geo_alt_ft=_safe_float(raw.get("alt_geom")),
            velocity_kt=_safe_float(raw.get("gs")),
            heading_deg=_safe_float(raw.get("track")),
            vert_rate_fpm=_safe_float(raw.get("baro_rate")),
            on_ground=on_ground,
            squawk=raw.get("squawk"),
            source="adsb_lol",
        )
    except Exception:  # noqa: BLE001 - a malformed record must not kill the batch
        logger.warning("normalize_failed", raw_hex=hexcode)
        return None


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dedupe_latest(records: list[StateVectorIn]) -> list[StateVectorIn]:
    """When the same icao24 appears more than once in a batch (overlapping
    hub-tile circles), keep only the most recent report - this is what makes
    the (icao24, ts) upsert idempotent even when the same physical position
    report arrives twice in one poll.
    """
    latest: dict[str, StateVectorIn] = {}
    for r in records:
        existing = latest.get(r.icao24)
        if existing is None or r.ts > existing.ts:
            latest[r.icao24] = r
    return list(latest.values())
