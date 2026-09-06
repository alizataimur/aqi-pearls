"""Unit tests for `scripts/clock_starter.py`'s stale/failed distinction
(CLAUDE.md §6, I3, ADR-034). A city whose feed answers but whose reading is
too old must be classified `stale` — recorded as an explicit ledger gap and
exit 0 — never `failed`, which would exit 1 and (per clock-starter.yml's own
step ordering, no `if:` override on "Commit the ledger") skip committing the
very gap record that makes the condition honestly visible at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import clock_starter  # noqa: E402

from aqi.sources.aqicn import StaleReadingError  # noqa: E402

CITY = {
    "id": "islamabad",
    "aqicn_station": "@11739",
    "lat": 33.7,
    "lon": 73.1,
}


def test_stale_reading_is_recorded_as_a_gap_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AQICN_TOKEN", "dummy-token")
    monkeypatch.setattr(clock_starter, "load_cities", lambda: [CITY])
    monkeypatch.setattr(clock_starter, "load_dotenv", lambda: None)
    monkeypatch.setattr(clock_starter, "LEDGER", tmp_path)
    monkeypatch.setattr(sys, "argv", ["clock_starter.py"])

    def _raise_stale(city: object, token: str, now: object, dry_run: bool) -> bool:
        raise StaleReadingError(
            "station reading is 4848.7h old (time.iso=2026-02-16T17:00:00+05:00) "
            "— older than the 6.0h freshness threshold, refusing to write it as "
            "a current observation",
            time_iso="2026-02-16T17:00:00+05:00",
            age_hours=4848.7,
        )

    monkeypatch.setattr(clock_starter, "capture_city", _raise_stale)

    exit_code = clock_starter.main()

    assert exit_code == 0

    stderr = capsys.readouterr().err
    assert "stale" in stderr.lower()
    assert "failed" not in stderr.lower() or "failed:" not in stderr.lower()

    gap_files = list((tmp_path / "observed" / "islamabad").glob("*.jsonl"))
    assert len(gap_files) == 1
    rows = [json.loads(line) for line in gap_files[0].read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["city_id"] == "islamabad"
    assert row["station_id"] == "@11739"
    assert row["observation_gap"] is True
    assert row["reason"] == "stale_upstream_reading"
    assert row["reading_time_iso"] == "2026-02-16T17:00:00+05:00"
    assert row["age_hours"] == 4848.7


def test_a_real_failure_still_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aqi.sources.aqicn import AQICNError

    monkeypatch.setenv("AQICN_TOKEN", "dummy-token")
    monkeypatch.setattr(clock_starter, "load_cities", lambda: [CITY])
    monkeypatch.setattr(clock_starter, "load_dotenv", lambda: None)
    monkeypatch.setattr(clock_starter, "LEDGER", tmp_path)
    monkeypatch.setattr(sys, "argv", ["clock_starter.py"])

    def _raise_real_failure(city: object, token: str, now: object, dry_run: bool) -> bool:
        raise AQICNError("connection refused")

    monkeypatch.setattr(clock_starter, "capture_city", _raise_real_failure)

    assert clock_starter.main() == 1
