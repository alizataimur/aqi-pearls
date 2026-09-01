"""Rung 4 (D6, expected champion per CLAUDE.md §12.1) — LightGBM.

Plain regression objective — quantile objectives are the conformal layer's
job (differentiator #2, cut this session; see docs/DECISIONS.md). Small fixed
hyperparameters, same cut as `models/forest.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from numpy.typing import NDArray


class LightGBMModel:
    name = "lightgbm"

    def __init__(
        self,
        n_estimators: int = 300,
        num_leaves: int = 31,
        learning_rate: float = 0.05,
        random_state: int = 42,
    ) -> None:
        self._model = LGBMRegressor(
            n_estimators=n_estimators,
            num_leaves=num_leaves,
            learning_rate=learning_rate,
            random_state=random_state,
            verbosity=-1,
        )

    def fit(self, x: pd.DataFrame, y: pd.Series[float]) -> LightGBMModel:
        self._model.fit(x, y)
        return self

    def predict(self, x: pd.DataFrame) -> pd.Series[float]:
        predictions: NDArray[np.float64] = np.asarray(
            self._model.predict(x), dtype=np.float64
        )
        return pd.Series(predictions, index=x.index)

    @property
    def sklearn_model(self) -> LGBMRegressor:
        """The raw fitted estimator — `explain/shap_explain.py`'s
        `shap.TreeExplainer` needs the real LightGBM booster, not this
        wrapper (CLAUDE.md D13)."""
        return self._model
