"""Loader for ADS-B Exchange's free `readsb-hist` historical sample data —
the real, publicly available, no-key-needed source of historical aircraft
*trajectory* data M1 (trajectory forecasting) and M4's learned anomaly layer
train against. BTS (ml/data/loaders/bts.py) has no position data at all — it's
scheduled-flight records, not tracks — so it cannot serve these two models.

ADS-B Exchange publishes global airborne-traffic snapshots every 5 seconds,
and makes the complete data for the 1st of each month free of charge with no
registration (https://www.adsbexchange.com/data-products/sample-data/). One
snapshot is one JSON object per timestamp at
`https://samples.adsbexchange.com/readsb-hist/{yyyy}/{mm}/{dd}/{HHMMSS}Z.json.gz`
(despite the `.gz` suffix, the server returns it already decompressed —
confirmed empirically, not assumed).

This loader deliberately does NOT download a full day (17,280 snapshots,
tens of GB) — see docs/adr/0004-adsbx-historical-sample-window.md for the
reasoning and the exact window this project trains on. `load_range` is
general-purpose (any start/end/interval); `DEFAULT_TRAINING_WINDOW` is the
specific window the ADR settled on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import httpx
import pandas as pd

from services.common.telemetry import get_logger

logger = get_logger(__name__)

BASE_URL = "https://samples.adsbexchange.com/readsb-hist"
DEFAULT_CACHE_DIR = Path("data/raw/adsbx_hist")

# (min_lat, max_lat, min_lon, max_lon) - same CONUS scope as live ingest
# (services.common.config.Settings.conus_bbox), duplicated here rather than
# imported so this loader has zero dependency on the running-service config
# module and can be used standalone in a notebook/script context.
CONUS_BBOX = (24.5, 49.5, -125.0, -66.5)

# See docs/adr/0004 - free data is only guaranteed for the 1st of a month;
# 2024-01-01 14:00-15:00 UTC (~9am-10am US Eastern) is within US daytime
# traffic for the whole CONUS width, at the data's native 5s cadence.
DEFAULT_TRAINING_WINDOW = (
    dt.datetime(2024, 1, 1, 14, 0, 0, tzinfo=dt.UTC),
    dt.datetime(2024, 1, 1, 15, 0, 0, tzinfo=dt.UTC),
)


def _snapshot_url(ts: dt.datetime) -> str:
    return f"{BASE_URL}/{ts:%Y/%m/%d}/{ts:%H%M%S}Z.json.gz"


def _snapshot_cache_path(ts: dt.datetime, cache_dir: Path) -> Path:
    return cache_dir / f"{ts:%Y%m%d}" / f"{ts:%H%M%S}Z.json"


def _rows_from_snapshot(raw_text: str) -> list[dict]:
    data = json.loads(raw_text)
    snapshot_ts = dt.datetime.fromtimestamp(data["now"], tz=dt.UTC)
    min_lat, max_lat, min_lon, max_lon = CONUS_BBOX

    rows = []
    for ac in data.get("aircraft", []):
        hex_id = ac.get("hex", "")
        # readsb prefixes non-ICAO addresses (TIS-B / "other" relayed
        # aircraft, e.g. "~279241") with "~" - not a real 24-bit ICAO
        # address, so not a valid icao24 identity for this project's schema
        # (services.common.schemas.aircraft.StateVectorIn requires a plain
        # hex string). Confirmed empirically: this is a real, recurring
        # value in the live data, not a rare edge case.
        if not hex_id or not all(c in "0123456789abcdefABCDEF" for c in hex_id):
            continue

        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None:
            continue
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue

        alt_baro = ac.get("alt_baro")
        on_ground = alt_baro == "ground"
        callsign = (ac.get("flight") or "").strip() or None

        rows.append(
            {
                "icao24": hex_id,
                "ts": snapshot_ts,
                "lat": lat,
                "lon": lon,
                "baro_alt_ft": None if on_ground else alt_baro,
                "velocity_kt": ac.get("gs"),
                "heading_deg": ac.get("track"),
                # baro_rate is preferred (matches the live ingest pipeline's
                # own field); geom_rate is the fallback some records use
                # instead when baro_rate isn't reported.
                "vert_rate_fpm": ac.get("baro_rate", ac.get("geom_rate")),
                "on_ground": on_ground,
                "callsign": callsign,
                "registration": ac.get("r"),
                "type_code": ac.get("t"),
            }
        )
    return rows


async def _fetch_one(
    client: httpx.AsyncClient, ts: dt.datetime, cache_dir: Path, sem: asyncio.Semaphore
) -> list[dict]:
    cache_path = _snapshot_cache_path(ts, cache_dir)
    async with sem:
        if cache_path.exists():
            raw_text = cache_path.read_text(encoding="utf-8")
        else:
            try:
                resp = await client.get(_snapshot_url(ts), timeout=30)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("adsbx_snapshot_fetch_failed", ts=ts.isoformat(), error=str(exc))
                return []
            raw_text = resp.text
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(raw_text, encoding="utf-8")

    try:
        return _rows_from_snapshot(raw_text)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("adsbx_snapshot_parse_failed", ts=ts.isoformat(), error=str(exc))
        return []


async def _load_range_async(
    start: dt.datetime, end: dt.datetime, interval_s: int, cache_dir: Path, max_concurrency: int
) -> pd.DataFrame:
    timestamps = []
    t = start
    while t <= end:
        timestamps.append(t)
        t += dt.timedelta(seconds=interval_s)

    sem = asyncio.Semaphore(max_concurrency)
    logger.info(
        "adsbx_load_range_start",
        start=start.isoformat(),
        end=end.isoformat(),
        count=len(timestamps),
    )

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_fetch_one(client, ts, cache_dir, sem) for ts in timestamps]
        )

    all_rows = [row for rows in results for row in rows]
    logger.info("adsbx_load_range_complete", snapshots=len(timestamps), rows=len(all_rows))
    return pd.DataFrame(all_rows)


def load_range(
    start: dt.datetime,
    end: dt.datetime,
    interval_s: int = 5,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_concurrency: int = 16,
) -> pd.DataFrame:
    """Downloads (using a per-snapshot on-disk cache) and parses every
    `readsb-hist` snapshot in `[start, end]` at `interval_s` spacing,
    filtered to CONUS, into one flat DataFrame of position reports — the
    same normalized shape `services.common.schemas.aircraft.StateVectorIn`
    uses for live data, so downstream windowing code
    (ml/datasets/trajectory.py) doesn't need two code paths for live vs.
    historical data.
    """
    return asyncio.run(_load_range_async(start, end, interval_s, cache_dir, max_concurrency))


def load_default_training_window(
    cache_dir: Path = DEFAULT_CACHE_DIR, max_concurrency: int = 16
) -> pd.DataFrame:
    start, end = DEFAULT_TRAINING_WINDOW
    return load_range(
        start, end, interval_s=5, cache_dir=cache_dir, max_concurrency=max_concurrency
    )
