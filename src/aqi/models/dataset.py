"""Feature-Store -> training matrix, for every rung of the ladder (D5, D12).

Everything here reads *only* from the feature store (never re-fetches a raw
source) — D5's brief is "fetches historical (features, targets) from the
Feature Store," and re-deriving inputs from `src/aqi/sources/` here would
silently violate that contract even if the numbers came out the same.

One `Split` (CLAUDE.md I2, `evaluation/splits.py`) is built directly rather
than via `walk_forward_splits`'s n-equal-chunks logic: ADR-016 already fixed
the held-out window as the 2025-26 smog season, and session 5's brief asks
for exactly **one** expanding-window split with the 72h purge gap, not a
multi-fold sweep. `train_mask`/`test_mask` (session 2's real leakage-tested
functions) are reused unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from aqi.config import get_config, get_zones
from aqi.evaluation.splits import MIN_PURGE_GAP_HOURS, Split, test_mask, train_mask
from aqi.features.spec import admitted_columns
from aqi.store import get_store
from aqi.store.base import FeatureStore

SMOG_SEASON_START_LOCAL = (2025, 10, 1)  # ADR-016
SMOG_SEASON_END_LOCAL_EXCLUSIVE = (2026, 3, 1)

# The lag-column base features (conf/features.yaml `lags.base_features`) are
# the sequence channels session 5's small LSTM (rung 5) reads — see
# models/deep.py. Declared here, not there, because it's a *data* choice
# (which columns form the sequence) rather than a model-architecture one.
LSTM_SEQUENCE_BASE_FEATURES = (
    "pm2_5",
    "pm10",
    "o3",
    "no2",
    "so2",
    "co",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "boundary_layer_height",
)
LSTM_SEQUENCE_LAG_HOURS = (168, 48, 24, 12, 6, 3, 1)  # oldest -> newest, then "now"


def load_ladder_frame(store: FeatureStore | None = None) -> pd.DataFrame:
    """Both zones' full history, one row per (city_id, time_utc). Reads via
    `get_store()` (I10) so `FEATURE_STORE_BACKEND` picks Hopsworks or Parquet
    the same way every other pipeline does — never hardcoded here."""
    store = store or get_store()
    cfg = get_config()
    frame = store.read(
        cfg.store.feature_group,
        pd.Timestamp("2000-01-01", tz="UTC"),
        pd.Timestamp.now(tz="UTC"),
    )
    if frame.empty:
        raise ValueError("feature store returned no rows — has the backfill run?")
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    return frame.sort_values(["city_id", "time_utc"]).reset_index(drop=True)


def daily_aqi_by_date(frame: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct `date -> (daily_aqi, exceeds_200)` per zone from the
    already-built `target_daily_aqi_h24` column, since that column *is*
    "daily_aqi of local_date + 1 day" for every row in a given calendar day
    (constant within the day — `builder._add_targets` computes it once per
    `local_date`). No raw CAMS refetch (see module docstring).

    Used only to build the rule-based baselines (`models/baselines.py`) and
    SARIMAX's daily endog series — every other rung reads the hourly frame
    directly via `admitted_feature_columns`.
    """
    rows = []
    for city_id, group in frame.groupby("city_id"):
        daily = group.groupby("local_date").first()
        dates = pd.to_datetime(pd.Series(daily.index)) + pd.Timedelta(days=1)
        rows.append(
            pd.DataFrame(
                {
                    "city_id": city_id,
                    "date": dates.dt.date.to_numpy(),
                    "daily_aqi": daily["target_daily_aqi_h24"].to_numpy(),
                    "exceeds_200": daily["target_exceeds_200_h24"].to_numpy(),
                }
            )
        )
    out = pd.concat(rows, ignore_index=True)
    return out.dropna(subset=["daily_aqi"]).drop_duplicates(subset=["city_id", "date"])


def local_timezone() -> str:
    """The one Pakistan timezone shared by both zones (I7) — used by every
    rung that needs to reason about a *local calendar date* (baselines,
    SARIMAX's daily series, the split boundary below)."""
    zones = get_zones()
    timezones = {z.timezone for z in zones}
    if len(timezones) != 1:
        raise ValueError(f"expected one shared timezone across zones, got {timezones}")
    return next(iter(timezones))


