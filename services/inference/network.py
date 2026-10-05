"""Live serving for M3 (delay-propagation GNN): loads the currently
promoted "network" model and produces a delay forecast for a requested
airport from recent `flights` activity across every node in the model's
trained graph.

Honest caveat, more specific than "the live system hasn't accumulated
enough history yet" (though that's also true): the live assembler pipeline
(services/assembler/sinks/flights.py) populates `actual_dep`/`actual_arr`
on `flights` rows, but never `dep_delay_min`/`arr_delay_min` - computing a
delay needs a SCHEDULED time to compare against, and no scheduled-flight-
timetable ingestion source exists in this project yet (the live pipeline
only ever observes actual aircraft movement, never a published schedule).
So a live forecast today reflects real ops-count/cancellation/calendar
signal but a genuinely empty delay signal, not a fabricated one - this
module computes exactly what's really available, honestly, rather than
backfilling a plausible-looking number. It becomes meaningful once a
schedule-ingestion source exists, without any change needed here (the
shared feature function already accepts a real value whenever one exists).
"""

from __future__ import annotations

import datetime as dt

import torch
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ml.datasets.network import BUCKET_MINUTES, BUCKETS_PER_HOUR, HORIZONS_BUCKETS, SEQUENCE_LENGTH
from ml.models.delay_gnn import DelayGNN
from services.common.features.network import FEATURE_COLUMNS, compute_network_features
from services.common.models import Flight
from services.common.models.ml import ModelRegistry
from services.common.telemetry import get_logger

logger = get_logger(__name__)

# Keyed by model_version; cleared whenever a different version is promoted,
# so serving picks up a newly promoted model without a process restart.
_model_cache: dict[str, tuple[DelayGNN, list[str], torch.Tensor, torch.Tensor]] = {}


async def _load_promoted_model(
    session: AsyncSession,
) -> tuple[str, DelayGNN, list[str], torch.Tensor, torch.Tensor] | None:
    result = await session.execute(
        select(ModelRegistry).where(
            ModelRegistry.kind == "network", ModelRegistry.promoted.is_(True)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    if row.model_version in _model_cache:
        model, airports, flow_adj, rotation_adj = _model_cache[row.model_version]
        return row.model_version, model, airports, flow_adj, rotation_adj

    checkpoint = torch.load(row.artifact_uri, map_location="cpu", weights_only=False)
    model = DelayGNN()
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    airports = checkpoint["airports"]
    flow_adj = torch.tensor(checkpoint["flow_adj"], dtype=torch.float32)
    rotation_adj = torch.tensor(checkpoint["rotation_adj"], dtype=torch.float32)

    _model_cache.clear()
    _model_cache[row.model_version] = (model, airports, flow_adj, rotation_adj)
    return row.model_version, model, airports, flow_adj, rotation_adj


async def _recent_node_features(session: AsyncSession, airports: list[str]) -> torch.Tensor:
    """Builds a (SEQUENCE_LENGTH, N, F) tensor of the most recent
    SEQUENCE_LENGTH 15-min buckets' node features from the live `flights`
    table, using the same shared feature function
    (services.common.features.network.compute_network_features) training
    applies to historical BTS data - the live-serving counterpart to
    ml/datasets/network.py's bucket aggregation.
    """
    bucket_duration = dt.timedelta(minutes=BUCKET_MINUTES)
    window_start = dt.datetime.now(dt.UTC) - bucket_duration * SEQUENCE_LENGTH

    rows = (
        (
            await session.execute(
                select(Flight).where(
                    or_(Flight.actual_dep >= window_start, Flight.actual_arr >= window_start),
                    or_(Flight.origin_icao.in_(airports), Flight.dest_icao.in_(airports)),
                )
            )
        )
        .scalars()
        .all()
    )

    idx = {a: i for i, a in enumerate(airports)}
    n = len(airports)
    X = torch.zeros(SEQUENCE_LENGTH, n, len(FEATURE_COLUMNS), dtype=torch.float32)
    ops_by_bucket_node: dict[tuple[int, int], int] = {}
    cancel_by_bucket_node: dict[tuple[int, int], int] = {}

    def _bucket_of(ts: dt.datetime) -> int | None:
        b = int((ts - window_start) / bucket_duration)
        return b if 0 <= b < SEQUENCE_LENGTH else None

    for f in rows:
        if f.actual_dep is not None and f.origin_icao in idx:
            b = _bucket_of(f.actual_dep)
            if b is not None:
                key = (b, idx[f.origin_icao])
                ops_by_bucket_node[key] = ops_by_bucket_node.get(key, 0) + 1
                if f.cancelled:
                    cancel_by_bucket_node[key] = cancel_by_bucket_node.get(key, 0) + 1
        if f.actual_arr is not None and f.dest_icao in idx:
            b = _bucket_of(f.actual_arr)
            if b is not None:
                key = (b, idx[f.dest_icao])
                ops_by_bucket_node[key] = ops_by_bucket_node.get(key, 0) + 1

    for (b, n_idx), ops in ops_by_bucket_node.items():
        bucket_ts = window_start + b * bucket_duration
        features = compute_network_features(
            mean_dep_delay_min=None,  # see module docstring - not populated by the live pipeline
            mean_arr_delay_min=None,
            ops_count=ops,
            cancellations=cancel_by_bucket_node.get((b, n_idx), 0),
            bucket_hour=bucket_ts.hour,
            bucket_day_of_week=bucket_ts.weekday(),
            bucket_date_iso=bucket_ts.strftime("%Y-%m-%d"),
        )
        X[b, n_idx] = torch.tensor([getattr(features, c) for c in FEATURE_COLUMNS])

    return X


async def forecast_delay(session: AsyncSession, icao: str) -> dict | None:
    """Returns a per-horizon arrival-delay forecast for one airport, or
    None if no "network" model has been promoted yet, or the airport isn't
    one of the graph's trained nodes (it wasn't among the ~334 airports
    with scheduled traffic in the BTS training window).
    """
    loaded = await _load_promoted_model(session)
    if loaded is None:
        return None
    model_version, model, airports, flow_adj, rotation_adj = loaded

    icao = icao.upper()
    if icao not in airports:
        return None

    X = await _recent_node_features(session, airports)
    with torch.no_grad():
        pred = model(X.unsqueeze(0), flow_adj, rotation_adj)  # (1, N, len(HORIZONS_BUCKETS))

    node_idx = airports.index(icao)
    return {
        "airport": icao,
        "model_version": model_version,
        "horizons": [
            {
                "hours": h // BUCKETS_PER_HOUR,
                "predicted_arr_delay_min": float(pred[0, node_idx, h_i]),
            }
            for h_i, h in enumerate(HORIZONS_BUCKETS)
        ],
    }
