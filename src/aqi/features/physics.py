"""Punjab winter-smog physics features (CLAUDE.md §10).

Each earns its place via correlation with PM2.5 spikes in
`notebooks/03_physics_features.ipynb` (session 4) — a feature that shows
nothing is a documented, tried-and-rejected finding, not silently dropped.

All functions take a tidy hourly DataFrame (one row per UTC hour, columns as
named below) and a `local_date` column (a `datetime.date`, computed by the
caller from `time_utc` in the city's timezone — CLAUDE.md I7) and return new
columns. No function reaches outside its own row/rolling window, so nothing
here can violate I1 on its own; the caller is responsible for only handing
this module data timestamped `<= T`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from aqi.features.calendar_pk import crop_burning_window, heating_season, is_festival

_EPSILON = 1.0


def inversion_proxy(frame: pd.DataFrame) -> pd.Series[float]:
    """`temperature_850hPa - temperature_2m`. Positive = inversion capping the
    boundary layer — the mechanism behind Punjab winter smog."""
    return frame["temperature_850hPa"] - frame["temperature_2m"]


def stagnation_index(frame: pd.DataFrame) -> pd.Series[float]:
    """Rolling-24h low wind x high humidity x low boundary-layer height.

    Each component is squashed to roughly [0, 1] and multiplied — high only
    when all three conditions (stale air, moist, capped) hold at once. This is
    an engineering-judgment composite, not a published index; that is exactly
    what the session-4 correlation check is for.
    """
    # min_periods=24 (full window, explicit): a 24h mean straddling the BLH
    # source gap must come out NaN, not a value computed from the one or two
    # real points min_periods=1 would have allowed through looking exactly
    # like a genuine 24h average. See boundary_layer_height_is_missing /
    # stagnation_index_is_missing in add_physics_features.
    wind_24h = frame["wind_speed_10m"].rolling(24, min_periods=24).mean()
    blh_24h = frame["boundary_layer_height"].rolling(24, min_periods=24).mean()
    humidity_24h = frame["relative_humidity_2m"].rolling(24, min_periods=24).mean()

    low_wind = 1.0 / (_EPSILON + wind_24h)
    low_blh = 1.0 / (_EPSILON + blh_24h)
    high_humidity = humidity_24h / 100.0
    return low_wind * low_blh * high_humidity


def ventilation_index(frame: pd.DataFrame) -> pd.Series[float]:
    """`boundary_layer_height * wind_speed_10m` — standard dispersion capacity."""
    return frame["boundary_layer_height"] * frame["wind_speed_10m"]


def wind_from_sector(frame: pd.DataFrame) -> pd.DataFrame:
    """One-hot of the upwind sector (meteorological convention: the direction
    the wind is blowing FROM)."""
    degrees = frame["wind_direction_10m"] % 360
    return pd.DataFrame(
        {
            "wind_from_sector_N": ((degrees >= 315) | (degrees < 45)).astype(int),
            "wind_from_sector_E": ((degrees >= 45) & (degrees < 135)).astype(int),
            "wind_from_sector_S": ((degrees >= 135) & (degrees < 225)).astype(int),
            "wind_from_sector_W": ((degrees >= 225) & (degrees < 315)).astype(int),
        },
        index=frame.index,
    )


def calendar_flags(frame: pd.DataFrame, festival_dates: set[date]) -> pd.DataFrame:
    """`crop_burning_season`, `crop_burning_day_count`, `festival_flag`,
    `heating_season` — from `local_date`, never a day-of-year formula."""
    local_dates: list[date] = list(frame["local_date"])
    windows = [crop_burning_window(d) for d in local_dates]
    return pd.DataFrame(
        {
            "crop_burning_season": [int(in_window) for in_window, _ in windows],
            "crop_burning_day_count": [count for _, count in windows],
            "festival_flag": [int(is_festival(d, festival_dates)) for d in local_dates],
            "heating_season": [int(heating_season(d)) for d in local_dates],
        },
        index=frame.index,
    )


def add_physics_features(frame: pd.DataFrame, festival_dates: set[date]) -> pd.DataFrame:
    """Add every §10 physics column to a copy of `frame`."""
    out = frame.copy()
    out["inversion_proxy"] = inversion_proxy(frame)
    out["stagnation_index"] = stagnation_index(frame)
    out["ventilation_index"] = ventilation_index(frame)

    # boundary_layer_height has a real, confirmed source gap (2024-01-01 to
    # 2024-06-30, both zones — see notebooks/03_physics_features.ipynb). With
    # min_periods now requiring a full window (builder.py, stagnation_index
    # above), stagnation_index/ventilation_index go genuinely NaN across that
    # gap rather than being silently filled from a near-empty window. These
    # flags let a tree learn "dispersion unknown" instead of being fed
    # whatever a later imputation step fills in as if it were a measurement.
    blh_missing = frame["boundary_layer_height"].isna()
    out["boundary_layer_height_is_missing"] = blh_missing.astype(int)
    out["stagnation_index_is_missing"] = out["stagnation_index"].isna().astype(int)
    out["ventilation_index_is_missing"] = out["ventilation_index"].isna().astype(int)

    out = pd.concat([out, wind_from_sector(frame)], axis=1)
    out = pd.concat([out, calendar_flags(frame, festival_dates)], axis=1)
    return out