def smog_season_split(purge_gap_hours: int = MIN_PURGE_GAP_HOURS) -> Split:
    """The single split session 5 evaluates every rung on (ADR-016): test =
    the full 2025-26 local smog season, train = everything before it minus
    the 72h purge gap (CLAUDE.md I2)."""
    if purge_gap_hours < MIN_PURGE_GAP_HOURS:
        raise ValueError(f"purge_gap_hours must be >= {MIN_PURGE_GAP_HOURS}")
    tz = ZoneInfo(local_timezone())
    start_year, start_month, start_day = SMOG_SEASON_START_LOCAL
    end_year, end_month, end_day = SMOG_SEASON_END_LOCAL_EXCLUSIVE
    test_start = pd.Timestamp(
        year=start_year, month=start_month, day=start_day, tz=tz
    ).tz_convert("UTC")
    test_end_exclusive = pd.Timestamp(
        year=end_year, month=end_month, day=end_day, tz=tz
    ).tz_convert("UTC")
    test_end = test_end_exclusive - pd.Timedelta(hours=1)
    train_end = test_start - pd.Timedelta(hours=purge_gap_hours)
    return Split(train_end=train_end, test_start=test_start, test_end=test_end)


def local_dates(time_utc: pd.Series[Any], tz_name: str) -> pd.Series[Any]:
    """UTC timestamps -> local calendar dates (I7) — shared by the baselines,
    SARIMAX's daily aggregation, and the training pipeline's SARIMAX-to-row
    broadcast, so all three agree on the same day boundary."""
    tz = ZoneInfo(tz_name)
    return pd.to_datetime(time_utc, utc=True).dt.tz_convert(tz).dt.date


def daily_split_bounds(split: Split, tz_name: str) -> tuple[date, date, date]:
    """`Split`'s UTC hourly boundaries -> local calendar dates, for the
    daily-granularity rungs (baselines' train/test cut, SARIMAX)."""
    tz = ZoneInfo(tz_name)
    train_end_date = split.train_end.tz_convert(tz).date()
    test_start_date = split.test_start.tz_convert(tz).date()
    test_end_date = split.test_end.tz_convert(tz).date()
    return train_end_date, test_start_date, test_end_date


@dataclass(frozen=True)
class HorizonMatrix:
    horizon_hours: int
    feature_columns: list[str]
    train_x: pd.DataFrame
    train_y: pd.Series[float]
    test_x: pd.DataFrame
    test_y: pd.Series[float]
    test_city_id: pd.Series[str]
    test_time_utc: pd.Series[Any]


def _with_zone_dummies(x: pd.DataFrame, city_id: pd.Series[str]) -> pd.DataFrame:
    """Zone one-hot appended for the pooled-across-zones rungs (Ridge, RF,
    LightGBM, LSTM) — the rule-based/SARIMAX rungs are fit per zone instead
    (see models/baselines.py, models/sarimax.py), so they don't need this."""
    dummies = pd.get_dummies(city_id, prefix="zone", dtype=float)
    return pd.concat([x.reset_index(drop=True), dummies.reset_index(drop=True)], axis=1)


def build_horizon_matrix(frame: pd.DataFrame, horizon_hours: int) -> HorizonMatrix:
    """Admitted-at-`horizon_hours` columns (ADR-011), rows with any NaN
    feature or NaN target dropped, split via `smog_season_split` — the same
    (train_x, test_x) shape handed to every pooled rung so "the identical
    window" (CLAUDE.md §12.1) means identical *rows*, not just identical
    dates."""
    target_col = f"target_daily_aqi_h{horizon_hours}"
    feature_cols = admitted_columns(frame.columns, horizon_hours)

    split = smog_season_split()
    train_rows = train_mask(pd.DatetimeIndex(frame["time_utc"]), split)
    test_rows = test_mask(pd.DatetimeIndex(frame["time_utc"]), split)

    def _clean(mask: NDArray[np.bool_]) -> pd.DataFrame:
        subset = frame.loc[mask, ["city_id", "time_utc", *feature_cols, target_col]]
        return subset.dropna(subset=[*feature_cols, target_col])

    train = _clean(train_rows)
    test = _clean(test_rows)
    if train.empty or test.empty:
        raise ValueError(f"horizon {horizon_hours}h: empty train or test after dropna")

    train_x = _with_zone_dummies(train[feature_cols], train["city_id"])
    test_x = _with_zone_dummies(test[feature_cols], test["city_id"])
    # Zone dummies must line up even if one split happens to be single-zone.
    for col in set(train_x.columns) - set(test_x.columns):
        test_x[col] = 0.0
    for col in set(test_x.columns) - set(train_x.columns):
        train_x[col] = 0.0
    test_x = test_x[train_x.columns]

    return HorizonMatrix(
        horizon_hours=horizon_hours,
        feature_columns=list(train_x.columns),
        train_x=train_x.reset_index(drop=True),
        train_y=train[target_col].reset_index(drop=True),
        test_x=test_x.reset_index(drop=True),
        test_y=test[target_col].reset_index(drop=True),
        test_city_id=test["city_id"].reset_index(drop=True),
        test_time_utc=test["time_utc"].reset_index(drop=True),
    )


