"""Daily target builder — the `daily_episode` family (CLAUDE.md §9.1).

`aqi_from_24h_mean`'s own docstring already fixes the definition: the daily
target is the 24-hour-mean-based AQI, maxed **across pollutant sub-indices**
for that local calendar day (CLAUDE.md §9.2), not maxed across hours. That was
decided in Stage 0 (`src/aqi/aqi_scale.py`) and is implemented here, not
re-decided.

Calendar-day boundaries are **local** (Asia/Karachi), per I7 — a daily max
computed on UTC calendar days would silently shift which hours belong to
which day.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

from aqi.aqi_scale import AQIResult, aqi_from_24h_mean, overall_aqi

# open_meteo_air.py's column name -> aqi_scale.py's pollutant key.
_POLLUTANT_COLUMNS: dict[str, str] = {
    "pm2_5": "pm2_5",
    "pm10": "pm10",
    "ozone": "o3",
    "nitrogen_dioxide": "no2",
    "sulphur_dioxide": "so2",
    "carbon_monoxide": "co",
}

# A day built from fewer than this many hourly readings is too incomplete to
# trust as a 24h mean — its target is left NaN rather than silently biased
# toward whichever hours happened to report.
MIN_HOURS_FOR_DAILY_MEAN = 18


def _local_date_column(
    time_utc: pd.Series[pd.Timestamp], timezone: str
) -> pd.Series[date]:
    return time_utc.dt.tz_convert(ZoneInfo(timezone)).dt.date


def daily_pollutant_means(cams_df: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """One row per local calendar day: mean concentration per pollutant, plus
    the hour count backing each day (so an incomplete day is visible, not
    silently averaged over fewer hours than the next)."""
    frame = cams_df.copy()
    frame["local_date"] = _local_date_column(frame["time_utc"], timezone)

    means = frame.groupby("local_date")[list(_POLLUTANT_COLUMNS)].mean()
    counts = frame.groupby("local_date").size().rename("hour_count")
    return means.join(counts)


def daily_aqi_table(cams_df: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """One row per local calendar day: `daily_aqi`, `driving_pollutant`,
    `exceeds_200`, `hour_count`."""
    means = daily_pollutant_means(cams_df, timezone)

    rows = []
    for local_date, row in means.iterrows():
        if row["hour_count"] < MIN_HOURS_FOR_DAILY_MEAN:
            rows.append(
                {
                    "local_date": local_date,
                    "daily_aqi": None,
                    "driving_pollutant": None,
                    "exceeds_200": None,
                    "hour_count": row["hour_count"],
                }
            )
            continue

        results: list[AQIResult | None] = []
        for column, pollutant in _POLLUTANT_COLUMNS.items():
            conc = row[column]
            if pd.isna(conc):
                continue
            results.append(aqi_from_24h_mean(float(conc), pollutant))

        best = overall_aqi(results)
        rows.append(
            {
                "local_date": local_date,
                "daily_aqi": best.aqi if best else None,
                "driving_pollutant": best.pollutant if best else None,
                "exceeds_200": (best.aqi > 200) if best else None,
                "hour_count": row["hour_count"],
            }
        )

    return pd.DataFrame(rows).set_index("local_date")
