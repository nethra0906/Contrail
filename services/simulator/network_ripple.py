"""Couples the counterfactual runway-closure scenario to M3 (the already-
trained delay-propagation GNN) - the "M3 propagation coupling" the master
spec names as part of Stage 7, done without retraining anything.

Honest framing of what this actually computes, stated once here rather
than left implicit: `DelayGNN.forward()` conditions on SEQUENCE_LENGTH
trailing 15-minute buckets of real history to forecast delay 1-6 hours
ahead (ml/datasets/network.py). A scenario's runway closure starts exactly
at the end of that trailing window, so the model has never seen the
closure's effect by the time it makes its forecast. What this module
reports instead is a real, bounded what-if: "if this airport's most recent
throughput had already been reduced to zero by a closure like this one,
how much higher does the trained model's own delay forecast at its
flow-connected neighbors come out, compared to the real unperturbed
history?" That's a genuine reuse of the trained model's learned
propagation structure, not a claim about what will literally happen next -
the gap between the two is exactly why this docstring exists.

Best-effort throughout: if no trained checkpoint is on disk, or the
airport isn't in the graph the checkpoint was trained on, or there isn't
enough trailing history this early in the cached month, this returns None
rather than fabricating a number - the same "404, not a stub" convention
services/inference/network.py already established for the live endpoint.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from ml.datasets.network import (
    BUCKET_MINUTES,
    HORIZONS_BUCKETS,
    SEQUENCE_LENGTH,
    AirportGraph,
    build_arrays,
)
from ml.models.delay_gnn import DelayGNN
from services.common.features.network import FEATURE_COLUMNS
from services.simulator.historical import load_simulation_month

MODEL_DIR = Path("data/models")

# Index of each feature this module zeroes out to represent "no real
# operations happened at this airport during the closure" - see
# FEATURE_COLUMNS in services/common/features/network.py for the full,
# ordered list these indices must stay in sync with.
_MEAN_DEP_DELAY_IDX = FEATURE_COLUMNS.index("mean_dep_delay_min")
_MEAN_ARR_DELAY_IDX = FEATURE_COLUMNS.index("mean_arr_delay_min")
_OPS_COUNT_IDX = FEATURE_COLUMNS.index("ops_count")
_CANCELLATIONS_IDX = FEATURE_COLUMNS.index("cancellations")

# How many of the top flow-connected neighbors to report - keeps the API
# response small and focused on the airports actually likely to feel it.
TOP_N_NEIGHBORS = 5


@dataclass(frozen=True)
class RippleEstimate:
    model_version: str
    horizon_minutes: int
    neighbor_delay_delta_min: dict[str, float]


def _latest_checkpoint_path() -> Path | None:
    candidates = sorted(MODEL_DIR.glob("delay-gnn-*.pt"))
    return candidates[-1] if candidates else None


def _load_checkpoint(
    path: Path,
) -> tuple[DelayGNN, list[str], torch.Tensor, torch.Tensor]:
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    model = DelayGNN()
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    flow_adj = torch.tensor(checkpoint["flow_adj"], dtype=torch.float32)
    rotation_adj = torch.tensor(checkpoint["rotation_adj"], dtype=torch.float32)
    return model, checkpoint["airports"], flow_adj, rotation_adj


@lru_cache(maxsize=1)
def _cached_checkpoint() -> tuple[DelayGNN, list[str], torch.Tensor, torch.Tensor] | None:
    path = _latest_checkpoint_path()
    if path is None:
        return None
    return _load_checkpoint(path)


@lru_cache(maxsize=1)
def _cached_graph_and_arrays() -> tuple[AirportGraph, np.ndarray, np.ndarray]:
    """Builds the node-feature tensor `build_arrays` needs, WITHOUT paying
    `ml.datasets.network.build_graph`'s real adjacency-matrix construction
    cost (measured directly: ~107s of this module's ~115s cold-start,
    almost entirely its rotation-edge computation's per-tail-number Python
    loop over hundreds of thousands of rows). That cost buys flow_adj/
    rotation_adj - and this module never uses them, since the forward pass
    below always uses the CHECKPOINT's own trained adjacency (the graph
    structure the model actually learned against), never a freshly rebuilt
    one. `build_arrays` itself only ever reads `graph.airports` (for node
    indexing), so a real airports list with placeholder zero adjacency
    matrices produces an identical `X`/`bucket_starts` at a fraction of
    the cost.
    """
    df = load_simulation_month()
    airports = sorted(set(df["origin"]).union(df["dest"]))
    placeholder_adj = np.zeros((len(airports), len(airports)), dtype=np.float32)
    graph = AirportGraph(airports=airports, flow_adj=placeholder_adj, rotation_adj=placeholder_adj)
    arrays = build_arrays(df, graph)
    return graph, arrays.X, arrays.bucket_starts


def estimate_ripple(
    airport: str, day: dt.date, closure_start_minute: float, closure_duration_minutes: float
) -> RippleEstimate | None:
    cached = _cached_checkpoint()
    if cached is None:
        return None
    model, ckpt_airports, flow_adj, rotation_adj = cached

    graph, X, bucket_starts = _cached_graph_and_arrays()
    if ckpt_airports != graph.airports:
        # Checkpoint and the freshly-built graph disagree on node ordering
        # (e.g. a retrain against a different window) - refuse rather than
        # silently indexing into the wrong airport.
        return None
    if airport not in graph.airports:
        return None
    node_idx = graph.airports.index(airport)

    day_start = pd.Timestamp(day, tz="UTC").to_pydatetime().replace(tzinfo=None)
    closure_start_ts = np.datetime64(day_start + dt.timedelta(minutes=closure_start_minute), "ns")
    bucket_idx = int(np.searchsorted(bucket_starts, closure_start_ts))
    if bucket_idx < SEQUENCE_LENGTH or bucket_idx >= X.shape[0]:
        return None

    affected_buckets = min(
        SEQUENCE_LENGTH,
        -(-int(closure_duration_minutes) // BUCKET_MINUTES),  # ceil div
    )

    window = X[bucket_idx - SEQUENCE_LENGTH : bucket_idx].copy()
    perturbed = window.copy()
    if affected_buckets > 0:
        perturbed[-affected_buckets:, node_idx, _OPS_COUNT_IDX] = 0.0
        perturbed[-affected_buckets:, node_idx, _CANCELLATIONS_IDX] = 0.0
        perturbed[-affected_buckets:, node_idx, _MEAN_DEP_DELAY_IDX] = 0.0
        perturbed[-affected_buckets:, node_idx, _MEAN_ARR_DELAY_IDX] = 0.0

    with torch.no_grad():
        baseline_out = model(
            torch.tensor(window, dtype=torch.float32).unsqueeze(0), flow_adj, rotation_adj
        )[0]
        perturbed_out = model(
            torch.tensor(perturbed, dtype=torch.float32).unsqueeze(0), flow_adj, rotation_adj
        )[0]
    delta = (perturbed_out - baseline_out)[:, 0]  # nearest horizon only (1h ahead)

    neighbor_order = np.argsort(-flow_adj[node_idx].numpy())
    neighbors = {
        graph.airports[j]: float(delta[j])
        for j in neighbor_order
        if j != node_idx and flow_adj[node_idx, j] > 0
    }
    top_neighbors = dict(list(neighbors.items())[:TOP_N_NEIGHBORS])

    checkpoint_path = _latest_checkpoint_path()
    model_version = checkpoint_path.stem if checkpoint_path else "unknown"
    return RippleEstimate(
        model_version=model_version,
        horizon_minutes=HORIZONS_BUCKETS[0] * BUCKET_MINUTES,
        neighbor_delay_delta_min=top_neighbors,
    )
