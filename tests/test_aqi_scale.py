"""Unit tests for the EPA AQI conversion.

Every expected value here is either an exact EPA breakpoint boundary or a
hand-computed piecewise-linear interpolation. If one of these fails after a
dependency bump, the conversion changed and every downstream metric is suspect.
"""

from __future__ import annotations

import pytest

from aqi.aqi_scale import (
    aqi_from_24h_mean,
    aqi_nowcast,
    category_for,
    overall_aqi,
    ugm3_to_epa_units,
)


class TestBreakpointBoundaries:
    """Exact category boundaries, 2024 EPA revision."""

    @pytest.mark.parametrize(
        ("conc", "expected"),
        [
            (0.0, 0),
            (9.0, 50),
            (9.1, 51),
            (35.4, 100),
            (35.5, 101),
            (55.4, 150),
            (55.5, 151),
            (125.4, 200),
            (125.5, 201),
            (225.4, 300),
            (225.5, 301),
            (325.4, 500),
        ],
    )
    def test_pm25_boundaries(self, conc: float, expected: int) -> None:
        assert aqi_from_24h_mean(conc, "pm2_5").aqi == expected

    @pytest.mark.parametrize(
        ("conc", "expected"), [(54.0, 50), (154.0, 100), (254.0, 150), (604.0, 500)]
    )
    def test_pm10_boundaries(self, conc: float, expected: int) -> None:
        assert aqi_from_24h_mean(conc, "pm10").aqi == expected

    def test_2024_revision_is_actually_applied(self) -> None:
        """12.0 ug/m3 was exactly AQI 50 under the pre-2024 breakpoints.

        Under the 2024 revision it is 56. This test exists to fail loudly if
        anyone reinstates the old table — that swap would silently shift every
        number in the report and poison the AQICN comparison.
        """
        assert aqi_from_24h_mean(12.0, "pm2_5").aqi == 56

    def test_above_500_is_not_clipped(self) -> None:
        """Punjab smog exceeds the published scale; clipping destroys signal."""
        result = aqi_from_24h_mean(400.0, "pm2_5")
        assert result.aqi > 500
        assert result.exceeds_scale


class TestInterpolation:
    def test_midband_pm25(self) -> None:
        # (300-201)/(225.4-125.5) * (150.0-125.5) + 201 = 225.28 -> 225
        assert aqi_from_24h_mean(150.0, "pm2_5").aqi == 225

    def test_truncation_not_rounding(self) -> None:
        """EPA truncates concentration before indexing; 9.09 must stay in Good."""
        assert aqi_from_24h_mean(9.09, "pm2_5").aqi == 50


class TestUnitConversion:
    def test_pm_passes_through(self) -> None:
        assert ugm3_to_epa_units(42.0, "pm2_5") == 42.0

    def test_ozone_ugm3_to_ppm(self) -> None:
        # 100 ug/m3 * 24.45 / 48 / 1000 = 0.0509 ppm
        assert ugm3_to_epa_units(100.0, "o3") == pytest.approx(0.0509375, rel=1e-6)
        assert aqi_from_24h_mean(100.0, "o3").aqi == 46

    def test_co_ugm3_to_ppm(self) -> None:
        # 5000 ug/m3 -> 4.3645 ppm -> truncated 4.3 -> AQI 49
        assert aqi_from_24h_mean(5000.0, "co").aqi == 49

    def test_gas_index_differs_from_naive_ugm3(self) -> None:
        """Applying the index straight to ug/m3 is a real and common bug."""
        converted = aqi_from_24h_mean(100.0, "o3").aqi
        naive = aqi_from_24h_mean(100.0, "o3", units="epa").aqi
        assert converted != naive


