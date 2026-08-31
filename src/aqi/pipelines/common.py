"""Fetch-three-sources-and-build, shared by the backfill and hourly pipelines.

Both `backfill.py` (one calendar month at a time) and `feature_pipeline.py`
(one rolling window at a time) do exactly the same three-source fetch and
call the same `build_feature_frame` — the only difference is which date
range they ask for and which rows they keep. Factored out once here so that
never drifts into two subtly different implementations.
"""

from __future__ import annotations

import time

import pandas as pd

from aqi.config import ZoneConfig
from aqi.features.builder import build_feature_frame
from aqi.sources._http import SourceError
from aqi.sources.open_meteo_air import fetch_air_quality, parse_air_quality
from aqi.sources.open_meteo_hist_forecast import (
    fetch_historical_forecast,
    parse_historical_forecast,
)
from aqi.sources.open_meteo_weather import fetch_weather_archive, parse_weather_hourly


def fetch_zone_frame(
    zone: ZoneConfig,
    start_date: str,
    end_date: str,
    *,
    sleep_seconds: float = 2.0,
) -> pd.DataFrame:
    """CAMS + ERA5 + historical-forecast for `[start_date, end_date]` (UTC
    calendar dates, inclusive) -> the full built feature+target frame.

    A sleep between each of the three calls, per CLAUDE.md §8.3 — getting
    throttled during submission week is a self-inflicted outage.
    """
    cams_payload = fetch_air_quality(
        zone.lat, zone.lon, start_date=start_date, end_date=end_date
    )
    time.sleep(sleep_seconds)
    era5_payload = fetch_weather_archive(zone.lat, zone.lon, start_date, end_date)
    time.sleep(sleep_seconds)
    hist_fc_payload = fetch_historical_forecast(zone.lat, zone.lon, start_date, end_date)
    time.sleep(sleep_seconds)

    cams_df = parse_air_quality(cams_payload, city_id=zone.zone_id)
    era5_df = parse_weather_hourly(
        era5_payload, city_id=zone.zone_id, source="era5_archive"
    )
    hist_fc_df = parse_historical_forecast(hist_fc_payload, city_id=zone.zone_id)

    if cams_df.empty or era5_df.empty or hist_fc_df.empty:
        raise SourceError(
            f"{zone.zone_id}: one or more sources returned no rows for "
            f"{start_date}..{end_date}"
        )

    return build_feature_frame(
        cams_df, era5_df, hist_fc_df, city_id=zone.zone_id, timezone=zone.timezone
    )
