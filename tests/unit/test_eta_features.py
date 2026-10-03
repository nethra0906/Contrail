"""Unit tests for the single ETA feature implementation
(services/common/features/eta.py) - shared by training and (eventually)
live serving, so correctness here is load-bearing for both.
"""

from __future__ import annotations

import pytest

from services.common.features.eta import (
    DEFAULT_TAXI_IN_MIN,
    FEATURE_COLUMNS,
    compute_eta_features,
    hour_of_day_from_hhmm,
)


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("0905", 9),
        (905, 9),
        ("905", 9),
        ("2350", 23),
        ("0000", 0),
        (0, 0),
        ("1200", 12),
    ],
)
def test_hour_of_day_from_hhmm(hhmm, expected):
    assert hour_of_day_from_hhmm(hhmm) == expected


def test_compute_eta_features_happy_path():
    f = compute_eta_features(
        distance_mi=1148.0,
        sched_elapsed_min=219.0,
        dep_delay_min=-5.0,
        sched_dep_hhmm="0856",
        day_of_week=1,
        month=1,
        carrier="9E",
        origin="LGA",
        dest="OMA",
        dest_sched_arrivals_30min=3,
        historic_taxi_in_p50_min=6.0,
    )

    assert f.hour_of_day == 8
    assert f.day_of_week == 1
    assert f.dest_sched_arrivals_30min == 3
    assert f.historic_taxi_in_p50_min == 6.0
    assert f.carrier == "9E"
    # block speed: 1148 mi / (219/60 h) * 0.868976 kt/mph
    assert f.sched_block_speed_kt == pytest.approx(1148 / (219 / 60) * 0.868976, rel=1e-6)


def test_missing_historic_taxi_in_falls_back_to_default():
    f = compute_eta_features(
        distance_mi=500.0,
        sched_elapsed_min=90.0,
        dep_delay_min=0.0,
        sched_dep_hhmm="1200",
        day_of_week=3,
        month=6,
        carrier="XX",
        origin="AAA",
        dest="BBB",
        dest_sched_arrivals_30min=0,
        historic_taxi_in_p50_min=None,
    )
    assert f.historic_taxi_in_p50_min == DEFAULT_TAXI_IN_MIN


def test_rejects_non_positive_scheduled_elapsed_time():
    with pytest.raises(ValueError):
        compute_eta_features(
            distance_mi=500.0,
            sched_elapsed_min=0.0,
            dep_delay_min=0.0,
            sched_dep_hhmm="1200",
            day_of_week=3,
            month=6,
            carrier="XX",
            origin="AAA",
            dest="BBB",
            dest_sched_arrivals_30min=0,
            historic_taxi_in_p50_min=None,
        )


def test_feature_columns_cover_every_dataclass_field():
    f = compute_eta_features(
        distance_mi=500.0,
        sched_elapsed_min=90.0,
        dep_delay_min=0.0,
        sched_dep_hhmm="1200",
        day_of_week=3,
        month=6,
        carrier="XX",
        origin="AAA",
        dest="BBB",
        dest_sched_arrivals_30min=0,
        historic_taxi_in_p50_min=5.0,
    )
    for col in FEATURE_COLUMNS:
        assert hasattr(f, col)
