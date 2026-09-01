"""Rung 5 (D6, D12) — a small PyTorch LSTM.

Deliberately small (CLAUDE.md "keep models small"): one LSTM layer, hidden
size 16, a handful of epochs, CPU-only. Input is the short lag-ordered
sequence `models.dataset.build_sequence_matrix` builds from already-computed
`_lag_{h}h` columns (all `min_lag_hours = None` — historical, safe at every
horizon per ADR-011), not a raw hourly window — this is what makes rung 5
architecturally distinct from every flat-feature-vector rung above it while
still training on leakage-safe, already-tested inputs. The zone one-hot is
concatenated to the LSTM's final hidden state before the linear head, the
same "give every pooled model a zone signal" treatment `dataset._with_zone_
dummies` gives Ridge/RF/LightGBM.

Normalization is fit on the train split only (the same rule
`evaluation/scaling.py` states and `models/linear.py`'s `StandardScaler`
enforces) — per-channel mean/std computed from `train_x` alone, applied
unchanged to `test_x`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from numpy.typing import NDArray
from torch import nn


@dataclass(frozen=True)
class _ChannelStats:
    mean: torch.Tensor  # (n_channels,)
    std: torch.Tensor


@dataclass(frozen=True)
class _ScalarStats:
    mean: float
    std: float


def _fit_channel_stats(train_x: NDArray[np.float32]) -> _ChannelStats:
    flat = train_x.reshape(-1, train_x.shape[-1])
    mean = flat.mean(axis=0)
    std = flat.std(axis=0)
    std[std == 0] = 1.0
    return _ChannelStats(
        mean=torch.tensor(mean, dtype=torch.float32),
        std=torch.tensor(std, dtype=torch.float32),
    )


def _normalize(x: NDArray[np.float32], stats: _ChannelStats) -> torch.Tensor:
    tensor = torch.tensor(x, dtype=torch.float32)
    normalized: torch.Tensor = (tensor - stats.mean) / stats.std
    return normalized


def _fit_scalar_stats(train_y: NDArray[np.float32]) -> _ScalarStats:
    std = float(train_y.std())
    return _ScalarStats(mean=float(train_y.mean()), std=std if std > 0 else 1.0)


class _SmallLSTM(nn.Module):
    def __init__(self, n_channels: int, n_zone_features: int, hidden_size: int) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_channels, hidden_size=hidden_size, batch_first=True
        )
        self.head = nn.Linear(hidden_size + n_zone_features, 1)

    def forward(self, x_seq: torch.Tensor, x_zone: torch.Tensor) -> torch.Tensor:
        _, (h_n, _) = self.lstm(x_seq)
        last_hidden = h_n[-1]
        combined = torch.cat([last_hidden, x_zone], dim=1)
        output: torch.Tensor = self.head(combined).squeeze(-1)
        return output


class LSTMModel:
    name = "lstm"

    def __init__(
        self,
        hidden_size: int = 16,
        epochs: int = 8,
        batch_size: int = 256,
        learning_rate: float = 1e-3,
        random_state: int = 42,
    ) -> None:
        self.hidden_size = hidden_size
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.random_state = random_state
        self._net: _SmallLSTM | None = None
        self._stats: _ChannelStats | None = None
        self._y_stats: _ScalarStats | None = None

    def fit(
        self,
        train_x: NDArray[np.float32],
        train_zone: NDArray[np.float32],
        train_y: NDArray[np.float32],
    ) -> LSTMModel:
        torch.manual_seed(self.random_state)
        self._stats = _fit_channel_stats(train_x)
        # Target normalization (train-only, same rule as the input channels
        # and as `evaluation/scaling.py`): raw AQI is on a 0-500 scale, and an
        # untrained net predicting near zero against that scale starts so far
        # from the target that Adam at a small fixed lr never recovers in a
        # handful of epochs (session 5 observed this directly — RMSE ~136,
        # worse than predicting the mean, before this fix).
        self._y_stats = _fit_scalar_stats(train_y)
        x_seq = _normalize(train_x, self._stats)
        x_zone = torch.tensor(train_zone, dtype=torch.float32)
        y = torch.tensor(
            (train_y - self._y_stats.mean) / self._y_stats.std, dtype=torch.float32
        )

        net = _SmallLSTM(
            n_channels=train_x.shape[-1],
            n_zone_features=train_zone.shape[-1],
            hidden_size=self.hidden_size,
        )
        optimizer = torch.optim.Adam(net.parameters(), lr=self.learning_rate)
        loss_fn = nn.MSELoss()

        n = x_seq.shape[0]
        generator = torch.Generator().manual_seed(self.random_state)
        net.train()
        for _epoch in range(self.epochs):
            permutation = torch.randperm(n, generator=generator)
            for start in range(0, n, self.batch_size):
                idx = permutation[start : start + self.batch_size]
                optimizer.zero_grad()
                pred = net(x_seq[idx], x_zone[idx])
                loss = loss_fn(pred, y[idx])
                loss.backward()
                optimizer.step()

        net.eval()
        self._net = net
        return self

    def predict(
        self, x: NDArray[np.float32], zone: NDArray[np.float32]
    ) -> NDArray[np.float64]:
        if self._net is None or self._stats is None or self._y_stats is None:
            raise RuntimeError("LSTMModel.predict called before fit")
        with torch.no_grad():
            x_seq = _normalize(x, self._stats)
            x_zone = torch.tensor(zone, dtype=torch.float32)
            pred = self._net(x_seq, x_zone)
        result: NDArray[np.float64] = (
            pred.numpy().astype(np.float64) * self._y_stats.std + self._y_stats.mean
        )
        return result