@dataclass(frozen=True)
class SequenceMatrix:
    horizon_hours: int
    base_features: tuple[str, ...]
    lag_steps: int
    train_x: NDArray[np.float32]  # (n, lag_steps+1, n_base_features)
    train_zone: NDArray[np.float32]  # (n, n_zones) one-hot
    train_y: NDArray[np.float32]
    test_x: NDArray[np.float32]
    test_zone: NDArray[np.float32]
    test_y: NDArray[np.float32]
    test_city_id: pd.Series[str]
    test_time_utc: pd.Series[Any]
    zone_columns: list[str]


def build_sequence_matrix(frame: pd.DataFrame, horizon_hours: int) -> SequenceMatrix:
    """The small PyTorch LSTM's (rung 5) input: a short lag-ordered sequence
    per base feature, built from the already-computed `_lag_{h}h` columns
    (`models.dataset.LSTM_SEQUENCE_BASE_FEATURES` /
    `..._LAG_HOURS`) rather than re-deriving a raw hourly window — every
    column used has `min_lag_hours = None` (ADR-011: historical, safe at
    every horizon), so this can never leak a future value regardless of
    `horizon_hours`.
    """
    target_col = f"target_daily_aqi_h{horizon_hours}"
    lag_cols = [
        f"{base}_lag_{h}h"
        for base in LSTM_SEQUENCE_BASE_FEATURES
        for h in LSTM_SEQUENCE_LAG_HOURS
    ]
    now_cols = list(LSTM_SEQUENCE_BASE_FEATURES)
    needed = [*lag_cols, *now_cols, target_col]

    split = smog_season_split()
    train_rows = train_mask(pd.DatetimeIndex(frame["time_utc"]), split)
    test_rows = test_mask(pd.DatetimeIndex(frame["time_utc"]), split)

    def _clean(mask: NDArray[np.bool_]) -> pd.DataFrame:
        subset = frame.loc[mask, ["city_id", "time_utc", *needed]]
        return subset.dropna(subset=needed)

    train = _clean(train_rows)
    test = _clean(test_rows)
    if train.empty or test.empty:
        raise ValueError(f"horizon {horizon_hours}h: empty train or test after dropna")

    zone_dummies_train = pd.get_dummies(train["city_id"], prefix="zone", dtype=float)
    zone_dummies_test = pd.get_dummies(test["city_id"], prefix="zone", dtype=float)
    for col in set(zone_dummies_train.columns) - set(zone_dummies_test.columns):
        zone_dummies_test[col] = 0.0
    for col in set(zone_dummies_test.columns) - set(zone_dummies_train.columns):
        zone_dummies_train[col] = 0.0
    zone_dummies_test = zone_dummies_test[zone_dummies_train.columns]

    def _sequence(subset: pd.DataFrame) -> NDArray[np.float32]:
        base_features = LSTM_SEQUENCE_BASE_FEATURES
        steps = [
            np.stack(
                [subset[f"{base}_lag_{h}h"].to_numpy() for base in base_features], axis=1
            )
            for h in LSTM_SEQUENCE_LAG_HOURS
        ]
        steps.append(
            np.stack([subset[base].to_numpy() for base in base_features], axis=1)
        )
        result: NDArray[np.float32] = np.stack(steps, axis=1).astype(np.float32)
        return result  # (n, steps, n_base)

    return SequenceMatrix(
        horizon_hours=horizon_hours,
        base_features=LSTM_SEQUENCE_BASE_FEATURES,
        lag_steps=len(LSTM_SEQUENCE_LAG_HOURS) + 1,
        train_x=_sequence(train),
        train_zone=zone_dummies_train.to_numpy(dtype=np.float32),
        train_y=train[target_col].to_numpy(dtype=np.float32),
        test_x=_sequence(test),
        test_zone=zone_dummies_test.to_numpy(dtype=np.float32),
        test_y=test[target_col].to_numpy(dtype=np.float32),
        test_city_id=test["city_id"].reset_index(drop=True),
        test_time_utc=test["time_utc"].reset_index(drop=True),
        zone_columns=list(zone_dummies_train.columns),
    )
