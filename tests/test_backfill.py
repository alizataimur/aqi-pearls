"""Unit tests for the backfill manifest/chunking/coverage logic (D4).

No network — `run_chunk`'s live-fetch path is exercised by actually running
the pipeline (see `docs/STATE.md` for the real backfill's status), not by a
mocked unit test here. This file is about the resumability contract:
chunk boundaries, manifest round-trip, and the coverage report reading
exactly what the manifest says rather than being hand-typed (CLAUDE.md I5).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from aqi.pipelines.backfill import (
    Chunk,
    append_manifest,
    build_coverage_report,
    load_manifest,
    month_range,
)
from aqi.store.parquet_store import ParquetFeatureStore


class TestMonthRange:
    def test_single_month(self) -> None:
        start = datetime(2024, 3, 15, tzinfo=UTC)
        end = datetime(2024, 3, 20, tzinfo=UTC)
        assert month_range(start, end) == [(2024, 3)]

    def test_spans_year_boundary(self) -> None:
        start = datetime(2023, 11, 1, tzinfo=UTC)
        end = datetime(2024, 2, 1, tzinfo=UTC)
        assert month_range(start, end) == [
            (2023, 11),
            (2023, 12),
            (2024, 1),
            (2024, 2),
        ]


class TestChunk:
    def test_month_boundaries(self) -> None:
        chunk = Chunk("capital", 2024, 2)  # leap year February
        assert chunk.month_start == chunk.month_start.replace(
            year=2024, month=2, day=1, hour=0
        )
        assert chunk.month_end.day == 29
        assert chunk.month_end.hour == 23

    def test_key_is_stable_and_unique_per_zone(self) -> None:
        assert Chunk("capital", 2024, 2).key == "capital:2024-02"
        assert Chunk("lahore", 2024, 2).key != Chunk("capital", 2024, 2).key


class TestManifest:
    def test_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "manifest.jsonl"
        record = {
            "chunk_key": "capital:2024-02",
            "zone_id": "capital",
            "year": 2024,
            "month": 2,
            "row_count": 696,
            "first_time_utc": "2024-02-01T00:00:00+00:00",
            "last_time_utc": "2024-02-29T23:00:00+00:00",
            "completed_at_utc": "2024-03-01T00:00:00+00:00",
        }
        append_manifest(record, path)
        loaded = load_manifest(path)
        assert loaded["capital:2024-02"] == record

    def test_missing_file_is_empty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert load_manifest(tmp_path / "does_not_exist.jsonl") == {}


class TestCoverageReport:
    def test_reports_gaps_and_totals_from_manifest_only(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "manifest.jsonl"
        append_manifest(
            {
                "chunk_key": "capital:2024-01",
                "zone_id": "capital",
                "year": 2024,
                "month": 1,
                "row_count": 744,
                "first_time_utc": "2024-01-01T00:00:00+00:00",
                "last_time_utc": "2024-01-31T23:00:00+00:00",
                "completed_at_utc": "2024-02-01T00:00:00+00:00",
            },
            path,
        )
        # 2024-02 deliberately left out of the manifest -> a gap.

        report = build_coverage_report(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 2, 29, tzinfo=UTC),
            zone_ids=["capital"],
            manifest_path=path,
        )

        capital = report["zones"]["capital"]
        assert capital["expected_months"] == 2
        assert capital["completed_months"] == 1
        assert capital["gap_months"] == ["2024-02"]
        assert capital["total_rows"] == 744
        assert capital["first_time_utc"] == "2024-01-01T00:00:00+00:00"
        # No store was passed -> data-presence stays unopined-on, not "zero gaps".
        assert capital["null_rates"] == {}

    def test_null_rates_reflect_data_presence_not_row_presence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Row presence alone would call this window gap-free — every hour has
        a row. Null rates must still surface that half of them carry no real
        `boundary_layer_height` reading (the ADR-015 shape of bug)."""
        manifest_path = tmp_path / "manifest.jsonl"
        append_manifest(
            {
                "chunk_key": "capital:2024-01",
                "zone_id": "capital",
                "year": 2024,
                "month": 1,
                "row_count": 4,
                "first_time_utc": "2024-01-01T00:00:00+00:00",
                "last_time_utc": "2024-01-01T03:00:00+00:00",
                "completed_at_utc": "2024-02-01T00:00:00+00:00",
            },
            manifest_path,
        )

        store = ParquetFeatureStore(root=tmp_path / "feature_store")
        frame = pd.DataFrame(
            {
                "time_utc": pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"),
                "city_id": ["capital"] * 4,
                "pm2_5": [10.0, 11.0, 12.0, 13.0],
                "boundary_layer_height": [500.0, None, None, 600.0],
            }
        )
        store.write(frame, "aqi_features", 1)

        report = build_coverage_report(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 1, 3, tzinfo=UTC),
            zone_ids=["capital"],
            manifest_path=manifest_path,
            store=store,
        )

        capital = report["zones"]["capital"]
        assert capital["gap_months"] == []  # row presence looks perfect
        assert capital["null_rates"]["boundary_layer_height"] == 0.5
        assert "pm2_5" not in capital["null_rates"]  # fully populated -> omitted
