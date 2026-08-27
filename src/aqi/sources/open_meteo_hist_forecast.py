"""Open-Meteo Historical Forecast archive — the leakage-safe future covariate.

This is the source that makes I1 satisfiable at all. When building a training
row that issues a forecast at time `T` for target `T+h`, the weather feature
for `T+h` may not be ERA5's *actual* value — that value did not exist at issue
time, and using it is leakage that inflates every metric downstream into
fiction (CLAUDE.md I1). This endpoint instead serves the operational forecast
**as it was issued**, so a backfilled training row sees exactly what a
forecast consumer would have seen at the time, not what later turned out to be
true.

Same wire shape as `open_meteo_weather`'s ERA5 archive, so it reuses that
module's variable list and parser — see that module's docstring for why they
are shared rather than duplicated. The two are tagged with a different
`source` value and must never be blended without that tag surviving.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pandas as pd

from aqi.sources._http import SourceError, get_json
from aqi.sources.open_meteo_weather import HOURLY_VARIABLES, parse_weather_hourly

BASE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"


class HistoricalForecastError(SourceError):
    """Raised when the historical-forecast feed cannot be retrieved or parsed."""


def fetch_historical_forecast(
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
    """Fetch raw forecast-as-issued weather for one location and date range.

    Same variable list as the ERA5 archive (`open_meteo_weather`) so the two
    are directly comparable — that comparison is what lets the feature builder
    (Stage 2) later quantify how wrong the forecast covariates typically are,
    which is itself diagnostic information, not just a leakage guard.
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
        raise HistoricalForecastError(str(exc)) from exc

    if "hourly" not in payload:
        raise HistoricalForecastError(f"no 'hourly' block in response: {payload}")
    return payload


def parse_historical_forecast(payload: dict[str, Any], *, city_id: str) -> pd.DataFrame:
    """Raw historical-forecast payload -> tidy hourly DataFrame.

    Tags every row `source="historical_forecast"` so a downstream join can
    never silently mix forecast-as-issued rows with ERA5 actuals.
    """
    return parse_weather_hourly(payload, city_id=city_id, source="historical_forecast")
