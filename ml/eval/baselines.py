"""Baseline predictors for M2 (ETA / arrival-delay regression) - MUST be
implemented and reported alongside the learned model, per execution rule 2:
"Write the baseline before the advanced model. Always. Report both."
"""

from __future__ import annotations

import pandas as pd


def baseline_scheduled(df: pd.DataFrame) -> pd.Series:
    """(a) "scheduled arrival time": predict zero arrival delay. The
    simplest possible ETA - the resulting error is just the true arrival
    delay's magnitude.
    """
    return pd.Series(0.0, index=df.index)


def baseline_departure_carryover(df: pd.DataFrame) -> pd.Series:
    """(b) "great-circle / groundspeed" baseline's spirit adapted to this
    v1's pre-departure feature set (see services/common/features/eta.py's
    module docstring): propagate the departure delay unchanged to arrival.
    A standard, much stronger baseline in delay-prediction literature than
    predicting zero, since most of a flight's eventual arrival delay is
    already visible once it has pushed back.
    """
    return df["dep_delay_min"]
