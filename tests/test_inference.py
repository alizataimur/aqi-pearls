"""`serving/inference.py` — D9/D10's shared prediction path.

Integration-style, deliberately: this module's whole job is to read the
*real* feature store and the *real* Model Registry, and a mocked-out version
of either would mostly test the mock. Both are gitignored local artifacts
(same as `data/feature_store/` always has been — ADR-006/ADR-014), so these
tests skip rather than fail when they're absent (CI, a fresh checkout) —
the same honest-skip precedent `tests/test_store_parity.py` set for
Hopsworks credentials, not a silently-green fake pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aqi.serving.inference import (
    HORIZONS_HOURS,
    build_live_row,
    current_reading,
    forecast_zone,
    latest_row,
    load_frame_cached,
    load_serving_model,
    zones,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()

pytestmark = pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY),
    reason="needs the real feature store + registered LightGBM models (local dev data)",
)


@pytest.fixture(scope="module")
def frame():  # type: ignore[no-untyped-def]
    return load_frame_cached()


class TestLoadServingModel:
    def test_loads_every_horizon(self) -> None:
        for h in HORIZONS_HOURS:
            loaded = load_serving_model(h)
            assert loaded.horizon_hours == h
            assert loaded.feature_columns
            assert "regression" in loaded.metrics


class TestLatestRowAndLiveRow:
    def test_latest_row_is_the_max_timestamp_for_its_zone(self, frame) -> None:  # type: ignore[no-untyped-def]
        zone_id = zones()[0].zone_id
        row = latest_row(frame, zone_id)
        zone_frame = frame[frame["city_id"] == zone_id]
        assert row["time_utc"] == zone_frame["time_utc"].max()

    def test_unknown_zone_raises(self, frame) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="no feature-store rows"):
            latest_row(frame, "not_a_real_zone")

    def test_build_live_row_sets_only_the_matching_zone_dummy(self, frame) -> None:  # type: ignore[no-untyped-def]
        zone_id = zones()[0].zone_id
        row = latest_row(frame, zone_id)
        loaded = load_serving_model(24)
        live_row = build_live_row(row, zone_id, loaded.feature_columns)
        zone_cols = [c for c in loaded.feature_columns if c.startswith("zone_")]
        for col in zone_cols:
            expected = 1.0 if col == f"zone_{zone_id}" else 0.0
            assert live_row[col].iloc[0] == expected


class TestCurrentAndForecast:
    def test_current_reading_has_a_valid_category(self, frame) -> None:  # type: ignore[no-untyped-def]
        for zone in zones():
            reading = current_reading(frame, zone.zone_id)
            assert reading.category_en != ""
            assert reading.time_utc is not None

    def test_forecast_zone_returns_one_entry_per_horizon(self, frame) -> None:  # type: ignore[no-untyped-def]
        for zone in zones():
            horizons = forecast_zone(frame, zone.zone_id, zone.timezone)
            assert [h.horizon_hours for h in horizons] == list(HORIZONS_HOURS)
            for h in horizons:
                assert h.predicted_aqi >= 0
                assert h.category_en != ""

    def test_forecast_target_dates_are_strictly_increasing(self, frame) -> None:  # type: ignore[no-untyped-def]
        zone = zones()[0]
        horizons = forecast_zone(frame, zone.zone_id, zone.timezone)
        dates = [h.target_local_date for h in horizons]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)
