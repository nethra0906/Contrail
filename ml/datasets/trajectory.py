"""Turns loaded historical position data (ml/data/loaders/adsbx_hist.py)
into M1's training windows, applying services/common/features/trajectory.py's
single feature implementation - the same function the live inference path
(services/inference/trajectory.py) calls, per execution rule 5.

Per docs/adr/0004: this trains on ADS-B Exchange historical samples, not
this project's own live-ingested history (which doesn't have enough
accumulated continuous track data yet), and destination/wind-aloft features
are omitted (no scheduled-destination join or weather data available for
this historical source) - WINDOW_SIZE and the per-step kinematic features
(position deltas, groundspeed, vertical rate, turn rate) and the phase/WTC
static context are all real and computed identically to how a live caller
would.
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass

import numpy as np
import pandas as pd

from services.assembler.track_state import Phase, TrackState, advance
from services.common.features.trajectory import WINDOW_SIZE, TrackPoint, compute_trajectory_features
from services.common.geo import LatLon, to_enu

# A minimal duck-typed stand-in for services.common.schemas.aircraft.StateVectorIn,
# carrying only the five attributes track_state.advance()/classify_phase()
# actually read (icao24, ts, on_ground, baro_alt_ft, vert_rate_fpm). Building
# this dataset calls advance() once per historical position report (millions
# of rows for a one-hour window) - full Pydantic validation per call measured
# as the dominant cost building these arrays, for validation this data has
# already passed once (it came from ml/data/loaders/adsbx_hist.py's own
# parsing). advance()/classify_phase() only ever read attributes, never
# construct or revalidate a StateVectorIn themselves, so this is safe.
_PhaseInput = namedtuple(
    "_PhaseInput", ["icao24", "ts", "on_ground", "baro_alt_ft", "vert_rate_fpm"]
)

# Prediction horizons, in seconds - master spec §7's M1 list.
HORIZONS_S: tuple[int, ...] = (60, 180, 300, 600, 900)

# The ADS-B Exchange sample data's native cadence is 5s by construction
# (docs/adr/0004). A gap bigger than this between two consecutive reports
# for the same aircraft ends a continuous segment, mirroring
# track_state.COVERAGE_GAP_SECONDS's purpose but tighter, since this
# source's own cadence is itself only 5s (a 15s gap is already 3 missed
# snapshots in a row, not a brief dropout).
MAX_GAP_S = 15.0
HORIZON_TOLERANCE_S = 3.0

# Stride between consecutive window starts, in points (at 5s native cadence,
# a stride of 4 points = 20s) - keeps adjacent training windows from being
# near-duplicates of each other (a stride of 1 would mean 11 of every 12
# points are shared between consecutive windows), without discarding most
# of the data the way a non-overlapping stride of WINDOW_SIZE would.
DEFAULT_STRIDE_POINTS = 4

PHASES: tuple[str, ...] = ("ground", "climb", "cruise", "descent", "approach", "unknown")
WTC_CATEGORIES: tuple[str, ...] = ("L", "M", "H", "J", "unknown")
NUM_STEP_FEATURES = (
    6  # east_delta_m, north_delta_m, alt_delta_ft, gs_kt, vrate_fpm, turn_rate_deg_s
)
STATIC_DIM = len(PHASES) + len(WTC_CATEGORIES)


@dataclass
class TrajectoryArrays:
    """Model-ready arrays. X/static are the GRU's inputs; y/y_mask are the
    multi-horizon regression targets and which of them have real ground
    truth (a window near the end of the downloaded time range may have no
    valid target for the longer horizons - those are masked out of the loss
    rather than fabricated, see build_arrays' docstring).
    """

    X: np.ndarray  # (N, WINDOW_SIZE - 1, NUM_STEP_FEATURES), float32
    static: np.ndarray  # (N, STATIC_DIM), float32 (one-hot phase + WTC)
    y: np.ndarray  # (N, len(HORIZONS_S), 3): dE_m, dN_m, dAlt_ft, float32
    y_mask: np.ndarray  # (N, len(HORIZONS_S)), bool
    icao24: np.ndarray  # (N,) object - for inspection/debugging, not a model input
    anchor_ts: np.ndarray  # (N,) datetime64[ns] - the window's last point's timestamp,
    # i.e. what a chronological split sorts on (consistent with ml/data/split.py's
    # "split by time, never randomly" rule - this is the time value that rule applies to).


def _segments_for_aircraft(df_one: pd.DataFrame) -> list[pd.DataFrame]:
    df_one = df_one.sort_values("ts").reset_index(drop=True)
    gap_s = df_one["ts"].diff().dt.total_seconds().fillna(0)
    segment_id = (gap_s > MAX_GAP_S).cumsum()
    return [g.reset_index(drop=True) for _, g in df_one.groupby(segment_id)]


def _phase_sequence(segment: pd.DataFrame) -> list[Phase]:
    """Runs the exact same rules-based phase classifier the live assembler
    pipeline uses (services.assembler.track_state.advance) over a historical
    segment, point by point, so a window's phase context is computed
    identically regardless of whether the underlying data is historical or
    live.
    """
    previous: TrackState | None = None
    phases: list[Phase] = []
    for row in segment.itertuples():
        sv = _PhaseInput(
            icao24=row.icao24,
            ts=row.ts.to_pydatetime(),
            on_ground=bool(row.on_ground),
            baro_alt_ft=None if pd.isna(row.baro_alt_ft) else float(row.baro_alt_ft),
            vert_rate_fpm=None if pd.isna(row.vert_rate_fpm) else float(row.vert_rate_fpm),
        )
        # advance()/classify_phase() are typed to take a real StateVectorIn,
        # but only ever read the five attributes _PhaseInput provides (see
        # its own docstring for why this duck-typed stand-in exists) -
        # correct at runtime, not expressible without a Protocol change to
        # track_state.py's public signature, which isn't worth making for
        # this one internal call site.
        track = advance(sv, previous)  # type: ignore[arg-type]
        phases.append(track.phase)
        previous = track
    return phases


def _wtc_for(type_code: str | None) -> str:
    """Wake-turbulence category via OpenAP's bundled aircraft performance
    tables - the same library services/assembler/enrich.py already uses for
    fuel estimation, reused here rather than adding a second aircraft-
    properties data source.
    """
    if not type_code:
        return "unknown"
    try:
        from openap import prop

        wtc = prop.aircraft(type_code.strip().lower())["wtc"]
        return wtc if wtc in WTC_CATEGORIES else "unknown"
    except Exception:
        return "unknown"


def _static_vector(phase: Phase, wtc: str) -> np.ndarray:
    v = np.zeros(STATIC_DIM, dtype=np.float32)
    v[PHASES.index(phase.value if isinstance(phase, Phase) else phase)] = 1.0
    v[len(PHASES) + WTC_CATEGORIES.index(wtc)] = 1.0
    return v


def _step_matrix(features) -> np.ndarray:
    """TrajectoryFeatures' six per-step lists, stacked into (WINDOW_SIZE-1,
    NUM_STEP_FEATURES) in the fixed column order the model is trained/served
    on - mirrors services/common/features/eta.py's FEATURE_COLUMNS contract,
    adapted for a sequence model's per-step input instead of one flat row.
    """
    return np.stack(
        [
            features.east_deltas_m,
            features.north_deltas_m,
            features.alt_deltas_ft,
            features.groundspeed_kt,
            features.vertical_rate_fpm,
            features.turn_rate_deg_s,
        ],
        axis=1,
    ).astype(np.float32)


def _nearest_index(sorted_seconds: np.ndarray, target_s: float) -> int | None:
    """Index of the entry in `sorted_seconds` (a segment's timestamps, in
    seconds since the segment start - monotonically increasing by
    construction) nearest to `target_s`, via binary search rather than a
    full linear scan. Each segment's array is built once and reused across
    every window/horizon lookup within it (see build_arrays) - this is what
    turns target-lookup from O(segment length) per (window, horizon) pair
    into O(log segment length), the dominant cost found by profiling this
    dataset builder against real data.
    """
    pos = int(np.searchsorted(sorted_seconds, target_s))
    candidates = [i for i in (pos - 1, pos) if 0 <= i < len(sorted_seconds)]
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs(sorted_seconds[i] - target_s))


def build_arrays(df: pd.DataFrame, stride_points: int = DEFAULT_STRIDE_POINTS) -> TrajectoryArrays:
    """Builds every valid WINDOW_SIZE-point window, across every aircraft and
    every gap-free segment, from a loaded position-report DataFrame (the
    shape ml.data.loaders.adsbx_hist.load_range returns).

    A window's target for a given horizon is included (y_mask=True) only
    when a real observation exists within HORIZON_TOLERANCE_S of that
    horizon in the same unbroken segment - windows near the end of the
    downloaded time range legitimately have no valid long-horizon target,
    and are kept with that horizon masked out rather than dropped entirely
    or fabricated, so the shorter horizons' real ground truth still
    contributes to training.
    """
    X_list, static_list, y_list, mask_list, icao_list, ts_list = [], [], [], [], [], []
    horizons_arr = np.asarray(HORIZONS_S, dtype=np.float64)

    for icao24, group in df.groupby("icao24"):
        for segment in _segments_for_aircraft(group):
            if len(segment) < WINDOW_SIZE:
                continue
            phases = _phase_sequence(segment)
            wtc = _wtc_for(segment["type_code"].iloc[0] if "type_code" in segment else None)

            # Precomputed once per segment, reused by every window and every
            # horizon lookup within it - see _nearest_index's docstring.
            seg_ts_s = (segment["ts"] - segment["ts"].iloc[0]).dt.total_seconds().to_numpy()
            seg_lat = segment["lat"].to_numpy(dtype=np.float64)
            seg_lon = segment["lon"].to_numpy(dtype=np.float64)
            seg_alt = segment["baro_alt_ft"].to_numpy(dtype=np.float64)  # NaN where missing
            seg_vrate = segment["vert_rate_fpm"].to_numpy(dtype=np.float64)
            seg_heading = segment["heading_deg"].to_numpy(dtype=np.float64)

            starts = range(0, len(segment) - WINDOW_SIZE + 1, stride_points)
            for start in starts:
                end = start + WINDOW_SIZE  # exclusive
                anchor_idx = end - 1
                window_t0_s = seg_ts_s[start]

                points = [
                    TrackPoint(
                        ts_offset_s=float(seg_ts_s[i] - window_t0_s),
                        lat=float(seg_lat[i]),
                        lon=float(seg_lon[i]),
                        alt_ft=None if np.isnan(seg_alt[i]) else float(seg_alt[i]),
                        vert_rate_fpm=None if np.isnan(seg_vrate[i]) else float(seg_vrate[i]),
                        heading_deg=None if np.isnan(seg_heading[i]) else float(seg_heading[i]),
                    )
                    for i in range(start, end)
                ]
                try:
                    features = compute_trajectory_features(
                        points, destination=None, phase=phases[anchor_idx].value
                    )
                except ValueError:
                    continue  # non-increasing timestamps within tolerance - skip, don't fabricate

                anchor_ts_s = seg_ts_s[anchor_idx]
                anchor_ll = LatLon(seg_lat[anchor_idx], seg_lon[anchor_idx])
                anchor_alt = seg_alt[anchor_idx]

                targets = np.zeros((len(HORIZONS_S), 3), dtype=np.float32)
                mask = np.zeros(len(HORIZONS_S), dtype=bool)
                for h_i, horizon in enumerate(horizons_arr):
                    best = _nearest_index(seg_ts_s, anchor_ts_s + horizon)
                    if (
                        best is None
                        or abs(seg_ts_s[best] - (anchor_ts_s + horizon)) > HORIZON_TOLERANCE_S
                    ):
                        continue
                    east_m, north_m = to_enu(LatLon(seg_lat[best], seg_lon[best]), anchor_ll)
                    future_alt = seg_alt[best]
                    alt_delta_ft = (
                        0.0
                        if (np.isnan(anchor_alt) or np.isnan(future_alt))
                        else float(future_alt - anchor_alt)
                    )
                    targets[h_i] = (float(east_m), float(north_m), alt_delta_ft)
                    mask[h_i] = True
                if not mask.any():
                    continue  # no usable target at any horizon - not a trainable example

                X_list.append(_step_matrix(features))
                static_list.append(_static_vector(phases[anchor_idx], wtc))
                y_list.append(targets)
                mask_list.append(mask)
                icao_list.append(icao24)
                ts_list.append(segment["ts"].iloc[anchor_idx])

    if not X_list:
        raise ValueError(
            "no trainable windows found - check the input data covers enough time/aircraft"
        )

    return TrajectoryArrays(
        X=np.stack(X_list),
        static=np.stack(static_list),
        y=np.stack(y_list),
        y_mask=np.stack(mask_list),
        icao24=np.array(icao_list, dtype=object),
        anchor_ts=np.array(ts_list, dtype="datetime64[ns]"),
    )
