"""Normalizer tests against a recorded fixture - a real response captured
from `GET https://api.adsb.lol/v2/lat/40.7/lon/-74.0/dist/100` on 2026-09-17,
verified live during development. Using a real recorded payload (rather than
a hand-written one) catches shape assumptions a hand-rolled fixture would
quietly satisfy by construction.
"""

from __future__ import annotations

import datetime as dt

from services.ingest.normalizer import dedupe_latest, normalize_adsb_lol

FIXED_NOW = dt.datetime(2026, 9, 17, 12, 0, 0, tzinfo=dt.UTC)

# Real record shape captured from adsb.lol on 2026-09-17.
REAL_AIRBORNE_RECORD = {
    "hex": "abf375",
    "type": "adsb_icao",
    "flight": "SWA1624 ",
    "r": "N8696E",
    "t": "B738",
    "alt_baro": 6975,
    "alt_geom": 7550,
    "gs": 294.6,
    "track": 93.70,
    "baro_rate": -1088,
    "squawk": "1431",
    "emergency": "none",
    "lat": 40.071887,
    "lon": -75.530819,
    "seen_pos": 0.040,
    "messages": 141290,
    "seen": 0.0,
    "rssi": -11.3,
}

REAL_GROUND_RECORD = {
    "hex": "a22a73",
    "flight": "N239FG  ",
    "r": "N239FG",
    "t": "C172",
    "alt_baro": "ground",
    "gs": 5.0,
    "track": 78.47,
    "lat": 40.399382,
    "lon": -75.221891,
    "seen_pos": 1.2,
}

MALFORMED_MISSING_POSITION = {"hex": "deadbe", "flight": "GHOST1"}


def test_normalize_airborne_record():
    sv = normalize_adsb_lol(REAL_AIRBORNE_RECORD, now=FIXED_NOW)
    assert sv is not None
    assert sv.icao24 == "abf375"
    assert sv.lat == 40.071887
    assert sv.lon == -75.530819
    assert sv.baro_alt_ft == 6975
    assert sv.velocity_kt == 294.6
    assert sv.vert_rate_fpm == -1088
    assert sv.on_ground is False
    assert sv.source == "adsb_lol"
    # ts is now - seen_pos
    assert sv.ts == FIXED_NOW - dt.timedelta(seconds=0.040)


def test_normalize_ground_record_has_no_altitude():
    sv = normalize_adsb_lol(REAL_GROUND_RECORD, now=FIXED_NOW)
    assert sv is not None
    assert sv.on_ground is True
    assert sv.baro_alt_ft is None


def test_normalize_missing_position_returns_none():
    assert normalize_adsb_lol(MALFORMED_MISSING_POSITION, now=FIXED_NOW) is None


def test_normalize_missing_hex_returns_none():
    record = dict(REAL_AIRBORNE_RECORD)
    del record["hex"]
    assert normalize_adsb_lol(record, now=FIXED_NOW) is None


def test_normalize_is_case_insensitive_on_hex():
    record = dict(REAL_AIRBORNE_RECORD, hex="ABF375")
    sv = normalize_adsb_lol(record, now=FIXED_NOW)
    assert sv.icao24 == "abf375"


def test_dedupe_latest_keeps_most_recent():
    older = normalize_adsb_lol(dict(REAL_AIRBORNE_RECORD, seen_pos=5.0), now=FIXED_NOW)
    newer = normalize_adsb_lol(dict(REAL_AIRBORNE_RECORD, seen_pos=0.1, lat=40.08), now=FIXED_NOW)
    deduped = dedupe_latest([older, newer])
    assert len(deduped) == 1
    assert deduped[0].lat == 40.08


def test_dedupe_latest_preserves_distinct_aircraft():
    a = normalize_adsb_lol(REAL_AIRBORNE_RECORD, now=FIXED_NOW)
    b = normalize_adsb_lol(REAL_GROUND_RECORD, now=FIXED_NOW)
    deduped = dedupe_latest([a, b])
    assert {r.icao24 for r in deduped} == {"abf375", "a22a73"}
