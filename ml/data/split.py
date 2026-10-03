"""The single chronological train/val/test split used by every model (M1,
M2, M3). Execution rule 4 in the master spec is absolute: "Never use a
random train/test split. Chronological only. There is a guard test." -
every training script imports `chronological_split` from here rather than
rolling its own `sample(frac=...)` or `train_test_split(shuffle=True)`, so
there is exactly one place that rule can be violated, and exactly one place
the guard test needs to check.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ChronologicalSplit:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    train_end: dt.datetime
    val_end: dt.datetime


def chronological_split(
    df: pd.DataFrame,
    date_col: str,
    train_frac: float = 0.70,
    val_frac: float = 0.15,
) -> ChronologicalSplit:
    """Splits `df` into train/val/test by `date_col`, in time order - never by
    randomly sampled rows. The fractions are by *time span covered*, not row
    count: `train_end` and `val_end` are computed from the date range, then
    rows fall into whichever bucket their timestamp lands in. That matters
    because row density can vary over the window (e.g. more flights on
    weekdays) - splitting by elapsed time, not row count, is what keeps test
    genuinely "the future relative to train" rather than merely "some rows
    chosen so each split title has ~ the right size."
    """
    if not (0 < train_frac < 1) or not (0 < val_frac < 1) or train_frac + val_frac >= 1:
        raise ValueError("train_frac and val_frac must be in (0, 1) and sum to < 1")

    ts = pd.to_datetime(df[date_col])
    start, end = ts.min(), ts.max()
    span = end - start
    train_end = start + span * train_frac
    val_end = start + span * (train_frac + val_frac)

    train = df.loc[ts < train_end]
    val = df.loc[(ts >= train_end) & (ts < val_end)]
    test = df.loc[ts >= val_end]

    return ChronologicalSplit(
        train=train, val=val, test=test, train_end=train_end, val_end=val_end
    )
