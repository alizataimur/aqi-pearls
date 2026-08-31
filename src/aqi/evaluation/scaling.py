"""Fit-on-train-only scaling (CLAUDE.md §2.1 session-2 leakage check).

Dependency-free z-score scaler — deliberately not scikit-learn, which is a
Stage-3 dependency (`pyproject.toml`'s `models` extra) this session doesn't
need. Session 5's ladder is free to swap in `sklearn.preprocessing
.StandardScaler` per-model; what must survive that swap is the *rule*: fit
statistics on the training split only, then apply them unchanged to test.
Fitting on the full dataset (train+test) leaks test-set distribution
information into the training statistics — a quieter, easier-to-miss cousin
of temporal leakage, and exactly what `tests/test_no_leakage.py` checks for.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ScalerStats:
    means: pd.Series[float]
    stds: pd.Series[float]


def fit_scaler(train: pd.DataFrame, columns: list[str]) -> ScalerStats:
    """Fit mean/std on `train` only. Never call this on a frame that includes
    any test-split rows."""
    subset = train[columns]
    return ScalerStats(means=subset.mean(), stds=subset.std().replace(0, 1.0))


def apply_scaler(
    frame: pd.DataFrame, columns: list[str], stats: ScalerStats
) -> pd.DataFrame:
    """Apply previously-fit statistics to any frame (train or test) — this is
    the only function allowed to touch the test split."""
    out = frame.copy()
    out[columns] = (frame[columns] - stats.means) / stats.stds
    return out
