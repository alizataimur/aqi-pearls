"""Rung 2 (D6) — Random Forest, scikit-learn.

Small fixed hyperparameters (CLAUDE.md §4 — hyperparameter search beyond a
small fixed grid is cut). No scaling needed (tree splits are scale-invariant).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor


class RandomForestModel:
    name = "random_forest"

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 12,
        random_state: int = 42,
    ) -> None:
        self._model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            n_jobs=-1,
            random_state=random_state,
        )

    def fit(self, x: pd.DataFrame, y: pd.Series[float]) -> RandomForestModel:
        self._model.fit(x, y)
        return self

    def predict(self, x: pd.DataFrame) -> pd.Series[float]:
        predictions: NDArray[np.float64] = np.asarray(
            self._model.predict(x), dtype=np.float64
        )
        return pd.Series(predictions, index=x.index)
