"""Rung 3 (D12) — SARIMAX with exogenous variables, statsmodels.

Runs at **daily** granularity, one row per (zone, local calendar day), not
hourly — `target_daily_aqi_h{h}` is already constant within a calendar day
(`builder._add_targets`), so a SARIMAX fit at hourly resolution would just be
1000x the compute for the same information. Exogenous regressors are the
horizon's own admitted `fc_{variable}_h{h}` future covariates (ADR-011),
averaged over the issuing day — the same leakage-safe mechanism every other
rung reads, never an ERA5 actual (I1). Averaging within a day is safe: every
hourly value going into the mean was itself issued at or before its own hour,
and this rung treats "issued that day" as the coarser unit its daily
granularity actually operates at.

Fit once on train-only days, then extended through the test period with
`SARIMAXResults.append(..., refit=False)` — a Kalman-filter state update using
the already-estimated parameters, not a re-optimization per test day. That
keeps this small and fast (CLAUDE.md's "keep models small" instruction) while
still producing genuine one-step-ahead-in-state-space predictions: each test
day's `fittedvalues` entry uses only the state built from days strictly
before it, per `statsmodels`' own definition of filtered prediction.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

EXOG_VARIABLES = ("temperature_2m", "boundary_layer_height", "wind_speed_10m")
MIN_TRAIN_DAYS = 30


def _daily_series(
    frame: pd.DataFrame, horizon_hours: int
) -> tuple[pd.DataFrame, list[str]]:
    target_col = f"target_daily_aqi_h{horizon_hours}"
    fc_cols = [f"fc_{v}_h{horizon_hours}" for v in EXOG_VARIABLES]
    working = frame[["city_id", "local_date", target_col, *fc_cols]].copy()
    daily = (
        working.groupby(["city_id", "local_date"], as_index=False).agg(
            {target_col: "first", **{c: "mean" for c in fc_cols}}
        )
    ).rename(columns={target_col: "endog", "local_date": "date"})
    daily = daily.dropna(subset=["endog", *fc_cols]).sort_values(["city_id", "date"])
    return daily.reset_index(drop=True), fc_cols


class SarimaxModel:
    name = "sarimax"

    def __init__(self, order: tuple[int, int, int] = (2, 0, 1)) -> None:
        self.order = order
        self._fc_cols: list[str] = []

    def fit_predict_daily(
        self,
        frame: pd.DataFrame,
        horizon_hours: int,
        train_end_date: date,
        test_start_date: date,
        test_end_date: date,
    ) -> pd.DataFrame:
        """Returns one row per (city_id, date) with `yhat`, for every test
        date each zone had enough train history to fit — the training
        pipeline broadcasts this back onto hourly test rows by
        `(city_id, local_date)`."""
        daily, fc_cols = _daily_series(frame, horizon_hours)
        self._fc_cols = fc_cols
        predictions = []
        for city_id, group in daily.groupby("city_id"):
            # A plain RangeIndex, not a DatetimeIndex: real dropna'd gaps
            # (the BLH/forecast-archive gaps, docs/STATE.md) make the daily
            # series non-contiguous in calendar terms, and statsmodels'
            # `.append()` demands its new index literally extend the fitted
            # model's date frequency — it doesn't tolerate gaps. With no
            # seasonal_order component, this rung's AR/MA terms already mean
            # "the previous *available* day," not "the previous calendar
            # day," so a positional index changes nothing about what the
            # model represents, only how statsmodels indexes it.
            group = group.sort_values("date").reset_index(drop=True)
            train_rows = group[group["date"] <= train_end_date]
            test_rows = group[
                (group["date"] >= test_start_date) & (group["date"] <= test_end_date)
            ]
            if len(train_rows) < MIN_TRAIN_DAYS or test_rows.empty:
                continue

            train_endog = train_rows["endog"].reset_index(drop=True)
            train_exog = train_rows[fc_cols].reset_index(drop=True)
            model = SARIMAX(
                train_endog,
                exog=train_exog,
                order=self.order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            # method="powell": the default lbfgs path calls
            # scipy.optimize.fmin_l_bfgs_b(..., disp=...) internally, and this
            # environment's scipy (newer than statsmodels==0.14.4 was tested
            # against) removed that parameter — see docs/DECISIONS.md.
            # Powell avoids that call path entirely.
            fitted = model.fit(disp=False, method="powell")

            test_index = pd.RangeIndex(
                len(train_endog), len(train_endog) + len(test_rows)
            )
            test_endog = test_rows["endog"].reset_index(drop=True)
            test_endog.index = test_index
            test_exog = pd.DataFrame(
                test_rows[fc_cols].to_numpy(), columns=fc_cols, index=test_index
            )
            extended = fitted.append(test_endog, exog=test_exog, refit=False)
            test_fitted = extended.fittedvalues.loc[test_index]
            predictions.append(
                pd.DataFrame(
                    {
                        "city_id": city_id,
                        "date": test_rows["date"].to_numpy(),
                        "yhat": test_fitted.to_numpy(),
                    }
                )
            )
        if not predictions:
            return pd.DataFrame(columns=["city_id", "date", "yhat"])
        return pd.concat(predictions, ignore_index=True)
