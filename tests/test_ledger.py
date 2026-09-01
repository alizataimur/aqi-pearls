"""Unit tests for the ledger reader (CLAUDE.md §11.3, I3).

Fixture-only — never reads the real `data/ledger/`, since that grows hourly
and the point here is the quarantine-exclusion and window/count contract,
not any particular row count.
"""

from __future__ import annotations

import json
from pathlib import Path

from aqi.store.ledger import ledger_window, read_ledger


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class TestReadLedger:
    def test_reads_good_rows_across_cities(self, tmp_path: Path) -> None:
        _write_jsonl(
            tmp_path / "observed" / "islamabad" / "2026-08.jsonl",
            [{"captured_at_utc": "2026-08-31T14:10:50+00:00", "city_id": "islamabad"}],
        )
        _write_jsonl(
            tmp_path / "observed" / "lahore" / "2026-08.jsonl",
            [{"captured_at_utc": "2026-08-31T14:44:05+00:00", "city_id": "lahore"}],
        )

        frame = read_ledger("observed", root=tmp_path)

        assert len(frame) == 2
        assert set(frame["city_id"]) == {"islamabad", "lahore"}
        # sorted by capture time
        assert list(frame["city_id"]) == ["islamabad", "lahore"]

    def test_excludes_quarantine(self, tmp_path: Path) -> None:
        _write_jsonl(
            tmp_path / "observed" / "islamabad" / "2026-08.jsonl",
            [{"captured_at_utc": "2026-08-31T14:10:50+00:00", "city_id": "islamabad"}],
        )
        _write_jsonl(
            tmp_path / "_quarantine" / "observed" / "islamabad" / "2026-08.jsonl",
            [{"captured_at_utc": "2026-08-27T20:59:23+00:00", "city_id": "islamabad"}],
        )

        frame = read_ledger("observed", root=tmp_path)

        assert len(frame) == 1
        assert frame.iloc[0]["captured_at_utc"].isoformat() == "2026-08-31T14:10:50+00:00"

    def test_missing_stream_is_empty(self, tmp_path: Path) -> None:
        frame = read_ledger("aqicn", root=tmp_path)
        assert frame.empty

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "observed" / "lahore" / "2026-08.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"captured_at_utc": "2026-08-31T14:10:50+00:00", "city_id": "lahore"}\n\n',
            encoding="utf-8",
        )
        frame = read_ledger("observed", root=tmp_path)
        assert len(frame) == 1


class TestLedgerWindow:
    def test_window_and_count(self, tmp_path: Path) -> None:
        _write_jsonl(
            tmp_path / "aqicn" / "islamabad" / "2026-08.jsonl",
            [
                {"captured_at_utc": "2026-08-31T14:10:50+00:00", "city_id": "islamabad"},
                {"captured_at_utc": "2026-08-31T14:44:05+00:00", "city_id": "islamabad"},
            ],
        )

        first, last, count = ledger_window("aqicn", root=tmp_path)

        assert count == 2
        assert first is not None and last is not None
        assert first.isoformat() == "2026-08-31T14:10:50+00:00"
        assert last.isoformat() == "2026-08-31T14:44:05+00:00"

    def test_empty_ledger_reports_none_and_zero(self, tmp_path: Path) -> None:
        first, last, count = ledger_window("observed", root=tmp_path)
        assert (first, last, count) == (None, None, 0)
