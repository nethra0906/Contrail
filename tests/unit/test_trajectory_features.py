"""Tests for the single shared trajectory-feature implementation. The
determinism/parity test here is a smaller-scale stand-in for Stage 4's
required train/serve parity test (same computation, called from training and
serving) - proving this function is itself pure and deterministic is the
precondition for that later test meaning anything.
"""

from __future__ import annotations

import pytest

from services.common.features.trajectory import TrackPoint, compute_trajectory_features
from services.common.geo import LatLon


def _straight_line_window(n: int = 5, speed_mps: float = 100.0) -> list[TrackPoint]:
    """A synthetic window flying due north at constant speed and altitude -
    a known-answer case: groundspeed should be constant, turn rate zero,
    vertical rate zero.
    """
    points = []
    lat0 = 40.0
    for i in range(n):
        # ~100 m/s north ~= 0.0009 deg lat per second (rough, fine for a unit test)
        dt = float(i)
        points.append(
            TrackPoint(
                ts_offset_s=dt,
                lat=lat0 + i * 0.0009,
                lon=-74.0,
                alt_ft=35000.0,
                vert_rate_fpm=0.0,
                heading_deg=0.0,
            )
        )
    return points


def test_raises_on_window_too_short():
    with pytest.raises(ValueError, match="at least 2 points"):
        compute_trajectory_features([TrackPoint(0, 40.0, -74.0, 35000, 0, 0)])


def test_raises_on_non_increasing_timestamps():
    window = [
        TrackPoint(0, 40.0, -74.0, 35000, 0, 0),
        TrackPoint(0, 40.001, -74.0, 35000, 0, 0),  # same offset - not increasing
    ]
    with pytest.raises(ValueError, match="strictly increasing"):
        compute_trajectory_features(window)


def test_straight_level_flight_has_near_zero_turn_and_vertical_rate():
    window = _straight_line_window()
    features = compute_trajectory_features(window, phase="cruise")

    assert all(abs(t) < 1e-6 for t in features.turn_rate_deg_s)
    assert all(v == 0.0 for v in features.vertical_rate_fpm)
    # Roughly 100 m/s ~= 194 kt, generous tolerance for the flat-earth approx.
    assert all(abs(gs - 194.4) < 5 for gs in features.groundspeed_kt)


def test_output_length_matches_window_size_minus_one():
    window = _straight_line_window(n=12)
    features = compute_trajectory_features(window, phase="cruise")
    assert len(features.east_deltas_m) == 11
    assert len(features.groundspeed_kt) == 11
    assert len(features.turn_rate_deg_s) == 11


def test_first_point_is_the_enu_origin():
    window = _straight_line_window()
    features = compute_trajectory_features(window)
    # The first delta is relative to window[0] -> window[1], not window[0]
    # itself (which isn't in the output at all, by construction).
    assert features.east_deltas_m[0] != 0 or features.north_deltas_m[0] != 0


def test_phase_onehot_marks_exactly_one_phase():
    window = _straight_line_window()
    features = compute_trajectory_features(window, phase="climb")
    assert features.phase_onehot["climb"] == 1
    assert sum(features.phase_onehot.values()) == 1


def test_unknown_phase_string_still_produces_valid_onehot():
    """If the phase classifier ever passes a phase not in the enum tuple
    (shouldn't happen, but the feature function must not crash on it), the
    one-hot should come back all zero rather than raising.
    """
    window = _straight_line_window()
    features = compute_trajectory_features(window, phase="not_a_real_phase")
    assert sum(features.phase_onehot.values()) == 0


def test_destination_bearing_and_distance_computed_when_provided():
    window = _straight_line_window()
    dest = LatLon(41.0, -74.0)  # due north of the window
    features = compute_trajectory_features(window, destination=dest)
    assert features.bearing_to_destination_deg is not None
    assert abs(features.bearing_to_destination_deg) < 5  # ~due north
    assert features.distance_to_destination_km is not None


def test_destination_fields_are_none_when_not_provided():
    window = _straight_line_window()
    features = compute_trajectory_features(window)
    assert features.bearing_to_destination_deg is None
    assert features.distance_to_destination_km is None


def test_missing_altitude_degrades_gracefully_to_zero_vertical_rate():
    window = [
        TrackPoint(0, 40.0, -74.0, None, None, 0.0),
        TrackPoint(1, 40.001, -74.0, None, None, 0.0),
    ]
    features = compute_trajectory_features(window)
    assert features.vertical_rate_fpm == [0.0]
    assert features.alt_deltas_ft == [0.0]


def test_computation_is_deterministic_across_repeated_calls():
    """Stand-in for the Stage 4 train/serve parity requirement: calling this
    function twice on identical input must produce byte-identical output -
    no hidden randomness, no wall-clock dependence, no mutable shared state.
    """
    window = _straight_line_window(n=8)
    dest = LatLon(41.0, -74.0)
    a = compute_trajectory_features(window, destination=dest, phase="cruise")
    b = compute_trajectory_features(window, destination=dest, phase="cruise")
    assert a == b


def test_turn_rate_sign_reflects_turn_direction():
    """A window turning right (heading increasing) should have positive
    turn_rate; turning left should be negative - this sign convention is
    what the model's turn-rate feature depends on meaning something.
    """
    right_turn = [
        TrackPoint(0, 40.0, -74.0, 10000, 0, 0.0),
        TrackPoint(1, 40.001, -74.0, 10000, 0, 30.0),
    ]
    left_turn = [
        TrackPoint(0, 40.0, -74.0, 10000, 0, 30.0),
        TrackPoint(1, 40.001, -74.0, 10000, 0, 0.0),
    ]
    assert compute_trajectory_features(right_turn).turn_rate_deg_s[0] > 0
    assert compute_trajectory_features(left_turn).turn_rate_deg_s[0] < 0
