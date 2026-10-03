"""Loader for BTS "Reporting Carrier On-Time Performance" monthly data - the
free, no-key historical delay ground truth the master spec's M2 (ETA) and M3
(delay-propagation GNN) train against (see docs/CONTRAIL_MASTER_SPEC.md §2
and §7).

BTS publishes a predictable per-month ZIP under a documented bulk-download
path (transtats.bts.gov/PREZIP/...) - no form submission or API key needed,
just the right URL for a given (year, month). Downloads are cached to
`data/raw/bts/` (gitignored) so re-running training doesn't re-fetch ~250MB
of CSV per month.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from services.common.telemetry import get_logger

logger = get_logger(__name__)

BTS_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

DEFAULT_CACHE_DIR = Path("data/raw/bts")

# Only the columns M2/M3 actually use - the full file has >100 columns
# (diversion detail, individual delay-cause minutes, etc.) that would
# otherwise triple memory use for no benefit at this stage.
_USE_COLS = [
    "FlightDate",
    "Reporting_Airline",
    "Tail_Number",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "DepTime",
    "DepDelayMinutes",
    "CRSArrTime",
    "ArrTime",
    "ArrDelayMinutes",
    "Cancelled",
    "Diverted",
    "CRSElapsedTime",
    "ActualElapsedTime",
    "AirTime",
    "TaxiIn",
    "TaxiOut",
    "Distance",
    "DayOfWeek",
]

_DTYPES = {
    "Reporting_Airline": "category",
    "Tail_Number": "string",
    "Flight_Number_Reporting_Airline": "string",
    "Origin": "category",
    "Dest": "category",
    "CRSDepTime": "string",
    "DepTime": "string",
    "CRSArrTime": "string",
    "ArrTime": "string",
    "Cancelled": "float32",
    "Diverted": "float32",
    "DayOfWeek": "int8",
}

_RENAME = {
    "FlightDate": "flight_date",
    "Reporting_Airline": "carrier",
    "Tail_Number": "tail_number",
    "Flight_Number_Reporting_Airline": "flight_number",
    "Origin": "origin",
    "Dest": "dest",
    "CRSDepTime": "crs_dep_hhmm",
    "DepTime": "dep_hhmm",
    "DepDelayMinutes": "dep_delay_min",
    "CRSArrTime": "crs_arr_hhmm",
    "ArrTime": "arr_hhmm",
    "ArrDelayMinutes": "arr_delay_min",
    "Cancelled": "cancelled",
    "Diverted": "diverted",
    "CRSElapsedTime": "sched_elapsed_min",
    "ActualElapsedTime": "actual_elapsed_min",
    "AirTime": "air_time_min",
    "TaxiIn": "taxi_in_min",
    "TaxiOut": "taxi_out_min",
    "Distance": "distance_mi",
    "DayOfWeek": "day_of_week",
}


def download_month(year: int, month: int, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    """Downloads one month's ZIP if not already cached. Returns the local
    ZIP path. Raises requests.HTTPError if BTS doesn't have that month yet
    (e.g. asking for data from the future) - callers should let that
    propagate rather than silently falling back to synthetic data, per rule
    10 of the master spec.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"ontime_{year}_{month:02d}.zip"
    if zip_path.exists():
        logger.info("bts_cache_hit", year=year, month=month, path=str(zip_path))
        return zip_path

    url = BTS_URL_TEMPLATE.format(year=year, month=month)
    logger.info("bts_download_start", year=year, month=month, url=url)
    resp = httpx.get(url, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    zip_path.write_bytes(resp.content)
    logger.info("bts_download_complete", year=year, month=month, bytes=len(resp.content))
    return zip_path


def _read_zip(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"no CSV found in {zip_path}")
        with zf.open(csv_names[0]) as f:
            return pd.read_csv(
                io.BytesIO(f.read()),
                usecols=_USE_COLS,
                dtype=_DTYPES,
                parse_dates=["FlightDate"],
            )


def load_month(year: int, month: int, cache_dir: Path = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """Downloads (if needed) and parses one month into the normalized schema
    every M2/M3 consumer shares - see `_RENAME` for the column mapping.
    Cancelled and diverted flights are kept (rather than dropped here) since
    M3's delay-propagation graph needs cancellation counts per airport; M2's
    dataset builder is responsible for excluding them from ETA training rows
    (an arrival delay is undefined for a flight that never arrived).
    """
    zip_path = download_month(year, month, cache_dir)
    df = _read_zip(zip_path).rename(columns=_RENAME)
    df["year"] = year
    df["month"] = month
    return df


def load_months(
    year_months: list[tuple[int, int]], cache_dir: Path = DEFAULT_CACHE_DIR
) -> pd.DataFrame:
    """Loads and concatenates several months, sorted chronologically by
    flight_date - callers doing a chronological split depend on this being
    in time order, not file-load order.
    """
    if not year_months:
        raise ValueError("year_months must be non-empty")
    frames = [load_month(y, m, cache_dir) for y, m in year_months]
    return pd.concat(frames, ignore_index=True).sort_values("flight_date").reset_index(drop=True)
