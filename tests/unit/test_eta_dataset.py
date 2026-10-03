"""Unit tests for ml/datasets/eta.py's pure dataset-shaping logic - no BTS
download involved, all synthetic fixture rows.
"""

from __future__ import annotations

import pandas as pd

from ml.datasets.eta import TaxiInLookup, add_dest_congestion, exclude_non_arrivals


def test_exclude_non_arrivals_drops_cancelled_diverted_and_missing_delay():
    df = pd.DataFrame(
        [
            {"cancelled": 0.0, "diverted": 0.0, "arr_delay_min": 5.0},
            {"cancelled": 1.0, "diverted": 0.0, "arr_delay_min": None},
            {"cancelled": 0.0, "diverted": 1.0, "arr_delay_min": None},
            {"cancelled": 0.0, "diverted": 0.0, "arr_delay_min": None},
        ]
    )
    kept = exclude_non_arrivals(df)
    assert len(kept) == 1
    assert kept.iloc[0]["arr_delay_min"] == 5.0


def test_dest_congestion_counts_other_flights_in_same_30min_block():
    df = pd.DataFrame(
        [
            {"flight_date": "2024-01-08", "dest": "OMA", "crs_arr_hhmm": "1135"},
            {"flight_date": "2024-01-08", "dest": "OMA", "crs_arr_hhmm": "1140"},  # same block
            {"flight_date": "2024-01-08", "dest": "OMA", "crs_arr_hhmm": "1215"},  # different block
            {"flight_date": "2024-01-08", "dest": "JFK", "crs_arr_hhmm": "1137"},  # different dest
            {"flight_date": "2024-01-09", "dest": "OMA", "crs_arr_hhmm": "1136"},  # different day
        ]
    )
    out = add_dest_congestion(df)
    assert out.loc[0, "dest_sched_arrivals_30min"] == 1  # row 1 shares its block
    assert out.loc[1, "dest_sched_arrivals_30min"] == 1  # row 0 shares its block
    assert out.loc[2, "dest_sched_arrivals_30min"] == 0
    assert out.loc[3, "dest_sched_arrivals_30min"] == 0
    assert out.loc[4, "dest_sched_arrivals_30min"] == 0


def test_taxi_in_lookup_prefers_pair_then_dest_then_global():
    train = pd.DataFrame(
        [
            {"dest": "OMA", "carrier": "9E", "taxi_in_min": 4.0},
            {"dest": "OMA", "carrier": "9E", "taxi_in_min": 6.0},
            {"dest": "OMA", "carrier": "WN", "taxi_in_min": 20.0},
            {"dest": "JFK", "carrier": "AA", "taxi_in_min": 15.0},
        ]
    )
    lookup = TaxiInLookup(train)

    assert lookup.lookup("OMA", "9E") == 5.0  # median of [4, 6]
    assert lookup.lookup("OMA", "DL") == 6.0  # falls back to dest-only median of [4, 6, 20]
    assert lookup.lookup("ZZZ", "DL") is not None  # falls back to global median


def test_taxi_in_lookup_global_fallback_is_none_when_training_data_is_empty():
    lookup = TaxiInLookup(pd.DataFrame(columns=["dest", "carrier", "taxi_in_min"]))
    assert lookup.lookup("OMA", "9E") is None
