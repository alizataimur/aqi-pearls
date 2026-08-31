"""I1's mechanical enforcement (CLAUDE.md §5, §10; docs/RUNBOOK.md §2.1).

Never skipped, never weakened (CLAUDE.md I1's own words). Four independent
checks:

  1. Empirical sentinel-corruption: build the feature vector for (T, h),
     corrupt every *actual* reading timestamped after T, rebuild, and assert
     every historical feature is bit-identical. A positive control (corrupt
     data at-or-before T instead) proves the test isn't vacuously passing.
  2. `min_lag_hours` admission (ADR-011): a future covariate built for one
     horizon is never admitted into another horizon's feature vector.
  3. The walk-forward splitter enforces a >= 72h purge gap (I2).
  4. A scaler fit on train only never reflects test-split statistics.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from aqi.evaluation.scaling import fit_scaler
from aqi.evaluation.splits import MIN_PURGE_GAP_HOURS, walk_forward_splits
from aqi.features.builder import build_feature_frame, feature_vector
from aqi.features.spec import expand_feature_specs

# Large and positive: aqi_scale.py's breakpoint lookup rejects negative
# concentrations outright (a good sign that guard works), and its
# above-the-scale fallback (CLAUDE.md ADR-003) handles an oversized positive
# value gracefully instead of raising.
SENTINEL = 888_888.0


def _synthetic_raw(n_days: int, seed: int = 0):
    start = datetime(2024, 9, 1, tzinfo=UTC)
    n = n_days * 24
    times = pd.to_datetime([start + timedelta(hours=i) for i in range(n)], utc=True)
    rng = np.random.default_rng(seed)

    cams = pd.DataFrame(
        {
            "time_utc": times,
            "city_id": "islamabad",
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
    era5["temperature_850hPa"] = np.nan
    hist_fc = pd.DataFrame({**weather_common, "source": "historical_forecast"})
    hist_fc["temperature_850hPa"] = rng.uniform(-5, 20, n)
    return cams, era5, hist_fc


def _corrupt_actuals_after(cams, era5, cutoff: pd.Timestamp):
    """Overwrite every numeric actual reading strictly after `cutoff` with a
    sentinel. `hist_fc` is deliberately left untouched — it is *supposed* to
    describe times after `cutoff`, that is the entire reason it exists."""
    cams = cams.copy()
    era5 = era5.copy()
    numeric_cams = cams.select_dtypes("number").columns
    numeric_era5 = era5.select_dtypes("number").columns
    cams.loc[cams["time_utc"] > cutoff, numeric_cams] = SENTINEL
    era5.loc[era5["time_utc"] > cutoff, numeric_era5] = SENTINEL
    return cams, era5


def _corrupt_actuals_at_or_before(cams, era5, cutoff: pd.Timestamp):
    cams = cams.copy()
    era5 = era5.copy()
    numeric_cams = cams.select_dtypes("number").columns
    numeric_era5 = era5.select_dtypes("number").columns
    cams.loc[cams["time_utc"] <= cutoff, numeric_cams] = SENTINEL
    era5.loc[era5["time_utc"] <= cutoff, numeric_era5] = SENTINEL
    return cams, era5


class TestSentinelCorruption:
    """The primary I1 guard — empirical, not metadata-based."""

    @pytest.fixture(scope="class")
    def raw(self):
        return _synthetic_raw(n_days=25)

    @pytest.fixture(scope="class")
    def issue_time(self, raw):
        cams, _, _ = raw
        return cams["time_utc"].iloc[len(cams) // 2]

    def test_historical_features_unchanged_by_future_corruption(self, raw, issue_time):
        cams, era5, hist_fc = raw
        horizon = 24

        baseline = build_feature_frame(
            cams, era5, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )
        fv_before = feature_vector(baseline, issue_time, horizon)

        cams_c, era5_c = _corrupt_actuals_after(cams, era5, issue_time)
        corrupted = build_feature_frame(
            cams_c, era5_c, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )
        fv_after = feature_vector(corrupted, issue_time, horizon)

        assert fv_before.keys() == fv_after.keys()
        moved = []
        for name in fv_before:
            a, b = fv_before[name], fv_after[name]
            both_nan = (a is None or (isinstance(a, float) and np.isnan(a))) and (
                b is None or (isinstance(b, float) and np.isnan(b))
            )
            if both_nan:
                continue
            if not np.isclose(a, b, equal_nan=True):
                moved.append((name, a, b))
        assert not moved, f"columns that read the future: {moved}"

    def test_positive_control_past_corruption_does_move_features(self, raw, issue_time):
        """If corrupting the past *didn't* change anything, the test above
        would be vacuous — this proves the builder actually reads its input."""
        cams, era5, hist_fc = raw
        horizon = 24

        baseline = build_feature_frame(
            cams, era5, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )
        fv_before = feature_vector(baseline, issue_time, horizon)

        cams_c, era5_c = _corrupt_actuals_at_or_before(cams, era5, issue_time)
        corrupted = build_feature_frame(
            cams_c, era5_c, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )
        fv_after = feature_vector(corrupted, issue_time, horizon)

        changed = sum(
            1
            for name in fv_before
            if not np.isclose(fv_before[name], fv_after[name], equal_nan=True)
        )
        assert changed > 0, "corrupting the past changed nothing — the test is vacuous"


class TestMinLagAdmission:
    """ADR-011: a future covariate for one horizon must never leak into
    another horizon's feature vector."""

    def test_historical_features_admitted_at_every_horizon(self) -> None:
        historical = [s for s in expand_feature_specs() if s.min_lag_hours is None]
        assert historical
        for spec in historical:
            for h in (24, 48, 72):
                assert spec.admitted_at(h)

    def test_future_covariate_only_admitted_at_its_own_horizon(self) -> None:
        future = [s for s in expand_feature_specs() if s.min_lag_hours is not None]
        assert future
        for spec in future:
            assert spec.admitted_at(spec.min_lag_hours)
            for h in (24, 48, 72):
                if h != spec.min_lag_hours:
                    assert not spec.admitted_at(h), (
                        f"{spec.name} (min_lag={spec.min_lag_hours}) wrongly "
                        f"admitted at horizon {h}"
                    )

    def test_feature_vector_excludes_mismatched_horizon_covariates(self) -> None:
        cams, era5, hist_fc = _synthetic_raw(n_days=15)
        frame = build_feature_frame(
            cams, era5, hist_fc, city_id="islamabad", timezone="Asia/Karachi"
        )
        issue_time = frame.index[len(frame) // 2]
        fv = feature_vector(frame, issue_time, 24)
        assert "fc_temperature_2m_h24" in fv
        assert "fc_temperature_2m_h48" not in fv
        assert "fc_temperature_2m_h72" not in fv


class TestPurgeGap:
    def test_every_split_respects_the_minimum_purge_gap(self) -> None:
        index = pd.date_range("2022-08-04", "2024-08-04", freq="6h", tz="UTC")
        splits = walk_forward_splits(index, n_splits=5, purge_gap_hours=72)
        assert len(splits) == 5
        for split in splits:
            gap = split.test_start - split.train_end
            assert gap >= pd.Timedelta(hours=MIN_PURGE_GAP_HOURS)

    def test_purge_gap_below_minimum_is_rejected(self) -> None:
        index = pd.date_range("2022-08-04", "2024-08-04", freq="6h", tz="UTC")
        with pytest.raises(ValueError, match="72"):
            walk_forward_splits(index, n_splits=5, purge_gap_hours=24)


class TestScalerTrainOnly:
    def test_scaler_stats_reflect_only_the_training_split(self) -> None:
        train = pd.DataFrame({"pm2_5": np.full(100, 50.0)})
        test = pd.DataFrame({"pm2_5": np.full(100, 500.0)})  # wildly different

        stats = fit_scaler(train, ["pm2_5"])
        assert stats.means["pm2_5"] == pytest.approx(50.0)

        combined_mean = pd.concat([train, test])["pm2_5"].mean()
        assert stats.means["pm2_5"] != pytest.approx(combined_mean)
