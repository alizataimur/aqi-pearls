"""Rungs 0a-0c (CLAUDE.md §12.1) — persistence, seasonal-naive, climatology.

First-class per I6: evaluated on the identical (city_id, time_utc) rows as
every ML rung, via the same `dataset.HorizonMatrix`-derived test index. All
three predict from the reconstructed daily series (`dataset.daily_aqi_by_date`)
rather than the hourly frame — a daily forecast baseline has no business
reading hourly noise.

Persistence and seasonal-naive have no fitted parameters — they look up an
*already-realized* daily value strictly before the issue time, which is
always legitimate at inference regardless of train/test split (CLAUDE.md I2's
train-only-fit rule governs estimated statistics, not table lookups of the
past). Climatology **does** fit a parameter (the day-of-year mean) and must
only ever see train-split days, exactly like `evaluation/scaling.py`'s rule.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from aqi.models.dataset import local_dates as _local_dates


def _date_value_table(group: pd.DataFrame) -> dict[date, float]:
    dates = group["date"].to_numpy()
    values = group["daily_aqi"].to_numpy()
    return {d: float(v) for d, v in zip(dates, values, strict=True)}


class PersistenceModel:
    """ŷ(T, h) = the most recently *completed* local day's daily AQI,
    unchanged across every horizon — "today will look like yesterday
    finished," the literal rung-0a definition (CLAUDE.md §12.1)."""

    name = "persistence"

    def __init__(self, tz_name: str) -> None:
        self.tz_name = tz_name
        self._table: dict[str, dict[date, float]] = {}

    def fit(self, daily_train: pd.DataFrame) -> PersistenceModel:
        # Persistence needs the full realized history up to each test row's
        # issue time, not just train days — see module docstring. The
        # training pipeline calls `fit` with the *full* daily series (train +
        # already-elapsed test days), which is honest: only days strictly
        # before each row's own issue time are ever looked up.
        self._table = {
            str(city_id): _date_value_table(group)
            for city_id, group in daily_train.groupby("city_id")
        }
        return self

    def predict(
        self, city_id: pd.Series[str], time_utc: pd.Series[Any], horizon_hours: int
    ) -> NDArray[np.float64]:
        del horizon_hours  # constant across horizons by definition
        local = _local_dates(time_utc, self.tz_name)
        out = np.full(len(city_id), np.nan)
        for i, (cid, local_date) in enumerate(zip(city_id, local, strict=True)):
            table = self._table.get(cid, {})
            out[i] = table.get(local_date - timedelta(days=1), np.nan)
        return out


class SeasonalNaiveModel:
    """ŷ(T, h) = the realized daily AQI exactly 7 days before the target
    date (`local_date(T) + h_days`) — "same day last week" (CLAUDE.md §12.1).
    `target_date - 7 <= T` always (h <= 3 days), so this is a pure historical
    lookup, never a peek at the target's own week."""

    name = "seasonal_naive"

    def __init__(self, tz_name: str) -> None:
        self.tz_name = tz_name
        self._table: dict[str, dict[date, float]] = {}

    def fit(self, daily_train: pd.DataFrame) -> SeasonalNaiveModel:
        self._table = {
            str(city_id): _date_value_table(group)
            for city_id, group in daily_train.groupby("city_id")
        }
        return self

    def predict(
        self, city_id: pd.Series[str], time_utc: pd.Series[Any], horizon_hours: int
    ) -> NDArray[np.float64]:
        local = _local_dates(time_utc, self.tz_name)
        horizon_days = horizon_hours // 24
        out = np.full(len(city_id), np.nan)
        for i, (cid, local_date) in enumerate(zip(city_id, local, strict=True)):
            target_date = local_date + timedelta(days=horizon_days)
            lookup_date = target_date - timedelta(days=7)
            table = self._table.get(cid, {})
            out[i] = table.get(lookup_date, np.nan)
        return out


class ClimatologyModel:
    """ŷ(T, h) = the train-only day-of-year mean daily AQI for the target
    date (CLAUDE.md §12.1). The one baseline with a fitted parameter, so it
    is the one baseline `fit` must never be called with test-split days."""

    name = "climatology"

    def __init__(self, tz_name: str) -> None:
        self.tz_name = tz_name
        self._doy_mean: dict[str, dict[int, float]] = {}
        self._fallback_mean: dict[str, float] = {}

    def fit(self, daily_train: pd.DataFrame) -> ClimatologyModel:
        doy = pd.to_datetime(daily_train["date"]).dt.dayofyear
        train = daily_train.assign(_doy=doy)
        for city_id, group in train.groupby("city_id"):
            doy_values = group["_doy"].to_numpy()
            aqi_values = group["daily_aqi"].to_numpy()
            table: dict[int, list[float]] = {}
            for d, v in zip(doy_values, aqi_values, strict=True):
                table.setdefault(int(d), []).append(float(v))
            self._doy_mean[str(city_id)] = {
                d: float(np.mean(vs)) for d, vs in table.items()
            }
            self._fallback_mean[str(city_id)] = float(group["daily_aqi"].mean())
        return self

    def predict(
        self, city_id: pd.Series[str], time_utc: pd.Series[Any], horizon_hours: int
    ) -> NDArray[np.float64]:
        local = _local_dates(time_utc, self.tz_name)
        horizon_days = horizon_hours // 24
        out = np.full(len(city_id), np.nan)
        for i, (cid, local_date) in enumerate(zip(city_id, local, strict=True)):
            target_date = local_date + timedelta(days=horizon_days)
            doy_table = self._doy_mean.get(cid, {})
            value = doy_table.get(target_date.timetuple().tm_yday)
            out[i] = value if value is not None else self._fallback_mean.get(cid, np.nan)
        return out
