"""Expanding-window walk-forward splits with a purge gap (CLAUDE.md I2).

Implemented once, here, and used by every model — session 5's ladder reuses
this rather than re-implementing splitting inside a model module (CLAUDE.md
§12.2). No `shuffle=True`, no `train_test_split` on rows, no k-fold: all three
would let overlapping target windows bleed between train and test.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

MIN_PURGE_GAP_HOURS = 72  # CLAUDE.md I2 — >= the largest horizon (72h, D+3).

_SMOG_SEASON_MONTHS = {10, 11, 12, 1, 2}  # Oct-Feb, CLAUDE.md I2.


@dataclass(frozen=True)
class Split:
    train_end: pd.Timestamp
    """Last timestamp admitted to training — already purge-adjusted."""
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def walk_forward_splits(
    index: pd.DatetimeIndex,
    *,
    n_splits: int = 5,
    purge_gap_hours: int = MIN_PURGE_GAP_HOURS,
) -> list[Split]:
    """Expanding-window folds over `index`'s timestamps.

    Each fold's test window is one of `n_splits` roughly-equal later chunks;
    every fold's training set is everything before that chunk, minus a
    `purge_gap_hours` buffer immediately before the test window starts — the
    guard against a target window near the boundary overlapping into test.
    """
    if purge_gap_hours < MIN_PURGE_GAP_HOURS:
        raise ValueError(
            f"purge_gap_hours must be >= {MIN_PURGE_GAP_HOURS} (CLAUDE.md I2), "
            f"got {purge_gap_hours}"
        )
    unique_sorted = pd.DatetimeIndex(sorted(pd.DatetimeIndex(index).unique()))
    if len(unique_sorted) < n_splits + 1:
        raise ValueError(
            f"need at least {n_splits + 1} distinct timestamps for {n_splits} "
            f"splits, got {len(unique_sorted)}"
        )

    fold_chunks = np.array_split(unique_sorted, n_splits + 1)
    purge = pd.Timedelta(hours=purge_gap_hours)

    splits = []
    for i in range(1, n_splits + 1):
        test_chunk = fold_chunks[i]
        test_start, test_end = test_chunk.min(), test_chunk.max()
        train_end = test_start - purge
        splits.append(
            Split(train_end=train_end, test_start=test_start, test_end=test_end)
        )
    return splits


def train_mask(index: pd.DatetimeIndex, split: Split) -> NDArray[np.bool_]:
    return pd.DatetimeIndex(index) <= split.train_end


def test_mask(index: pd.DatetimeIndex, split: Split) -> NDArray[np.bool_]:
    dt_index = pd.DatetimeIndex(index)
    return (dt_index >= split.test_start) & (dt_index <= split.test_end)


def contains_full_smog_season(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """True when [start, end] spans every smog-season month (Oct-Feb) at least
    once — CLAUDE.md I2's requirement for the final held-out test period."""
    months_present = {
        (start + pd.Timedelta(days=d)).month for d in range(int((end - start).days) + 1)
    }
    return _SMOG_SEASON_MONTHS.issubset(months_present)
