"""Builds M4's learned-layer training data: each continuous flight segment
resampled to RESAMPLE_POINTS=128 points (master spec §7, M4) - the input
the 1D conv autoencoder (ml/models/traj_autoencoder.py) reconstructs.

Reuses the same ADS-B Exchange historical data M1 trains on
(ml/data/loaders/adsbx_hist.py) - see docs/adr/0004 for why (BTS has no
position data at all, so it cannot serve M4 either). Only segments with at
least RESAMPLE_POINTS native samples are used, so "resampled to 128 points"
means genuine downsampling of a real continuous track, not mostly-
interpolated padding of a short fragment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.datasets.trajectory import _segments_for_aircraft
from services.common.geo import LatLon, to_enu

RESAMPLE_POINTS = 128
NUM_CHANNELS = 4  # east_m, north_m (relative to segment start), alt_ft, groundspeed_kt


@dataclass
class AnomalyWindows:
    X: np.ndarray  # (N, RESAMPLE_POINTS, NUM_CHANNELS), float32
    icao24: np.ndarray  # (N,) object
    segments: list[
        pd.DataFrame
    ]  # the original (non-resampled) rows per window, for rule-based evaluation


def _resample_segment(segment: pd.DataFrame) -> np.ndarray:
    """Evenly resamples one continuous segment to RESAMPLE_POINTS points via
    linear interpolation over elapsed time - a real downsampling of however
    many native 5s-cadence points the segment actually has (enforced by the
    caller's length filter), not an upsampling of a short fragment.
    """
    ts_s = (segment["ts"] - segment["ts"].iloc[0]).dt.total_seconds().to_numpy()
    anchor = LatLon(segment["lat"].iloc[0], segment["lon"].iloc[0])

    east_list, north_list = [], []
    for lat, lon in zip(segment["lat"], segment["lon"], strict=True):
        e, n = to_enu(LatLon(lat, lon), anchor)
        east_list.append(e)
        north_list.append(n)
    east, north = np.array(east_list), np.array(north_list)
    alt = segment["baro_alt_ft"].fillna(0.0).to_numpy()
    gs = segment["velocity_kt"].fillna(0.0).to_numpy()

    sample_points = np.linspace(ts_s[0], ts_s[-1], RESAMPLE_POINTS)
    resampled = np.stack(
        [
            np.interp(sample_points, ts_s, east),
            np.interp(sample_points, ts_s, north),
            np.interp(sample_points, ts_s, alt),
            np.interp(sample_points, ts_s, gs),
        ],
        axis=1,
    )
    return resampled.astype(np.float32)


def build_windows(df: pd.DataFrame) -> AnomalyWindows:
    X_list, icao_list, segment_list = [], [], []

    for icao24, group in df.groupby("icao24"):
        for segment in _segments_for_aircraft(group):
            if len(segment) < RESAMPLE_POINTS:
                continue
            X_list.append(_resample_segment(segment))
            icao_list.append(icao24)
            segment_list.append(segment)

    if not X_list:
        raise ValueError(
            f"no segment reached {RESAMPLE_POINTS} native points - "
            "widen the training window or lower RESAMPLE_POINTS"
        )

    return AnomalyWindows(
        X=np.stack(X_list), icao24=np.array(icao_list, dtype=object), segments=segment_list
    )
