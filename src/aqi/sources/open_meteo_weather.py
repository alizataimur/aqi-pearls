"""Open-Meteo ERA5 Archive — backward-looking weather, what actually happened.

CLAUDE.md §8.1: this is the *actuals* source. It must never be used to
populate a feature for a target time in the future relative to the forecast's
issue time — that is exactly the leakage I1 exists to prevent. Future-dated
weather covariates come from `open_meteo_hist_forecast` instead, which serves
forecasts as they were issued rather than what ERA5 later confirmed happened.

`parse_weather_hourly` is shared with `open_meteo_hist_forecast` — both
endpoints return the same hourly-variable wire shape, only the semantics
differ (actual vs. forecast-as-issued), so the wire-format translation is one
function shared by both `source` tags rather than duplicated.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pandas as pd

from aqi.sources._http import SourceError, get_json

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# CLAUDE.md §10 weather variables, incl. the pressure-level temperature that
# feeds `inversion_proxy` (CLAUDE.md §10 physics table).
HOURLY_VARIABLES: tuple[str, ...] = (
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


class WeatherArchiveError(SourceError):
    """Raised when the ERA5 archive feed cannot be retrieved or parsed."""


def fetch_weather_archive(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    *,
    base_url: str = BASE_URL,
    attempts: int = 4,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch raw ERA5 weather actuals for one location and date range.

    Unlike the air-quality and historical-forecast endpoints, the archive has
    no `past_days`/`forecast_days` shorthand — it is actuals, so a date range
    is the only sensible way to ask for it.
    """
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "UTC",
    }
    url = f"{base_url}?{urlencode(params)}"
    try:
        payload = get_json(
            url,
            attempts=attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            timeout=timeout,
        )
    except SourceError as exc:
        raise WeatherArchiveError(str(exc)) from exc

    if "hourly" not in payload:
        raise WeatherArchiveError(f"no 'hourly' block in response: {payload}")
    return payload


def parse_weather_hourly(
    payload: dict[str, Any], *, city_id: str, source: str
) -> pd.DataFrame:
    """Raw Open-Meteo weather payload -> tidy hourly DataFrame.

    `source` distinguishes `"era5_archive"` (actuals) from
    `"historical_forecast"` (forecast as issued) — the two are never allowed
    to blend silently downstream, since which one fed a given feature row is
    exactly what I1 is checked against.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    columns = ["time_utc", "city_id", "source", *HOURLY_VARIABLES]
    if not times:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame({"time_utc": pd.to_datetime(times, utc=True)})
    for variable in HOURLY_VARIABLES:
        frame[variable] = hourly.get(variable, [None] * len(times))
    frame["city_id"] = city_id
    frame["source"] = source
    return frame[columns]
