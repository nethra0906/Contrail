"""M4's learned anomaly layer (master spec §7): resample a flight segment to
128 points (ml/datasets/anomaly.py) -> 1D conv autoencoder -> 64-d embedding.
Reconstruction error (and an IsolationForest fit on the embeddings, see
ml/train/train_autoencoder.py) is the anomaly score - a segment the
autoencoder reconstructs poorly, or whose embedding sits far from the dense
region of "normal" embeddings, looks unlike the nominal flight shapes it was
trained on.

Lives in ml/models/ for the same reason trajectory_gru.py and delay_gnn.py
do: a live-serving caller needs the identical class definition.
"""

from __future__ import annotations

import torch
from torch import nn

from ml.datasets.anomaly import NUM_CHANNELS, RESAMPLE_POINTS

EMBEDDING_DIM = 64


class TrajectoryAutoencoder(nn.Module):
    """forward(x) -> (reconstruction, embedding).
    x: (batch, RESAMPLE_POINTS, NUM_CHANNELS).
    """

    def __init__(self) -> None:
        super().__init__()
        # 128 -> 64 -> 32 -> 16 (each conv halves the length; stride 2, kernel 5, padding 2).
        self.encoder = nn.Sequential(
            nn.Conv1d(NUM_CHANNELS, 16, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv1d(16, 32, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=5, stride=2, padding=2),
            nn.ReLU(),
        )
        self._encoded_len = RESAMPLE_POINTS // 8  # 16, for RESAMPLE_POINTS=128
        self.to_embedding = nn.Linear(64 * self._encoded_len, EMBEDDING_DIM)
        self.from_embedding = nn.Linear(EMBEDDING_DIM, 64 * self._encoded_len)
        self.decoder = nn.Sequential(
            nn.ConvTranspose1d(64, 32, kernel_size=5, stride=2, padding=2, output_padding=1),
            nn.ReLU(),
            nn.ConvTranspose1d(32, 16, kernel_size=5, stride=2, padding=2, output_padding=1),
            nn.ReLU(),
            nn.ConvTranspose1d(
                16, NUM_CHANNELS, kernel_size=5, stride=2, padding=2, output_padding=1
            ),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x_t = x.transpose(1, 2)  # (batch, NUM_CHANNELS, RESAMPLE_POINTS)
        encoded = self.encoder(x_t)  # (batch, 64, encoded_len)
        embedding = self.to_embedding(encoded.flatten(1))  # (batch, EMBEDDING_DIM)

        decoded_flat = self.from_embedding(embedding)
        decoded = decoded_flat.view(-1, 64, self._encoded_len)
        reconstruction = self.decoder(decoded).transpose(
            1, 2
        )  # (batch, RESAMPLE_POINTS, NUM_CHANNELS)
        return reconstruction, embedding
