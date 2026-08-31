"""The feature + target builder (CLAUDE.md §10, D2).

Takes the three raw hourly DataFrames session 1's source modules produce for
one city — CAMS pollutants, ERA5 actuals, historical-forecast-as-issued — and
returns one row per UTC hour (`time_utc` = the issue time `T`), with:

  * historical features: current readings, lags, rolling stats, derived AQI
    change rate, physics indices, calendar flags — every one built only from
    data timestamped `<= T`, so safe at every horizon.
  * future covariates `fc_{variable}_h{24,48,72}`: the historical-forecast
    value *as issued at T* for target time `T+h` — the mechanism that makes
    D+1/D+2/D+3 forecasting possible at all without leaking ERA5 actuals
    (CLAUDE.md I1, §8).
  * targets `target_daily_aqi_h{24,48,72}` / `target_exceeds_200_h{24,48,72}`
    — the `daily_episode` label for the local calendar day `h` hours ahead.

`temperature_850hPa` is filled from historical-forecast wherever ERA5 leaves
it NaN, which today means *every* row (ADR-009, resolved) — ERA5 archive does
not serve pressure-level output at all at these coordinates. Any other ERA5
gap gets the same treatment for the same reason: a forecast-as-issued value
for a past hour was, by definition, issued at or before that hour, so it
never violates I1 even though it isn't a true reanalysis actual.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from aqi.aqi_scale import aqi_nowcast
from aqi.features.calendar_pk import load_festival_dates
from aqi.features.physics import add_physics_features
from aqi.features.spec import expand_feature_specs, load_raw_spec
from aqi.features.targets import daily_aqi_table

_POLLUTANT_COLUMNS = (
    "pm2_5",
    "pm10",
    "ozone",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "carbon_monoxide",
    "dust",
    "aerosol_optical_depth",
)
_RENAME_POLLUTANTS = {
    "ozone": "o3",
    "nitrogen_dioxide": "no2",
    "sulphur_dioxide": "so2",
    "carbon_monoxide": "co",
}

_WEATHER_COLUMNS = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "surface_pressure",
    "precipitation",
    "cloud_cover",
    "boundary_layer_height",
    "shortwave_radiation",
    "temperature_850hPa",
)


def _reindex_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    """Full hourly UTC index so every `.shift`/`.rolling`/`.diff` offset means
    exactly what it says in hours, even across a real data gap."""
    if frame.empty:
        return frame
    full_index = pd.date_range(
        frame.index.min(), frame.index.max(), freq="h", tz="UTC", name="time_utc"
    )
    return frame.reindex(full_index)


def _merge_actuals(
    cams_df: pd.DataFrame, era5_df: pd.DataFrame, hist_forecast_df: pd.DataFrame
) -> pd.DataFrame:
    pollutants = cams_df.set_index("time_utc")[list(_POLLUTANT_COLUMNS)].rename(
        columns=_RENAME_POLLUTANTS
    )
    era5 = era5_df.set_index("time_utc")[list(_WEATHER_COLUMNS)]
    hist_fc = hist_forecast_df.set_index("time_utc")[list(_WEATHER_COLUMNS)]

    # ERA5 actuals preferred; a forecast-as-issued value fills anything ERA5
    # doesn't serve (ADR-009) — legal under I1 for t <= T (see module docstring).
    weather = era5.combine_first(hist_fc)[list(_WEATHER_COLUMNS)]

    merged = pollutants.join(weather, how="outer")
    return _reindex_hourly(merged)


def _add_time_features(frame: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Cyclical time features in **local** time — traffic, cooking and prayer
    schedules correlate with local clock time, not UTC."""
    index = pd.DatetimeIndex(frame.index)
    local = index.tz_convert(ZoneInfo(timezone))
    out = frame.copy()
    out["local_date"] = local.date

    hour = local.hour + local.minute / 60.0
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24)

    dow = local.dayofweek
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    doy = local.dayofyear
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

    month = local.month
    out["month_sin"] = np.sin(2 * np.pi * month / 12)
    out["month_cos"] = np.cos(2 * np.pi * month / 12)

    out["is_weekend"] = (dow >= 5).astype(int)  # Sat/Sun — Pakistan's weekend
    return out


def _add_lag_rolling(frame: pd.DataFrame, raw_spec: dict[str, Any]) -> pd.DataFrame:
    new_columns: dict[str, pd.Series[float]] = {}

    lags = raw_spec["lags"]
    for base in lags["base_features"]:
        for h in lags["lag_hours"]:
            new_columns[f"{base}_lag_{h}h"] = frame[base].shift(h)

    rolling = raw_spec["rolling"]
    for base in rolling["base_features"]:
        for window in rolling["windows_hours"]:
            roll = frame[base].rolling(window, min_periods=1)
            for stat in ("mean", "max", "min", "std"):
                new_columns[f"{base}_roll_{stat}_{window}h"] = getattr(roll, stat)()

    return pd.concat([frame, pd.DataFrame(new_columns, index=frame.index)], axis=1)


