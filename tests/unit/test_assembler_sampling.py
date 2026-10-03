"""Unit tests for adaptive sampling (services/assembler/sampling.py)."""

from __future__ import annotations

import datetime as dt

from services.assembler.sampling import CRUISE_SAMPLE_INTERVAL_SECONDS, should_sample
from services.assembler.track_state import Phase

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def test_always_samples_non_cruise_phases():
    for phase in (Phase.GROUND, Phase.CLIMB, Phase.DESCENT, Phase.APPROACH, Phase.UNKNOWN):
        assert should_sample(phase, T0, T0 + dt.timedelta(seconds=1))


def test_always_samples_first_ever_report():
    assert should_sample(Phase.CRUISE, None, T0)


def test_cruise_is_throttled_within_the_interval():
    soon = T0 + dt.timedelta(seconds=CRUISE_SAMPLE_INTERVAL_SECONDS - 1)
    assert not should_sample(Phase.CRUISE, T0, soon)


def test_cruise_samples_again_once_the_interval_elapses():
    later = T0 + dt.timedelta(seconds=CRUISE_SAMPLE_INTERVAL_SECONDS)
    assert should_sample(Phase.CRUISE, T0, later)
