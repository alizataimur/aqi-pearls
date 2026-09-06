"""Unit tests for the inference pipeline's ledger-writing half (CLAUDE.md
§13, I3). `forecast_zone`/`zones` are monkeypatched — this file's job is the
row schema and the append-only write, not re-testing `forecast_zone` itself
(that lives in `tests/test_inference.py`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from aqi.config import ZoneConfig
from aqi.models.registry import LocalModelRegistry
from aqi.pipelines import inference_pipeline
from aqi.serving.inference import HorizonForecast


def _zone_config(zone_id: str) -> ZoneConfig:
    from aqi.config import CityConfig

    city = CityConfig(
        id=zone_id,
        name_en=zone_id,
        name_ur=zone_id,
        lat=0.0,
        lon=0.0,
        timezone="Asia/Karachi",
        cams_grid=(0.0, 0.0),
        zone=zone_id,
    )
    return ZoneConfig(
        zone_id=zone_id, representative_city=city, member_city_ids=(zone_id,)
    )


@pytest.fixture
def two_zones() -> tuple[ZoneConfig, ZoneConfig]:
    return (_zone_config("capital"), _zone_config("lahore"))


def test_write_ledger_rows_one_row_per_zone_horizon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    two_zones: tuple[ZoneConfig, ZoneConfig],
) -> None:
    monkeypatch.setattr(inference_pipeline, "zones", lambda: two_zones)
    monkeypatch.setattr(
        inference_pipeline,
        "forecast_zone",
        lambda frame, zone_id, tz: [
            HorizonForecast(
                horizon_hours=h,
                target_local_date="2026-09-07",
                predicted_aqi=100.0 + h,
                category_en="Moderate",
                category_ur="درمیانہ",
            )
            for h in (24, 48, 72)
        ],
    )
    monkeypatch.setattr(
        LocalModelRegistry,
        "load_metadata",
        lambda self, model_name, horizon_hours: {"git_sha": "deadbeef"},
    )

    issued_at = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
    written = inference_pipeline.write_ledger_rows(
        pd.DataFrame(), issued_at, root=tmp_path
    )

    assert written == 6  # 2 zones x 3 horizons
    for zone_id in ("capital", "lahore"):
        path = tmp_path / "predicted" / zone_id / "2026-09.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        assert len(rows) == 3
        for row in rows:
            assert row["city_id"] == zone_id
            assert row["issued_at_utc"] == "2026-09-06T12:00:00+00:00"
            assert row["model_version"] == "lightgbm@deadbeef"
            assert row["y_true"] is None
            assert row["horizon_h"] in (24, 48, 72)
            # D+1 target 2026-09-07 local (Asia/Karachi, UTC+5) -> midnight
            # local is the previous UTC day at 19:00.
            assert row["target_time_utc"] == "2026-09-06T19:00:00+00:00"


def test_model_version_degrades_when_metadata_missing(tmp_path: Path) -> None:
    registry = LocalModelRegistry(root=tmp_path)
    version = inference_pipeline._model_version(registry, 24)
    assert version == "lightgbm@unknown"


def test_write_ledger_rows_is_append_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    two_zones: tuple[ZoneConfig, ZoneConfig],
) -> None:
    """Two separate issue events append, they never overwrite each other —
    the append-only contract I3 depends on."""
    monkeypatch.setattr(inference_pipeline, "zones", lambda: (two_zones[0],))
    monkeypatch.setattr(
        inference_pipeline,
        "forecast_zone",
        lambda frame, zone_id, tz: [
            HorizonForecast(
                horizon_hours=24,
                target_local_date="2026-09-07",
                predicted_aqi=100.0,
                category_en="Moderate",
                category_ur="درمیانہ",
            )
        ],
    )
    monkeypatch.setattr(
        LocalModelRegistry,
        "load_metadata",
        lambda self, model_name, horizon_hours: {"git_sha": "deadbeef"},
    )

    first = datetime(2026, 9, 6, 10, 0, 0, tzinfo=UTC)
    second = datetime(2026, 9, 6, 11, 0, 0, tzinfo=UTC)
    inference_pipeline.write_ledger_rows(pd.DataFrame(), first, root=tmp_path)
    inference_pipeline.write_ledger_rows(pd.DataFrame(), second, root=tmp_path)

    path = tmp_path / "predicted" / "capital" / "2026-09.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {r["issued_at_utc"] for r in rows} == {
        "2026-09-06T10:00:00+00:00",
        "2026-09-06T11:00:00+00:00",
    }
