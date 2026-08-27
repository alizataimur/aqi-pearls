"""Open-Meteo Air Quality (CAMS) — pollutant history, and the training labels.

CLAUDE.md §8.1: CAMS is the only free source with long, gap-free, consistent
history at these coordinates, so it — not AQICN — is what training labels and
pollutant features are built from. AQICN stations remain ground truth and the
incumbent benchmark; this module never touches the ledger.

`domains` is pinned to `cams_global` explicitly rather than left as `auto`
(CLAUDE.md §8.2) so a silent Open-Meteo domain change can never quietly change
what "history" means between two backfill runs.

Column names below are Open-Meteo's own API parameter names (e.g.
`nitrogen_dioxide`, not `no2`) — deliberately left unrenamed here so this
module stays a faithful, boring translation of the wire format. Mapping onto
CLAUDE.md §10's feature names is the feature builder's job (Stage 2), not
this one's.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pandas as pd

from aqi.sources._http import SourceError, get_json

BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# CLAUDE.md §10 pollutants, plus the two us_aqi comparison series (I8: a
# comparison series, never the target definition).
HOURLY_VARIABLES: tuple[str, ...] = (
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "carbon_monoxide",
    "dust",
    "aerosol_optical_depth",
    "us_aqi",
    "us_aqi_pm2_5",
)


class AirQualityError(SourceError):
    """Raised when the CAMS air-quality feed cannot be retrieved or parsed."""


def fetch_air_quality(
    lat: float,
    lon: float,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
    domain: str = "cams_global",
    base_url: str = BASE_URL,
    attempts: int = 4,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch raw CAMS pollutant data for one location.

    Pass either a `start_date`/`end_date` range (backfill) or
    `past_days`/`forecast_days` (a rolling capture) — never both; Open-Meteo
    would silently prefer one and the caller should not have to guess which.
    """
    if (start_date or end_date) and (past_days is not None or forecast_days is not None):
        raise ValueError(
            "pass either start_date/end_date or past_days/forecast_days, not both"
        )

    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_VARIABLES),
        "domains": domain,
        "timezone": "UTC",
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if past_days is not None:
        params["past_days"] = past_days
    if forecast_days is not None:
        params["forecast_days"] = forecast_days

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
        raise AirQualityError(str(exc)) from exc

    if "hourly" not in payload:
        raise AirQualityError(f"no 'hourly' block in response: {payload}")
    return payload


def parse_air_quality(payload: dict[str, Any], *, city_id: str) -> pd.DataFrame:
    """Raw CAMS payload -> tidy hourly DataFrame, one row per UTC timestamp.

    A timestamp with a missing sample comes through as NaN rather than being
    dropped — the gap is itself information for the D4 coverage report, and
    silently dropping it would make a hole in the data look like a hole that
    was never there.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    columns = ["time_utc", "city_id", "cams_grid_lat", "cams_grid_lon", *HOURLY_VARIABLES]
    if not times:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame({"time_utc": pd.to_datetime(times, utc=True)})
    for variable in HOURLY_VARIABLES:
        frame[variable] = hourly.get(variable, [None] * len(times))
    frame["city_id"] = city_id
    frame["cams_grid_lat"] = payload.get("latitude")
    frame["cams_grid_lon"] = payload.get("longitude")
    return frame[columns]
