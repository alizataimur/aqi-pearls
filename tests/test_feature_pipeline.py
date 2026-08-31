"""Unit tests for the hourly feature pipeline (D1).

No network: `fetch_zone_frame` and `get_store` are monkeypatched. This file
is about the pipeline's own contract — never writing a future-timestamped
row (I1), refreshing the write window rather than just the latest hour, and
one zone's failure not blocking the other (CLAUDE.md §8.3) — not about the
real Open-Meteo integration, which the backfill run exercises live.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import aqi.pipelines.feature_pipeline as fp
from aqi.config import ZoneConfig
from aqi.sources._http import SourceError
from aqi.store.parquet_store import ParquetFeatureStore


def _fake_zone(zone_id: str) -> ZoneConfig:
    city = {
        "id": zone_id,
        "name_en": zone_id,
        "name_ur": zone_id,
        "lat": 33.0,
        "lon": 73.0,
        "timezone": "Asia/Karachi",
        "aqicn_station": "@1",
        "cams_grid": (33.0, 73.0),
        "zone": zone_id,
    }
    return ZoneConfig(
        zone_id=zone_id,
        representative_city=city,  # type: ignore[arg-type]
        member_city_ids=(zone_id,),
    )


def _fake_frame(zone_id: str, now: pd.Timestamp) -> pd.DataFrame:
    # Spans well before and well after "now" — the pipeline must trim to the
    # write window and must never keep a row past "now" (I1).
    start = now - pd.Timedelta(days=10)
    end = now + pd.Timedelta(days=4)
    times = pd.date_range(start, end, freq="h")
    frame = pd.DataFrame(
        {"time_utc": times, "city_id": zone_id, "pm2_5": range(len(times))}
    )
    return frame.set_index("time_utc")


@pytest.fixture(autouse=True)
def _patch_zones_and_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> ParquetFeatureStore:
    zones = [_fake_zone("capital"), _fake_zone("lahore")]
    monkeypatch.setattr(fp, "get_zones", lambda: zones)
    store = ParquetFeatureStore(root=tmp_path / "store")
    monkeypatch.setattr(fp, "get_store", lambda: store)
    return store


class TestFeaturePipeline:
    def test_never_writes_a_row_past_now(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        now = pd.Timestamp.now(tz="UTC").floor("h")

        def fake_fetch(
            zone: ZoneConfig, start: str, end: str, *, sleep_seconds: float
        ) -> pd.DataFrame:
            return _fake_frame(zone.zone_id, now)

        monkeypatch.setattr(fp, "fetch_zone_frame", fake_fetch)

        result = fp.run_feature_pipeline(sleep_seconds=0.0)
        assert set(result["succeeded"]) == {"capital", "lahore"}

        store: ParquetFeatureStore = fp.get_store()
        window = pd.Timedelta(days=30)
        out = store.read("aqi_features", now - window, now + window)
        assert out["time_utc"].max() <= now
        assert out["time_utc"].min() >= now - pd.Timedelta(days=fp.WRITE_WINDOW_DAYS)

    def test_one_zone_failing_does_not_block_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = pd.Timestamp.now(tz="UTC").floor("h")

        def fake_fetch(
            zone: ZoneConfig, start: str, end: str, *, sleep_seconds: float
        ) -> pd.DataFrame:
            if zone.zone_id == "capital":
                raise SourceError("simulated outage")
            return _fake_frame(zone.zone_id, now)

        monkeypatch.setattr(fp, "fetch_zone_frame", fake_fetch)

        result = fp.run_feature_pipeline(sleep_seconds=0.0)
        assert result["succeeded"] == ["lahore"]
        assert result["failed"][0][0] == "capital"
