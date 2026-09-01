"""Unit tests for the feature + target builder (D2).

No network — everything here runs on synthetic hourly fixtures shaped like
session 1's `parse_*` output. `tests/test_no_leakage.py` is the dedicated I1
attack; this file is about correctness of the computation itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from aqi.features.builder import build_feature_frame
from aqi.features.calendar_pk import (
    crop_burning_window,
    heating_season,
    is_festival,
    load_festival_dates,
)
from aqi.features.physics import (
    add_physics_features,
    inversion_proxy,
    stagnation_index,
    ventilation_index,
    wind_from_sector,
)
from aqi.features.spec import expand_feature_specs, load_raw_spec, target_column_names
from aqi.features.targets import daily_aqi_table


def _synthetic_raw(n_days: int = 30, start: datetime | None = None, seed: int = 0):
    start = start or datetime(2024, 9, 1, tzinfo=UTC)
    n = n_days * 24
    times = pd.to_datetime([start + timedelta(hours=i) for i in range(n)], utc=True)
    rng = np.random.default_rng(seed)

    cams = pd.DataFrame(
        {
            "time_utc": times,
            "city_id": "islamabad",
            "cams_grid_lat": 33.7,
            "cams_grid_lon": 73.0,
            "pm2_5": rng.uniform(20, 200, n),
            "pm10": rng.uniform(40, 300, n),
            "nitrogen_dioxide": rng.uniform(5, 40, n),
            "sulphur_dioxide": rng.uniform(1, 20, n),
            "ozone": rng.uniform(10, 60, n),
            "carbon_monoxide": rng.uniform(200, 900, n),
            "dust": rng.uniform(0, 50, n),
            "aerosol_optical_depth": rng.uniform(0, 1, n),
            "us_aqi": rng.uniform(50, 200, n),
            "us_aqi_pm2_5": rng.uniform(50, 200, n),
        }
    )
    weather_common = {
        "time_utc": times,
        "city_id": "islamabad",
        "temperature_2m": rng.uniform(10, 30, n),
        "relative_humidity_2m": rng.uniform(20, 90, n),
        "dew_point_2m": rng.uniform(0, 20, n),
        "wind_speed_10m": rng.uniform(0, 15, n),
        "wind_direction_10m": rng.uniform(0, 360, n),
        "wind_gusts_10m": rng.uniform(0, 25, n),
        "surface_pressure": rng.uniform(950, 1020, n),
        "precipitation": rng.uniform(0, 5, n),
        "cloud_cover": rng.uniform(0, 100, n),
        "boundary_layer_height": rng.uniform(100, 2000, n),
        "shortwave_radiation": rng.uniform(0, 800, n),
    }
    era5 = pd.DataFrame({**weather_common, "source": "era5_archive"})
    era5["temperature_850hPa"] = np.nan  # ADR-009: ERA5 archive never has this
    hist_fc = pd.DataFrame({**weather_common, "source": "historical_forecast"})
    hist_fc["temperature_850hPa"] = rng.uniform(-5, 20, n)
    return cams, era5, hist_fc


class TestCalendarPk:
    def test_loads_festival_dates(self) -> None:
        dates = load_festival_dates()
        assert date(2024, 4, 10) in dates  # Eid al-Fitr 2024

    def test_is_festival(self) -> None:
        dates = {date(2024, 4, 10)}
        assert is_festival(date(2024, 4, 10), dates)
        assert not is_festival(date(2024, 4, 11), dates)

    def test_crop_burning_window(self) -> None:
        assert crop_burning_window(date(2024, 10, 14)) == (False, 0)
        assert crop_burning_window(date(2024, 10, 15)) == (True, 1)
        assert crop_burning_window(date(2024, 11, 30)) == (True, 47)
        assert crop_burning_window(date(2024, 12, 1)) == (False, 0)

    def test_heating_season_spans_new_year(self) -> None:
        assert heating_season(date(2024, 12, 15))
        assert heating_season(date(2025, 1, 15))
        assert heating_season(date(2025, 2, 15))
        assert not heating_season(date(2025, 2, 16))
        assert not heating_season(date(2024, 11, 30))


class TestPhysics:
    def test_inversion_proxy_is_temperature_difference(self) -> None:
        frame = pd.DataFrame({"temperature_850hPa": [5.0], "temperature_2m": [15.0]})
        assert inversion_proxy(frame).tolist() == [-10.0]

    def test_ventilation_index_is_product(self) -> None:
        frame = pd.DataFrame({"boundary_layer_height": [500.0], "wind_speed_10m": [4.0]})
        assert ventilation_index(frame).tolist() == [2000.0]

    def test_stagnation_index_highest_when_still_humid_capped(self) -> None:
        # 24 constant rows, not 1: stagnation_index's rolling means now require
        # a full 24h window (min_periods=window, session 4) so a window
        # straddling a source gap comes out NaN instead of a near-empty-window
        # value. A single-row frame would just be that NaN case.
        calm = pd.DataFrame(
            {
                "wind_speed_10m": [0.5] * 24,
                "boundary_layer_height": [50.0] * 24,
                "relative_humidity_2m": [95.0] * 24,
            }
        )
        windy = pd.DataFrame(
            {
                "wind_speed_10m": [15.0] * 24,
                "boundary_layer_height": [2000.0] * 24,
                "relative_humidity_2m": [20.0] * 24,
            }
        )
        assert stagnation_index(calm).iloc[-1] > stagnation_index(windy).iloc[-1]

    def test_wind_from_sector_is_one_hot(self) -> None:
        frame = pd.DataFrame({"wind_direction_10m": [0.0, 90.0, 180.0, 270.0]})
        sectors = wind_from_sector(frame)
        assert sectors.loc[0, "wind_from_sector_N"] == 1
        assert sectors.loc[1, "wind_from_sector_E"] == 1
        assert sectors.loc[2, "wind_from_sector_S"] == 1
        assert sectors.loc[3, "wind_from_sector_W"] == 1
        assert sectors.sum(axis=1).tolist() == [1, 1, 1, 1]

    def test_add_physics_features_adds_every_column(self) -> None:
        cams, era5, hist_fc = _synthetic_raw(n_days=5)
        merged = era5.combine_first(hist_fc).set_index("time_utc")
        merged["local_date"] = merged.index.date
        out = add_physics_features(merged, {date(2024, 9, 3)})
        for col in (
            "inversion_proxy",
            "stagnation_index",
            "ventilation_index",
            "crop_burning_season",
            "festival_flag",
            "heating_season",
        ):
            assert col in out.columns


class TestTargets:
    def test_daily_aqi_table_shape_and_range(self) -> None:
        cams, _, _ = _synthetic_raw(n_days=10)
        table = daily_aqi_table(cams, "Asia/Karachi")
        assert len(table) >= 9  # boundary days may be partial
        complete = table.dropna(subset=["daily_aqi"])
        assert (complete["daily_aqi"] >= 0).all()
        assert complete["driving_pollutant"].notna().all()

    def test_incomplete_day_is_nan_not_biased(self) -> None:
        cams, _, _ = _synthetic_raw(n_days=2)
        # Drop all but 3 hours of the second day - too few to trust.
        cutoff = cams["time_utc"].iloc[0] + pd.Timedelta(hours=27)
        sparse = cams[cams["time_utc"] < cutoff]
        table = daily_aqi_table(sparse, "Asia/Karachi")
        last_day = table.iloc[-1]
        assert last_day["hour_count"] < 18
        assert pd.isna(last_day["daily_aqi"])

    def test_known_pm25_gives_known_aqi(self) -> None:
        # 30-day flat pm2_5=12.0 ug/m3 -> AQI 56 (ADR-004's own regression value).
        n = 24 * 3
        times = pd.date_range("2024-09-01", periods=n, freq="h", tz="UTC")
        cams = pd.DataFrame(
            {
                "time_utc": times,
                "pm2_5": 12.0,
                "pm10": 0.0,
                "nitrogen_dioxide": 0.0,
                "sulphur_dioxide": 0.0,
                "ozone": 0.0,
                "carbon_monoxide": 0.0,
            }
        )
        table = daily_aqi_table(cams, "Asia/Karachi")
        middle_day = table.iloc[1]
        assert middle_day["daily_aqi"] == 56
        assert middle_day["driving_pollutant"] == "pm2_5"
        assert not middle_day["exceeds_200"]


class TestBuilderSchema:
    """D2 evidence: conf/features.yaml and the builder's real output must
    agree exactly, or the spec is documentation nobody can trust."""

    @pytest.fixture(scope="class")
    def frame(self) -> pd.DataFrame:
        cams, era5, hist_fc = _synthetic_raw(n_days=20)
        return build_feature_frame(
            cams, era5, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )

    def test_every_declared_feature_is_a_real_column(self, frame: pd.DataFrame) -> None:
        declared = {s.name for s in expand_feature_specs()}
        missing = declared - set(frame.columns)
        assert not missing, f"declared in features.yaml but never built: {missing}"

    def test_every_declared_target_is_a_real_column(self, frame: pd.DataFrame) -> None:
        missing = set(target_column_names()) - set(frame.columns)
        assert not missing

    def test_no_undeclared_feature_columns(self, frame: pd.DataFrame) -> None:
        declared = {s.name for s in expand_feature_specs()}
        targets = set(target_column_names())
        # Known, deliberate extras: identifiers/join keys and the raw
        # pre-transform wind direction (only its sin/cos/u/v are "features").
        allowed_extra = {"city_id", "local_date", "wind_direction_10m"}
        undeclared = set(frame.columns) - declared - targets - allowed_extra
        assert not undeclared, f"columns not declared anywhere: {undeclared}"

    def test_temperature_850hpa_is_never_all_nan(self, frame: pd.DataFrame) -> None:
        # The whole point of ADR-009's resolution.
        assert frame["temperature_850hPa"].notna().any()

    def test_lag_and_rolling_counts_match_spec(self, frame: pd.DataFrame) -> None:
        raw = load_raw_spec()
        expected_lags = len(raw["lags"]["base_features"]) * len(raw["lags"]["lag_hours"])
        expected_rolling = (
            len(raw["rolling"]["base_features"])
            * len(raw["rolling"]["windows_hours"])
            * len(raw["rolling"]["stats"])
        )
        lag_cols = [c for c in frame.columns if "_lag_" in c]
        roll_bases = tuple(f"{base}_roll_" for base in raw["rolling"]["base_features"])
        roll_cols = [c for c in frame.columns if c.startswith(roll_bases)]
        assert len(lag_cols) == expected_lags
        assert len(roll_cols) == expected_rolling
