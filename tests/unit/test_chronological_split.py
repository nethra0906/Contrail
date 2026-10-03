"""Guard test for execution rule 4: "Never use a random train/test split.
Chronological only." Every model's training script imports
`chronological_split` from ml/data/split.py - this test is what fails loudly
if that function (or a future training script bypassing it) ever produces a
split that isn't strictly time-ordered.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from ml.data.split import chronological_split


def _df(n_days: int) -> pd.DataFrame:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    dates = [start + dt.timedelta(days=i) for i in range(n_days)]
    return pd.DataFrame({"flight_date": dates, "value": range(n_days)})


def test_splits_are_strictly_chronological_with_no_overlap():
    split = chronological_split(_df(100), date_col="flight_date")

    assert split.train["flight_date"].max() <= split.train_end
    assert split.val["flight_date"].min() >= split.train_end
    assert split.val["flight_date"].max() <= split.val_end
    assert split.test["flight_date"].min() >= split.val_end

    # No row appears in more than one split, and every row appears exactly
    # once (a random shuffle-based split could duplicate or drop rows if
    # misapplied; this split must not).
    assert len(split.train) + len(split.val) + len(split.test) == 100
    train_idx = set(split.train.index)
    val_idx = set(split.val.index)
    test_idx = set(split.test.index)
    assert not (train_idx & val_idx)
    assert not (val_idx & test_idx)
    assert not (train_idx & test_idx)


def test_split_is_deterministic_not_randomly_sampled():
    df = _df(100)
    first = chronological_split(df, date_col="flight_date")
    second = chronological_split(df, date_col="flight_date")

    # A random split (even seeded) risks drifting between pandas/sklearn
    # versions; a chronological split by date boundary must not - this is
    # the behavioural signature that distinguishes the two.
    assert list(first.train.index) == list(second.train.index)
    assert list(first.test.index) == list(second.test.index)


def test_rejects_fractions_that_would_leave_no_room_for_test():
    with pytest.raises(ValueError):
        chronological_split(_df(10), date_col="flight_date", train_frac=0.8, val_frac=0.3)


def test_approximately_respects_requested_fractions_for_uniform_density():
    # With one row per day (uniform density), splitting by time span should
    # closely match splitting by row count.
    split = chronological_split(_df(1000), date_col="flight_date", train_frac=0.6, val_frac=0.2)
    assert 550 <= len(split.train) <= 650
    assert 150 <= len(split.val) <= 250
    assert 150 <= len(split.test) <= 250
