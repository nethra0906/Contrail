"""services/simulator/network_ripple.py couples the counterfactual engine
to M3 without retraining - these tests exercise that coupling against a
small, synthetic 3-node graph and a freshly-initialized (untrained)
DelayGNN, never the real cached BTS month or a real trained checkpoint, so
this suite runs fully offline and doesn't depend on `make train` having
been run first (data/models/ is gitignored - CI has nothing there).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import torch

from ml.datasets.network import NUM_NODE_FEATURES, SEQUENCE_LENGTH, AirportGraph
from ml.models.delay_gnn import DelayGNN
from services.simulator import network_ripple


def _synthetic_graph_and_arrays():
    airports = ["AAA", "BBB", "CCC"]
    flow_adj = np.array([[0.0, 0.6, 0.4], [0.5, 0.0, 0.5], [0.3, 0.7, 0.0]], dtype=np.float32)
    rotation_adj = np.zeros((3, 3), dtype=np.float32)
    graph = AirportGraph(airports=airports, flow_adj=flow_adj, rotation_adj=rotation_adj)

    total_buckets = SEQUENCE_LENGTH + 4
    rng = np.random.default_rng(0)
    X = rng.random((total_buckets, 3, NUM_NODE_FEATURES)).astype(np.float32)
    bucket_starts = np.array(
        [
            np.datetime64("2024-01-15T00:00:00") + np.timedelta64(15 * i, "m")
            for i in range(total_buckets)
        ],
        dtype="datetime64[ns]",
    )
    return graph, X, bucket_starts


def test_estimate_ripple_returns_none_when_no_checkpoint(monkeypatch):
    monkeypatch.setattr(network_ripple, "_cached_checkpoint", lambda: None)
    result = network_ripple.estimate_ripple("AAA", dt.date(2024, 1, 15), 120.0, 30.0)
    assert result is None


def test_estimate_ripple_returns_none_for_unknown_airport(monkeypatch):
    graph, X, bucket_starts = _synthetic_graph_and_arrays()
    model = DelayGNN()
    model.eval()
    flow_adj_t = torch.tensor(graph.flow_adj)
    rotation_adj_t = torch.tensor(graph.rotation_adj)

    monkeypatch.setattr(
        network_ripple,
        "_cached_checkpoint",
        lambda: (model, graph.airports, flow_adj_t, rotation_adj_t),
    )
    monkeypatch.setattr(
        network_ripple, "_cached_graph_and_arrays", lambda: (graph, X, bucket_starts)
    )

    result = network_ripple.estimate_ripple("ZZZ", dt.date(2024, 1, 15), 120.0, 30.0)
    assert result is None


def test_estimate_ripple_computes_real_forward_pass_delta(monkeypatch):
    graph, X, bucket_starts = _synthetic_graph_and_arrays()
    model = DelayGNN()
    model.eval()
    flow_adj_t = torch.tensor(graph.flow_adj)
    rotation_adj_t = torch.tensor(graph.rotation_adj)

    monkeypatch.setattr(
        network_ripple,
        "_cached_checkpoint",
        lambda: (model, graph.airports, flow_adj_t, rotation_adj_t),
    )
    monkeypatch.setattr(
        network_ripple, "_cached_graph_and_arrays", lambda: (graph, X, bucket_starts)
    )
    monkeypatch.setattr(network_ripple, "_latest_checkpoint_path", lambda: None)

    # 120 minutes in = bucket index 8 == SEQUENCE_LENGTH, the earliest point
    # with a full trailing window given bucket_starts starts at midnight.
    result = network_ripple.estimate_ripple("AAA", dt.date(2024, 1, 15), 120.0, 30.0)

    assert result is not None
    assert result.horizon_minutes == 60
    # AAA's two flow-connected neighbors (BBB, CCC) should both be reported.
    assert set(result.neighbor_delay_delta_min) == {"BBB", "CCC"}


def test_estimate_ripple_returns_none_without_enough_trailing_history(monkeypatch):
    graph, X, bucket_starts = _synthetic_graph_and_arrays()
    model = DelayGNN()
    model.eval()
    flow_adj_t = torch.tensor(graph.flow_adj)
    rotation_adj_t = torch.tensor(graph.rotation_adj)

    monkeypatch.setattr(
        network_ripple,
        "_cached_checkpoint",
        lambda: (model, graph.airports, flow_adj_t, rotation_adj_t),
    )
    monkeypatch.setattr(
        network_ripple, "_cached_graph_and_arrays", lambda: (graph, X, bucket_starts)
    )

    # closure_start_minute=0 is the very first bucket - no trailing history exists before it.
    result = network_ripple.estimate_ripple("AAA", dt.date(2024, 1, 14), 0.0, 30.0)
    assert result is None
