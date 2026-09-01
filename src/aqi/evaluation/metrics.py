"""D7 — RMSE, MAE, R2 per horizon, plus a simple precision/recall/F1 table at
AQI > 200 (CLAUDE.md §12.4, scoped down for session 5 — episode CSI/FAR/lead
time are differentiator #1, cut this session, see docs/DECISIONS.md).

Dependency-free (plain numpy) so this module has no opinion about which
model produced `y_pred` — every ladder rung, baseline or ML, is scored by
the same two functions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class RegressionMetrics:
    rmse: float
    mae: float
    r2: float
    n: int

    def to_dict(self) -> dict[str, float | int]:
        return {"rmse": self.rmse, "mae": self.mae, "r2": self.r2, "n": self.n}


@dataclass(frozen=True)
class EpisodeMetrics:
    precision: float
    recall: float
    f1: float
    n_true_positive_days: int
    n_predicted_positive_days: int
    n: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "n_true_positive_days": self.n_true_positive_days,
            "n_predicted_positive_days": self.n_predicted_positive_days,
            "n": self.n,
        }


def _clean_pair(
    y_true: NDArray[np.float64], y_pred: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Drop any pair where either side is NaN — a model that can't produce a
    prediction for a row must not be scored as if it silently guessed 0."""
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    return y_true[mask], y_pred[mask]


def regression_metrics(
    y_true: NDArray[np.float64] | list[float], y_pred: NDArray[np.float64] | list[float]
) -> RegressionMetrics:
    true_arr = np.asarray(y_true, dtype=float)
    pred_arr = np.asarray(y_pred, dtype=float)
    true_arr, pred_arr = _clean_pair(true_arr, pred_arr)
    if true_arr.size == 0:
        raise ValueError("no non-NaN (y_true, y_pred) pairs to score")

    errors = pred_arr - true_arr
    rmse = float(np.sqrt(np.mean(errors**2)))
    mae = float(np.mean(np.abs(errors)))

    ss_res = float(np.sum(errors**2))
    ss_tot = float(np.sum((true_arr - true_arr.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return RegressionMetrics(rmse=rmse, mae=mae, r2=r2, n=int(true_arr.size))


def episode_precision_recall_f1(
    y_true: NDArray[np.float64] | list[float],
    y_pred: NDArray[np.float64] | list[float],
    *,
    threshold: float = 200.0,
) -> EpisodeMetrics:
    """Precision/recall/F1 on the binary event "daily max AQI > threshold",
    derived from the continuous forecast and continuous ground truth rather
    than a separately-modelled classifier (CLAUDE.md §12.4's simplest cut)."""
    true_arr = np.asarray(y_true, dtype=float)
    pred_arr = np.asarray(y_pred, dtype=float)
    true_arr, pred_arr = _clean_pair(true_arr, pred_arr)
    if true_arr.size == 0:
        raise ValueError("no non-NaN (y_true, y_pred) pairs to score")

    true_positive_flag = true_arr > threshold
    pred_positive_flag = pred_arr > threshold

    true_positives = int(np.sum(true_positive_flag & pred_positive_flag))
    n_predicted_positive = int(np.sum(pred_positive_flag))
    n_true_positive = int(np.sum(true_positive_flag))

    precision = true_positives / n_predicted_positive if n_predicted_positive > 0 else 0.0
    recall = true_positives / n_true_positive if n_true_positive > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    )

    return EpisodeMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        n_true_positive_days=n_true_positive,
        n_predicted_positive_days=n_predicted_positive,
        n=int(true_arr.size),
    )
