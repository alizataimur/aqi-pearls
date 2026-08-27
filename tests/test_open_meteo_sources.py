"""Offline tests for the three Open-Meteo source modules.

No network calls — CI must never depend on a third party being up (I10).
`scripts/probe_sources.py` is the live check; run manually, not in `pytest`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from aqi.sources import open_meteo_air, open_meteo_hist_forecast, open_meteo_weather
from aqi.sources._http import SourceError


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


class TestAirQualityUrlConstruction:
    def test_pins_cams_global_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}

        def fake_get_json(url: str, **_: Any) -> dict[str, Any]:
            captured["url"] = url
            return {"hourly": {"time": []}}

        monkeypatch.setattr(open_meteo_air, "get_json", fake_get_json)
        open_meteo_air.fetch_air_quality(33.7, 73.1, past_days=1)

        query = _query(captured["url"])
        assert query["domains"] == ["cams_global"]
        requested = set(query["hourly"][0].split(","))
        assert requested == set(open_meteo_air.HOURLY_VARIABLES)

    def test_rejects_mixing_date_range_and_relative_window(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            open_meteo_air.fetch_air_quality(
                33.7, 73.1, start_date="2024-01-01", end_date="2024-01-02", past_days=3
            )

    def test_wraps_transport_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get_json(*_: Any, **__: Any) -> dict[str, Any]:
            raise SourceError("boom")

        monkeypatch.setattr(open_meteo_air, "get_json", fake_get_json)
        with pytest.raises(open_meteo_air.AirQualityError):
            open_meteo_air.fetch_air_quality(33.7, 73.1, past_days=1)

    def test_missing_hourly_block_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_payload = {"latitude": 33.7}
        monkeypatch.setattr(open_meteo_air, "get_json", lambda *a, **k: fake_payload)
        with pytest.raises(open_meteo_air.AirQualityError, match="no 'hourly' block"):
            open_meteo_air.fetch_air_quality(33.7, 73.1, past_days=1)


class TestAirQualityParsing:
    def test_parses_hourly_rows(self) -> None:
        payload = {
            "latitude": 33.700005,
            "longitude": 73.0,
            "hourly": {
                "time": ["2024-01-01T00:00", "2024-01-01T01:00"],
                "pm2_5": [55.0, 60.0],
                "us_aqi": [140, 150],
            },
        }
        frame = open_meteo_air.parse_air_quality(payload, city_id="islamabad")

        assert len(frame) == 2
        assert frame["city_id"].tolist() == ["islamabad", "islamabad"]
        assert frame["cams_grid_lat"].tolist() == [33.700005, 33.700005]
        assert frame["pm2_5"].tolist() == [55.0, 60.0]
        # A variable Open-Meteo didn't return for this call is NaN, not dropped.
        assert frame["dust"].isna().all()
        assert isinstance(frame["time_utc"].dtype, pd.DatetimeTZDtype)

    def test_empty_hourly_block_returns_empty_typed_frame(self) -> None:
        empty = {"hourly": {"time": []}}
        frame = open_meteo_air.parse_air_quality(empty, city_id="lahore")
        assert frame.empty
        assert "pm2_5" in frame.columns


class TestWeatherArchive:
    def test_requires_date_range_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}

        def fake_get_json(url: str, **_: Any) -> dict[str, Any]:
            captured["url"] = url
            return {"hourly": {"time": []}}

        monkeypatch.setattr(open_meteo_weather, "get_json", fake_get_json)
        open_meteo_weather.fetch_weather_archive(33.7, 73.1, "2022-08-04", "2022-08-05")

        query = _query(captured["url"])
        assert query["start_date"] == ["2022-08-04"]
        assert query["end_date"] == ["2022-08-05"]
        assert "temperature_850hPa" in query["hourly"][0].split(",")

    def test_missing_hourly_block_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(open_meteo_weather, "get_json", lambda *a, **k: {})
        with pytest.raises(open_meteo_weather.WeatherArchiveError):
            open_meteo_weather.fetch_weather_archive(
                33.7, 73.1, "2022-08-04", "2022-08-05"
            )

    def test_parse_tags_source_as_era5_archive(self) -> None:
        payload = {
            "hourly": {
                "time": ["2024-01-01T00:00"],
                "temperature_2m": [10.5],
                "temperature_850hPa": [5.0],
            }
        }
        frame = open_meteo_weather.parse_weather_hourly(
            payload, city_id="islamabad", source="era5_archive"
        )
        assert frame["source"].tolist() == ["era5_archive"]
        assert frame["temperature_850hPa"].tolist() == [5.0]


class TestHistoricalForecast:
    def test_shares_variable_list_with_era5_archive(self) -> None:
        hist_vars = open_meteo_hist_forecast.HOURLY_VARIABLES
        era5_vars = open_meteo_weather.HOURLY_VARIABLES
        assert hist_vars == era5_vars

    def test_parse_tags_source_as_historical_forecast(self) -> None:
        payload = {"hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [11.0]}}
        frame = open_meteo_hist_forecast.parse_historical_forecast(
            payload, city_id="lahore"
        )
        assert frame["source"].tolist() == ["historical_forecast"]

    def test_different_endpoint_from_era5_archive(self) -> None:
        assert open_meteo_hist_forecast.BASE_URL != open_meteo_weather.BASE_URL

    def test_missing_hourly_block_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(open_meteo_hist_forecast, "get_json", lambda *a, **k: {})
        with pytest.raises(open_meteo_hist_forecast.HistoricalForecastError):
            open_meteo_hist_forecast.fetch_historical_forecast(
                33.7, 73.1, "2024-01-01", "2024-01-02"
            )
