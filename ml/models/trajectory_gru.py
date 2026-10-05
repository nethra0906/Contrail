"""M1's model: a 2-layer GRU (hidden size 128) over the per-step kinematic
features ml/datasets/trajectory.py builds, with quantile heads
({0.1, 0.5, 0.9}) for (dE, dN, dAlt) at each of HORIZONS_S - master spec §7.

Lives in ml/models/ (previously empty except __init__.py - every other
model so far was small enough to stay inline in its train script; this one
is factored out because services/inference/trajectory.py needs the same
class definition to load the exported weights for live serving, and
duplicating a model's architecture between a training script and a serving
module is exactly the kind of drift execution rule 5 exists to prevent).
"""

from __future__ import annotations

import torch
from torch import nn

from ml.datasets.trajectory import HORIZONS_S, NUM_STEP_FEATURES, STATIC_DIM

NUM_QUANTILES = 3  # {0.1, 0.5, 0.9} - fixed, matches QUANTILES below
QUANTILES = (0.1, 0.5, 0.9)
TARGET_DIMS = 3  # dE, dN, dAlt
HIDDEN_SIZE = 128
NUM_GRU_LAYERS = 2


class TrajectoryGRU(nn.Module):
    """forward(x, static) -> (batch, len(HORIZONS_S), NUM_QUANTILES, TARGET_DIMS).

    `x`: (batch, WINDOW_SIZE-1, NUM_STEP_FEATURES) - the per-step kinematic
    sequence. `static`: (batch, STATIC_DIM) - one-hot phase + WTC context,
    concatenated to the GRU's final hidden state rather than broadcast into
    every timestep, since it doesn't change within a 55-60s window.
    """

    def __init__(self) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=NUM_STEP_FEATURES,
            hidden_size=HIDDEN_SIZE,
            num_layers=NUM_GRU_LAYERS,
            batch_first=True,
        )
        head_input_dim = HIDDEN_SIZE + STATIC_DIM
        head_output_dim = len(HORIZONS_S) * NUM_QUANTILES * TARGET_DIMS
        self.head = nn.Sequential(
            nn.Linear(head_input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, head_output_dim),
        )

    def forward(self, x: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        _, h_n = self.gru(x)
        last_hidden = h_n[-1]  # (batch, HIDDEN_SIZE) - top layer's final hidden state
        combined = torch.cat([last_hidden, static], dim=-1)
        raw = self.head(combined)
        return raw.view(-1, len(HORIZONS_S), NUM_QUANTILES, TARGET_DIMS)


def pinball_loss_tensor(pred: torch.Tensor, target: torch.Tensor, quantile: float) -> torch.Tensor:
    diff = target - pred
    return torch.maximum(quantile * diff, (quantile - 1) * diff)


def masked_quantile_loss(
    output: torch.Tensor, y: torch.Tensor, y_mask: torch.Tensor
) -> torch.Tensor:
    """output: (batch, H, 3, 3). y: (batch, H, 3). y_mask: (batch, H) bool.

    Averages pinball loss across quantiles and target dims, masked per
    horizon so a window with no valid long-horizon target (see
    ml/datasets/trajectory.py's build_arrays docstring) contributes zero
    loss for that horizon instead of being trained against a fabricated
    value or dropped from the batch entirely.
    """
    y_expanded = y.unsqueeze(2)  # (batch, H, 1, 3) broadcasts against quantile dim
    losses = []
    for q_i, q in enumerate(QUANTILES):
        losses.append(pinball_loss_tensor(output[:, :, q_i, :], y_expanded[:, :, 0, :], q))
    per_horizon = torch.stack(losses, dim=2).mean(dim=(2, 3))  # (batch, H)

    mask_f = y_mask.float()
    denom = mask_f.sum()
    if denom.item() == 0:
        return per_horizon.sum() * 0.0  # degenerate empty-batch case, keeps autograd graph valid
    return (per_horizon * mask_f).sum() / denom