class TestNowCast:
    def test_constant_series_returns_that_value(self) -> None:
        result = aqi_nowcast([20.0] * 12, "pm2_5")
        assert result is not None
        assert result.concentration == pytest.approx(20.0, abs=0.05)
        assert result.method == "nowcast"

    def test_weight_is_floored_at_half(self) -> None:
        # [100, 1 x 11] -> raw weight 0.01, floored to 0.5 -> NowCast 50.5
        result = aqi_nowcast([100.0] + [1.0] * 11, "pm2_5")
        assert result is not None
        assert result.concentration == pytest.approx(50.5, abs=0.05)
        assert result.aqi == 138

    def test_recent_hours_dominate(self) -> None:
        """A spike in the last hour must move NowCast more than a 12h-old one."""
        recent = aqi_nowcast([200.0] + [20.0] * 11, "pm2_5")
        stale = aqi_nowcast([20.0] * 11 + [200.0], "pm2_5")
        assert recent is not None and stale is not None
        assert recent.aqi > stale.aqi

    def test_insufficient_recent_data_returns_none(self) -> None:
        assert aqi_nowcast([None, None, 20.0] + [20.0] * 9, "pm2_5") is None

    def test_two_of_three_recent_is_sufficient(self) -> None:
        assert aqi_nowcast([20.0, None, 20.0] + [20.0] * 9, "pm2_5") is not None

    def test_too_short_series_returns_none(self) -> None:
        assert aqi_nowcast([20.0, 20.0], "pm2_5") is None

    def test_nowcast_differs_from_raw_hourly(self) -> None:
        """The whole point: a raw hourly reading is not a 'current AQI'."""
        series = [200.0] + [20.0] * 11
        nowcast = aqi_nowcast(series, "pm2_5")
        raw = aqi_from_24h_mean(series[0], "pm2_5")
        assert nowcast is not None
        assert nowcast.aqi != raw.aqi

    def test_nan_float_is_treated_as_missing_like_none(self) -> None:
        """Regression: pandas/numpy represent a real data gap as NaN, not
        None. `_add_derived` (builder.py) feeds this function raw numpy
        window slices, and a NaN silently let through the old `v is not
        None` check propagated into the weighted average, then crashed
        `_truncate`'s `math.floor` — first caught by running the backfill
        against live CAMS history near its 2022-08-04 floor, where the lag
        window legitimately contains hours before any data exists."""
        nan = float("nan")
        with_nan = aqi_nowcast([nan, 20.0, 20.0] + [20.0] * 9, "pm2_5")
        with_none = aqi_nowcast([None, 20.0, 20.0] + [20.0] * 9, "pm2_5")
        assert with_nan is not None
        assert with_none is not None
        assert with_nan.aqi == with_none.aqi

    def test_all_nan_window_returns_none(self) -> None:
        assert aqi_nowcast([float("nan")] * 12, "pm2_5") is None


class TestCategories:
    @pytest.mark.parametrize(
        ("aqi", "expected"),
        [
            (0, "Good"),
            (50, "Good"),
            (51, "Moderate"),
            (150, "Unhealthy for Sensitive Groups"),
            (200, "Unhealthy"),
            (300, "Very Unhealthy"),
            (301, "Hazardous"),
            (750, "Hazardous"),
        ],
    )
    def test_category_names(self, aqi: int, expected: str) -> None:
        assert category_for(aqi)[0] == expected

    def test_urdu_category_present(self) -> None:
        _, urdu = category_for(250)
        assert urdu and urdu != "Very Unhealthy"


class TestOverallAQI:
    def test_max_subindex_wins_and_names_the_driver(self) -> None:
        pm25 = aqi_from_24h_mean(150.0, "pm2_5")  # 225
        pm10 = aqi_from_24h_mean(100.0, "pm10")  # ~73
        overall = overall_aqi([pm25, pm10])
        assert overall is not None
        assert overall.aqi == pm25.aqi
        assert overall.pollutant == "pm2_5"

    def test_all_missing_returns_none(self) -> None:
        assert overall_aqi([None, None]) is None

    def test_ignores_missing_subindices(self) -> None:
        pm25 = aqi_from_24h_mean(50.0, "pm2_5")
        assert overall_aqi([pm25, None]) is pm25


def test_method_is_never_ambiguous() -> None:
    """CLAUDE.md I8: every AQI value must say how it was computed."""
    assert aqi_from_24h_mean(30.0).method == "24h_mean"
    nowcast = aqi_nowcast([30.0] * 12)
    assert nowcast is not None and nowcast.method == "nowcast"
