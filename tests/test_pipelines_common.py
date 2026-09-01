"""Regression test for `fetch_zone_frame`'s per-source date windows.

feature-pipeline.yml went red because `fetch_zone_frame` passed the same
`end_date` (now + TAIL_CONTEXT_DAYS, a future date) to all three sources.
CAMS and the historical-forecast archive can serve future dates; the ERA5
*archive* cannot — it is actuals-only and 400s past "today"
(confirmed live: https://archive-api.open-meteo.com's allowed range tops out
at the current UTC date). This test locks in that the ERA5 leg's end_date is
clamped, independent of what the caller asks for.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import aqi.pipelines.common as common
from aqi.config import ZoneConfig


def _zone() -> ZoneConfig:
    city = {
        "id": "capital",
        "name_en": "capital",
        "name_ur": "capital",
        "lat": 33.0,
        "lon": 73.0,
        "timezone": "Asia/Karachi",
        "aqicn_station": "@1",
        "cams_grid": (33.0, 73.0),
        "zone": "capital",
    }
    return ZoneConfig(
        zone_id="capital",
        representative_city=city,  # type: ignore[arg-type]
        member_city_ids=("capital",),
    )


def _payload(hours: int = 2) -> dict[str, Any]:
    times = pd.date_range("2026-01-01", periods=hours, freq="h", tz="UTC")
    return {
        "hourly": {
            "time": [t.isoformat() for t in times],
            "pm2_5": [1.0] * hours,
            "temperature_2m": [1.0] * hours,
        }
    }


class TestEra5EndDateIsClamped:
    def test_future_end_date_is_not_forwarded_to_the_era5_archive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        future_end = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=4)).strftime(
            "%Y-%m-%d"
        )

        seen: dict[str, tuple[str, str]] = {}

        def fake_air_quality(
            lat: float, lon: float, *, start_date: str, end_date: str
        ) -> dict[str, Any]:
            seen["cams"] = (start_date, end_date)
            return _payload()

        def fake_weather_archive(
            lat: float, lon: float, start_date: str, end_date: str
        ) -> dict[str, Any]:
            seen["era5"] = (start_date, end_date)
            return _payload()

        def fake_hist_forecast(
            lat: float, lon: float, start_date: str, end_date: str
        ) -> dict[str, Any]:
            seen["hist_fc"] = (start_date, end_date)
            return _payload()

        monkeypatch.setattr(common, "fetch_air_quality", fake_air_quality)
        monkeypatch.setattr(common, "fetch_weather_archive", fake_weather_archive)
        monkeypatch.setattr(common, "fetch_historical_forecast", fake_hist_forecast)
        monkeypatch.setattr(
            common,
            "build_feature_frame",
            lambda *a, **k: pd.DataFrame({"time_utc": pd.to_datetime([])}),
        )

        common.fetch_zone_frame(_zone(), "2026-08-24", future_end, sleep_seconds=0.0)

        # CAMS and historical-forecast legitimately get the future-dated
        # window the caller asked for.
        assert seen["cams"] == ("2026-08-24", future_end)
        assert seen["hist_fc"] == ("2026-08-24", future_end)
        # ERA5 is actuals-only and must never receive a future end_date.
        assert seen["era5"] == ("2026-08-24", today)

    def test_past_end_date_is_forwarded_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, tuple[str, str]] = {}

        monkeypatch.setattr(
            common,
            "fetch_air_quality",
            lambda lat, lon, *, start_date, end_date: _payload(),
        )

        def fake_weather_archive(
            lat: float, lon: float, start_date: str, end_date: str
        ) -> dict[str, Any]:
            seen["era5"] = (start_date, end_date)
            return _payload()

        monkeypatch.setattr(common, "fetch_weather_archive", fake_weather_archive)
        monkeypatch.setattr(
            common,
            "fetch_historical_forecast",
            lambda lat, lon, start_date, end_date: _payload(),
        )
        monkeypatch.setattr(
            common,
            "build_feature_frame",
            lambda *a, **k: pd.DataFrame({"time_utc": pd.to_datetime([])}),
        )

        common.fetch_zone_frame(_zone(), "2020-01-01", "2020-01-31", sleep_seconds=0.0)

        assert seen["era5"] == ("2020-01-01", "2020-01-31")
