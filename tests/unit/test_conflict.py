"""Unit tests for M5's conflict-probability pruning and Monte Carlo
estimation (services/inference/conflict.py). Pure logic, no I/O - same
philosophy as the assembler's pipeline tests.
"""

from __future__ import annotations

from services.common.determinism import Rng
from services.inference.conflict import (
    AircraftSnapshot,
    QuantilePrediction,
    monte_carlo_conflict_probability,
    prune_candidate_pairs,
)


def _sv(icao24: str, lat: float, lon: float, alt_ft: float) -> AircraftSnapshot:
    return AircraftSnapshot(icao24=icao24, lat=lat, lon=lon, alt_ft=alt_ft)


def _tight_prediction(horizon_s: int, spread: float = 50.0) -> QuantilePrediction:
    return QuantilePrediction(
        horizon_s=horizon_s,
        q10=(-spread, -spread, -spread),
        q50=(0, 0, 0),
        q90=(spread, spread, spread),
    )


# ---- prune_candidate_pairs ----


def test_nearby_same_altitude_aircraft_are_a_candidate_pair():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.001, -74.001, 35100)
    assert prune_candidate_pairs([a, b]) == [(0, 1)]


def test_geographically_distant_aircraft_are_not_a_candidate_pair():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 45.0, -80.0, 35000)
    assert prune_candidate_pairs([a, b]) == []


def test_nearby_but_far_in_altitude_is_not_a_candidate_pair():
    a = _sv("a", 40.0, -74.0, 10000)
    b = _sv("b", 40.001, -74.001, 35000)
    assert prune_candidate_pairs([a, b], altitude_band_ft=2000) == []


def test_three_aircraft_returns_only_the_close_pair():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.001, -74.001, 35000)
    c = _sv("c", 10.0, 10.0, 35000)
    pairs = prune_candidate_pairs([a, b, c])
    assert pairs == [(0, 1)]


def test_empty_input_returns_no_pairs():
    assert prune_candidate_pairs([]) == []


def test_single_aircraft_returns_no_pairs():
    assert prune_candidate_pairs([_sv("a", 40.0, -74.0, 35000)]) == []


# ---- monte_carlo_conflict_probability ----


def test_overlapping_predictions_at_close_range_have_high_conflict_probability():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.001, -74.001, 35050)
    preds = [_tight_prediction(60)]
    p = monte_carlo_conflict_probability(a, b, preds, preds, Rng(1))
    assert p > 0.9


def test_aircraft_predicted_to_diverge_vertically_has_zero_conflict_probability():
    a = _sv("a", 40.0, -74.0, 20000)
    b = _sv("b", 40.001, -74.001, 20000)
    # a climbs 5000ft (the alt axis, index 2), b holds - well beyond the
    # 1000ft vertical threshold, with tight spread on every axis.
    preds_a = [
        QuantilePrediction(horizon_s=60, q10=(-10, -10, 4950), q50=(0, 0, 5000), q90=(10, 10, 5050))
    ]
    preds_b = [_tight_prediction(60, spread=10.0)]
    p = monte_carlo_conflict_probability(a, b, preds_a, preds_b, Rng(1))
    assert p == 0.0


def test_no_shared_horizon_returns_zero():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.001, -74.001, 35000)
    preds_a = [_tight_prediction(60)]
    preds_b = [_tight_prediction(180)]
    assert monte_carlo_conflict_probability(a, b, preds_a, preds_b, Rng(1)) == 0.0


def test_empty_predictions_returns_zero():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.001, -74.001, 35000)
    assert monte_carlo_conflict_probability(a, b, [], [], Rng(1)) == 0.0


def test_result_is_deterministic_for_the_same_seed():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.002, -74.002, 35200)
    preds = [_tight_prediction(60, spread=200.0)]
    p1 = monte_carlo_conflict_probability(a, b, preds, preds, Rng(7))
    p2 = monte_carlo_conflict_probability(a, b, preds, preds, Rng(7))
    assert p1 == p2


def test_different_seeds_can_give_different_results_for_a_borderline_case():
    """Not a strict inequality assertion (two different seeds COULD land on
    the same estimate by chance for a small sample count) - instead checks
    that the function actually consumes randomness rather than being
    secretly deterministic regardless of the Rng passed in, by comparing
    against a wider spread where sample variance is large enough to expect
    a difference almost always.
    """
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.01, -74.01, 35500)
    preds = [_tight_prediction(60, spread=2000.0)]
    results = {
        monte_carlo_conflict_probability(a, b, preds, preds, Rng(seed), n_samples=50)
        for seed in range(5)
    }
    assert len(results) > 1


def test_probability_is_bounded_between_zero_and_one():
    a = _sv("a", 40.0, -74.0, 35000)
    b = _sv("b", 40.0005, -74.0005, 35000)
    preds = [_tight_prediction(h) for h in (60, 180, 300)]
    p = monte_carlo_conflict_probability(a, b, preds, preds, Rng(3))
    assert 0.0 <= p <= 1.0
