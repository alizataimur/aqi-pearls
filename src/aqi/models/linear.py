"""Rung 1 (D6) — Ridge regression, scikit-learn.

`StandardScaler` inside the `Pipeline` is fit only by `Pipeline.fit(train_x,
train_y)` — never sees `test_x` — which is `evaluation/scaling.py`'s
train-only-fit *rule* satisfied via sklearn's own idiom rather than a second,
parallel scaler (see that module's docstring: session 5 is free to make this
swap, provided the rule survives it).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def build_ridge_pipeline(alpha: float = 1.0, random_state: int = 42) -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=alpha, random_state=random_state)),
        ]
    )


class RidgeModel:
    name = "ridge"

    def __init__(self, alpha: float = 1.0, random_state: int = 42) -> None:
        self._pipeline = build_ridge_pipeline(alpha=alpha, random_state=random_state)

    def fit(self, x: pd.DataFrame, y: pd.Series[float]) -> RidgeModel:
        self._pipeline.fit(x, y)
        return self

    def predict(self, x: pd.DataFrame) -> pd.Series[float]:
        predictions: NDArray[np.float64] = np.asarray(
            self._pipeline.predict(x), dtype=np.float64
        )
        return pd.Series(predictions, index=x.index)