def _add_derived(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()

    pm25 = frame["pm2_5"].to_numpy()
    nowcast = np.full(len(pm25), np.nan)
    for i in range(len(pm25)):
        window = pm25[max(0, i - 11) : i + 1][::-1]  # most-recent-first
        result = aqi_nowcast(list(window), "pm2_5")
        if result is not None:
            nowcast[i] = result.aqi
    out["hourly_aqi_nowcast"] = nowcast

    out["aqi_change_rate_1h"] = out["hourly_aqi_nowcast"].diff(1)
    out["aqi_change_rate_3h"] = out["hourly_aqi_nowcast"].diff(3)
    out["aqi_change_rate_24h"] = out["hourly_aqi_nowcast"].diff(24)

    ratio = frame["pm2_5"] / frame["pm10"].replace(0, np.nan)
    out["pm25_pm10_ratio_roll_24h"] = ratio.rolling(24, min_periods=1).mean()
    return out


def _add_wind_components(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    radians = np.deg2rad(frame["wind_direction_10m"])
    out["wind_direction_10m_sin"] = np.sin(radians)
    out["wind_direction_10m_cos"] = np.cos(radians)
    # Meteorological convention: direction is where the wind comes FROM.
    out["wind_u_10m"] = -frame["wind_speed_10m"] * np.sin(radians)
    out["wind_v_10m"] = -frame["wind_speed_10m"] * np.cos(radians)
    return out


def _add_future_covariates(
    frame: pd.DataFrame, hist_forecast_df: pd.DataFrame, raw_spec: dict[str, Any]
) -> pd.DataFrame:
    """`fc_{variable}_h{h}` at issue time T = the historical-forecast value
    for T+h, as issued — never an ERA5 actual (I1)."""
    hist_fc = hist_forecast_df.set_index("time_utc")
    out = frame.copy()
    future = raw_spec["future_covariates"]
    for variable in future["variables"]:
        series = hist_fc[variable]
        for h in future["horizons_hours"]:
            shifted = series.copy()
            shifted.index = shifted.index - pd.Timedelta(hours=h)
            out[f"fc_{variable}_h{h}"] = shifted.reindex(out.index)
    return out


def _add_targets(
    frame: pd.DataFrame, cams_df: pd.DataFrame, timezone: str
) -> pd.DataFrame:
    daily = daily_aqi_table(cams_df, timezone)
    out = frame.copy()
    for h in (24, 48, 72):
        days_ahead = h // 24
        target_dates = pd.Series(out["local_date"]).apply(
            lambda d, n=days_ahead: d + pd.Timedelta(days=n)
        )
        aqi_lookup = target_dates.map(daily["daily_aqi"]).to_numpy()
        exceeds_lookup = target_dates.map(daily["exceeds_200"]).to_numpy()
        out[f"target_daily_aqi_h{h}"] = aqi_lookup
        out[f"target_exceeds_200_h{h}"] = exceeds_lookup
    return out


def build_feature_frame(
    cams_df: pd.DataFrame,
    era5_df: pd.DataFrame,
    hist_forecast_df: pd.DataFrame,
    *,
    city_id: str,
    timezone: str,
) -> pd.DataFrame:
    """Build the full hourly feature+target matrix for one city.

    Every input DataFrame is expected to already be filtered to this
    `city_id` (session 1's `parse_*` functions tag every row with it).
    """
    raw_spec = load_raw_spec()
    festival_dates = load_festival_dates()

    frame = _merge_actuals(cams_df, era5_df, hist_forecast_df)
    frame = _add_time_features(frame, timezone)
    frame = _add_wind_components(frame)
    frame = _add_lag_rolling(frame, raw_spec)
    frame = _add_derived(frame)
    frame = add_physics_features(frame, festival_dates)
    frame = _add_future_covariates(frame, hist_forecast_df, raw_spec)
    frame = _add_targets(frame, cams_df, timezone)

    frame.insert(0, "city_id", city_id)
    frame.index.name = "time_utc"
    return frame


def feature_vector(
    frame: pd.DataFrame, issue_time: pd.Timestamp, horizon_hours: int
) -> dict[str, Any]:
    """The feature vector a model would actually see at `issue_time` for
    `horizon_hours` — only columns ADR-011 admits at this horizon.

    This is the function `tests/test_no_leakage.py` attacks: it is the single
    chokepoint between the full built frame (which has every horizon's future
    covariates side by side) and what a model is allowed to read.
    """
    specs = {s.name: s for s in expand_feature_specs()}
    row = frame.loc[issue_time]
    return {
        name: row[name]
        for name, spec in specs.items()
        if name in frame.columns and spec.admitted_at(horizon_hours)
    }
