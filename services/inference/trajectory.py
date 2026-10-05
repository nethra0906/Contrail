"""Live serving for M1 (trajectory forecasting): loads the currently
promoted ONNX model and produces per-horizon quantile position predictions
("uncertainty cones" - master spec Stage 4 deliverable) for a tracked
aircraft from its recent `state_vectors` history.

Uses onnxruntime, not the PyTorch module (ml.models.trajectory_gru)
directly - master spec §7: "M1/M4 export to ONNX... loaded in-process."
Live serving doesn't import torch at all, keeping the api service's own
dependency footprint smaller than the full ml extras (torch is a heavy
dependency only training needs).

Honest caveat: predictions are only as good as the window of recent
position history available - an aircraft with fewer than
services.common.features.trajectory.WINDOW_SIZE recent reports in
`state_vectors` has no prediction available yet, returned as None rather
than a prediction computed from a short/padded window (same "never
fabricate missing input" rule every other feature module in this project
follows).
"""

from __future__ import annotations

import numpy as np
import onnxruntime as ort
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ml.datasets.trajectory import HORIZONS_S, PHASES, STATIC_DIM, WTC_CATEGORIES
from services.assembler.sinks.aircraft_registry import type_code_for
from services.assembler.track_state import TrackState, advance
from services.common.features.trajectory import WINDOW_SIZE, TrackPoint, compute_trajectory_features
from services.common.models import StateVector
from services.common.models.ml import ModelRegistry
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import get_logger

logger = get_logger(__name__)

# Keyed by model_version; cleared on promotion change, same pattern as
# services/inference/network.py's model cache.
_session_cache: dict[str, ort.InferenceSession] = {}


async def _load_promoted_session(session: AsyncSession) -> tuple[str, ort.InferenceSession] | None:
    result = await session.execute(
        select(ModelRegistry).where(
            ModelRegistry.kind == "trajectory", ModelRegistry.promoted.is_(True)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.model_version not in _session_cache:
        _session_cache.clear()
        _session_cache[row.model_version] = ort.InferenceSession(row.artifact_uri)
    return row.model_version, _session_cache[row.model_version]


def _wtc_for_type_code(type_code: str | None) -> str:
    """Mirrors ml/datasets/trajectory.py's _wtc_for exactly (same OpenAP
    lookup, same fallback) - kept as a separate small function here rather
    than importing that one, since it's named as a training-time internal
    (leading underscore) and this is the live-serving analogue, not a reuse
    of training-time plumbing.
    """
    if not type_code:
        return "unknown"
    try:
        from openap import prop

        wtc = prop.aircraft(type_code.strip().lower())["wtc"]
        return wtc if wtc in WTC_CATEGORIES else "unknown"
    except Exception:
        return "unknown"


async def predict_trajectory(session: AsyncSession, icao24: str) -> dict | None:
    """Returns {"model_version", "icao24", "horizons": [{horizon_s, q10, q50,
    q90}, ...]} (each q* a [east_m, north_m, alt_ft] triple relative to the
    aircraft's most recent known position), or None if no "trajectory"
    model is promoted yet, or there isn't a long-enough recent track.
    """
    loaded = await _load_promoted_session(session)
    if loaded is None:
        return None
    model_version, ort_session = loaded

    icao24 = icao24.lower()
    rows = (
        (
            await session.execute(
                select(StateVector)
                .where(StateVector.icao24 == icao24)
                .order_by(StateVector.ts.desc())
                .limit(WINDOW_SIZE)
            )
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))  # chronological order
    if len(rows) < WINDOW_SIZE:
        return None

    previous: TrackState | None = None
    track: TrackState | None = None
    for r in rows:
        sv = StateVectorIn(
            icao24=r.icao24,
            ts=r.ts,
            lat=r.lat,
            lon=r.lon,
            baro_alt_ft=r.baro_alt_ft,
            velocity_kt=r.velocity_kt,
            heading_deg=r.heading_deg,
            vert_rate_fpm=r.vert_rate_fpm,
            on_ground=r.on_ground,
            squawk=r.squawk,
            source=r.source,
        )
        track = advance(sv, previous)
        previous = track
    assert track is not None  # loop runs >=1 time: len(rows) >= WINDOW_SIZE was checked above
    phase = track.phase.value

    points = [
        TrackPoint(
            ts_offset_s=(r.ts - rows[0].ts).total_seconds(),
            lat=r.lat,
            lon=r.lon,
            alt_ft=r.baro_alt_ft,
            vert_rate_fpm=r.vert_rate_fpm,
            heading_deg=r.heading_deg,
        )
        for r in rows
    ]
    try:
        features = compute_trajectory_features(points, destination=None, phase=phase)
    except ValueError:
        return None

    x = np.stack(
        [
            features.east_deltas_m,
            features.north_deltas_m,
            features.alt_deltas_ft,
            features.groundspeed_kt,
            features.vertical_rate_fpm,
            features.turn_rate_deg_s,
        ],
        axis=1,
    ).astype(np.float32)[None, :, :]  # (1, WINDOW_SIZE-1, 6)

    type_code = await type_code_for(session, icao24, {})
    wtc = _wtc_for_type_code(type_code)
    static = np.zeros((1, STATIC_DIM), dtype=np.float32)
    static[0, PHASES.index(phase)] = 1.0
    static[0, len(PHASES) + WTC_CATEGORIES.index(wtc)] = 1.0

    (output,) = ort_session.run(None, {"x": x, "static": static})  # (1, H, 3, 3)
    output = output[0]  # (H, 3, 3): [horizon][quantile][dim]

    return {
        "model_version": model_version,
        "icao24": icao24,
        "horizons": [
            {
                "horizon_s": h,
                "q10": output[h_i, 0, :].tolist(),
                "q50": output[h_i, 1, :].tolist(),
                "q90": output[h_i, 2, :].tolist(),
            }
            for h_i, h in enumerate(HORIZONS_S)
        ],
    }
