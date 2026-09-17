"""Seed airports + runways from OurAirports' public CSV exports, filtered to
the CONUS bounding box the ingest service actually covers. Idempotent: safe
to re-run, upserts on primary key.

Usage:
    python -m scripts.seed_reference_data
"""

from __future__ import annotations

import asyncio
import csv
import io
import sys

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from services.common.config import get_settings
from services.common.db import get_sessionmaker
from services.common.models import Airport, Runway
from services.common.telemetry import configure_logging, get_logger

OURAIRPORTS_AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OURAIRPORTS_RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"

logger = get_logger(__name__)


async def fetch_csv(url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def _f(row: dict, key: str) -> float | None:
    v = row.get(key, "")
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


async def seed() -> None:
    settings = get_settings()
    min_lat, max_lat, min_lon, max_lon = settings.conus_bbox

    logger.info("fetching_ourairports_data")
    airport_rows = await fetch_csv(OURAIRPORTS_AIRPORTS_URL)

    conus_airports: list[dict] = []
    icao_set: set[str] = set()
    for row in airport_rows:
        icao = (
            (row.get("icao_code") or row.get("gps_code") or row.get("ident") or "").strip().upper()
        )
        if not icao or len(icao) != 4:
            continue
        if row.get("type") not in ("large_airport", "medium_airport"):
            continue
        lat, lon = _f(row, "latitude_deg"), _f(row, "longitude_deg")
        if lat is None or lon is None:
            continue
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        if row.get("iso_country") != "US":
            continue
        conus_airports.append(
            {
                "icao": icao,
                "iata": (row.get("iata_code") or None),
                "name": row.get("name", "")[:128],
                "city": (row.get("municipality") or None),
                "country": "US",
                "lat": lat,
                "lon": lon,
                "elevation_ft": _f(row, "elevation_ft"),
                "tz": None,
                "hub_rank": None,
            }
        )
        icao_set.add(icao)

    logger.info("filtered_airports", count=len(conus_airports))

    logger.info("fetching_runways")
    runway_rows = await fetch_csv(OURAIRPORTS_RUNWAYS_URL)
    conus_runways: list[dict] = []
    for row in runway_rows:
        airport_icao = (row.get("airport_ident") or "").strip().upper()
        if airport_icao not in icao_set:
            continue
        le_ident = row.get("le_ident")
        he_ident = row.get("he_ident")
        length_ft = _f(row, "length_ft")
        for ident, lat_key, lon_key, hdg_key in (
            (le_ident, "le_latitude_deg", "le_longitude_deg", "le_heading_degT"),
            (he_ident, "he_latitude_deg", "he_longitude_deg", "he_heading_degT"),
        ):
            if not ident:
                continue
            conus_runways.append(
                {
                    "airport_icao": airport_icao,
                    "ident": ident,
                    "true_heading_deg": _f(row, hdg_key),
                    "length_ft": length_ft,
                    "lat": _f(row, lat_key),
                    "lon": _f(row, lon_key),
                }
            )

    logger.info("filtered_runways", count=len(conus_runways))

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if conus_airports:
            stmt = insert(Airport).values(conus_airports)
            stmt = stmt.on_conflict_do_update(
                index_elements=["icao"],
                set_={c: stmt.excluded[c] for c in conus_airports[0] if c != "icao"},
            )
            await session.execute(stmt)

        # runways have no natural unique key from the source; clear and reinsert
        # only for airports we just seeded, keeping this idempotent.
        if icao_set:
            await session.execute(
                text("DELETE FROM runways WHERE airport_icao = ANY(:icaos)"),
                {"icaos": list(icao_set)},
            )
        if conus_runways:
            await session.execute(insert(Runway).values(conus_runways))

        await session.commit()

    logger.info("seed_complete", airports=len(conus_airports), runways=len(conus_runways))


if __name__ == "__main__":
    configure_logging()
    try:
        asyncio.run(seed())
    except Exception:
        logger.exception("seed_failed")
        sys.exit(1)
