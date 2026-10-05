"""M3's model (master spec §7, "*** powers the flagship ***"): a diffusion
graph convolution + temporal encoder - a spatio-temporal GCN. Predicts mean
arrival delay per airport at each of ml.datasets.network.HORIZONS_BUCKETS.

"Diffusion graph convolution": each layer propagates every node's features
to its neighbors, weighted by the (already row-normalized) adjacency matrix
- repeating this K times lets information travel K hops across the graph in
one forward pass, which is the actual mechanism that makes a delay at one
airport visible to a connected airport's prediction (the thing a plain
per-node model, like the LightGBM baseline with only one hop of "neighbor
delay," cannot do). Two separate adjacency matrices (flow, rotation) are
diffused and combined, since they carry different kinds of propagation
(route volume vs. the much tighter aircraft-rotation coupling the spec
calls out as "the mechanism that makes cascades realistic").

Lives in ml/models/ (not inline in the train script) for the same reason
trajectory_gru.py does: a live-serving caller needs the identical class
definition to load trained weights.
"""

from __future__ import annotations

import torch
from torch import nn

from ml.datasets.network import HORIZONS_BUCKETS, NUM_NODE_FEATURES

DIFFUSION_HOPS = 2
GRAPH_HIDDEN = 32
TEMPORAL_HIDDEN = 64


class DiffusionGraphConv(nn.Module):
    """One diffusion-convolution layer over a FIXED adjacency (the graph
    structure doesn't change between forward passes - it's data-derived
    once in ml/datasets/network.py, not learned or batch-dependent).
    """

    def __init__(self, in_dim: int, out_dim: int, hops: int = DIFFUSION_HOPS) -> None:
        super().__init__()
        self.hops = hops
        # One linear transform per hop per adjacency (flow, rotation) plus
        # a self-transform (hop 0, identity propagation) - concatenated
        # then projected down, rather than summed, so the model can learn
        # to weight near vs. far propagation differently instead of being
        # forced to treat every hop as equally important.
        self.self_lin = nn.Linear(in_dim, out_dim)
        self.flow_lins = nn.ModuleList([nn.Linear(in_dim, out_dim) for _ in range(hops)])
        self.rotation_lins = nn.ModuleList([nn.Linear(in_dim, out_dim) for _ in range(hops)])
        self.combine = nn.Linear(out_dim * (1 + 2 * hops), out_dim)

    def forward(
        self, x: torch.Tensor, flow_adj: torch.Tensor, rotation_adj: torch.Tensor
    ) -> torch.Tensor:
        """x: (batch, N, in_dim). flow_adj/rotation_adj: (N, N)."""
        parts = [self.self_lin(x)]
        flow_prop = x
        rotation_prop = x
        for k in range(self.hops):
            flow_prop = torch.einsum("ij,bjf->bif", flow_adj, flow_prop)
            rotation_prop = torch.einsum("ij,bjf->bif", rotation_adj, rotation_prop)
            parts.append(self.flow_lins[k](flow_prop))
            parts.append(self.rotation_lins[k](rotation_prop))
        combined = torch.cat(parts, dim=-1)
        return torch.relu(self.combine(combined))


class DelayGNN(nn.Module):
    """forward(x_seq, flow_adj, rotation_adj) -> (batch, N, len(HORIZONS_BUCKETS)).

    x_seq: (batch, SEQUENCE_LENGTH, N, NUM_NODE_FEATURES) - a trailing
    window of node features per airport. Each timestep is diffusion-
    convolved independently (spatial mixing), then a shared-weight GRU runs
    per node across the time dimension (temporal mixing) - the "hybrid"
    spatio-temporal structure the spec names, built from the two primitives
    above rather than a third-party graph-learning library.
    """

    def __init__(self) -> None:
        super().__init__()
        self.graph_conv = DiffusionGraphConv(NUM_NODE_FEATURES, GRAPH_HIDDEN)
        self.temporal = nn.GRU(
            input_size=GRAPH_HIDDEN, hidden_size=TEMPORAL_HIDDEN, batch_first=True
        )
        self.head = nn.Sequential(
            nn.Linear(TEMPORAL_HIDDEN, 32),
            nn.ReLU(),
            nn.Linear(32, len(HORIZONS_BUCKETS)),
        )

    def forward(
        self, x_seq: torch.Tensor, flow_adj: torch.Tensor, rotation_adj: torch.Tensor
    ) -> torch.Tensor:
        batch, seq_len, n_nodes, _ = x_seq.shape
        conv_out = []
        for t in range(seq_len):
            conv_out.append(self.graph_conv(x_seq[:, t, :, :], flow_adj, rotation_adj))
        conv_seq = torch.stack(conv_out, dim=1)  # (batch, seq_len, N, GRAPH_HIDDEN)

        # Fold node into batch so the GRU runs once with shared weights
        # across every node, rather than N separate GRUs.
        reshaped = conv_seq.permute(0, 2, 1, 3).reshape(batch * n_nodes, seq_len, GRAPH_HIDDEN)
        _, h_n = self.temporal(reshaped)
        last_hidden = h_n[-1]  # (batch * N, TEMPORAL_HIDDEN)

        out = self.head(last_hidden)  # (batch * N, len(HORIZONS_BUCKETS))
        return out.view(batch, n_nodes, len(HORIZONS_BUCKETS))
