"""Resumable, chunked historical backfill (CLAUDE.md §8.3, D4).

Fills the feature store with (features, targets) for every forecast zone
(ADR-013: `capital`, `lahore`) from `conf/config.yaml`'s `backfill_start`
(probed, not invented — CLAUDE.md §8.2) through `--end` (default: two days
ago, UTC — the historical-forecast archive's recency lags "now").

**Chunked per zone-month**, exactly as CLAUDE.md §8.3 requires, so a
multi-year pull that dies at 80% resumes rather than restarts. Each chunk:

1. Fetches CAMS + ERA5 + historical-forecast for the month **plus context**
   either side — 8 days before (the longest lag, 168h, plus rolling windows)
   and 4 days after (targets look up to 72h/3 days ahead, and daily targets
   need a full local day of hours to be non-NaN).
2. Builds the full feature+target frame with `build_feature_frame` — the
   same function session 2's leakage test already attacks, so a backfilled
   row and an hourly-pipeline row are produced by identical code.
3. Writes **only the rows whose `time_utc` falls inside the chunk's own
   calendar month** to the store — the context either side exists so lags,
   rolling stats and targets are correct at the chunk boundary, not so it
   gets persisted twice by neighbouring chunks (the store's upsert would
   tolerate that, but it's needless API load and disk).
4. Appends one line to the manifest (`data/backfill_manifest.jsonl`) only on
   success. A chunk with no manifest line is retried on the next run — that
   is the entire resume mechanism, no separate "resume from" state needed.

One API failure fails only its own chunk (CLAUDE.md §8.3) — the run
continues to the next chunk and reports every failure at the end, rather than
aborting a multi-hour pull over one transient error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqi.config import REPO_ROOT, ZoneConfig, get_config, get_zones
from aqi.pipelines.common import fetch_zone_frame
from aqi.sources._http import SourceError
from aqi.store import CITY_COLUMN, TIME_COLUMN, get_store
from aqi.store.base import FeatureStore

MANIFEST_PATH = REPO_ROOT / "data" / "backfill_manifest.jsonl"
COVERAGE_PATH = REPO_ROOT / "reports" / "metrics" / "coverage.json"

LEAD_CONTEXT_DAYS = 8  # covers the 168h lag + all rolling windows
TAIL_CONTEXT_DAYS = 4  # covers the 72h horizon + a full local day of hours


@dataclass(frozen=True)
class Chunk:
    zone_id: str
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.zone_id}:{self.year:04d}-{self.month:02d}"

    @property
    def month_start(self) -> pd.Timestamp:
        return pd.Timestamp(year=self.year, month=self.month, day=1, tz="UTC")

    @property
    def month_end(self) -> pd.Timestamp:
        # Last instant of the month, hour-resolution: first hour of next
        # month minus one hour.
        return (self.month_start + pd.DateOffset(months=1)) - pd.Timedelta(hours=1)


def month_range(start: datetime, end: datetime) -> list[tuple[int, int]]:
    months = []
    cursor = pd.Timestamp(start).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_marker = pd.Timestamp(end).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    while cursor <= end_marker:
        months.append((cursor.year, cursor.month))
        cursor = cursor + pd.DateOffset(months=1)
    return months


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    done: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            done[record["chunk_key"]] = record
    return done


def append_manifest(record: dict[str, Any], path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


@dataclass
class BackfillResult:
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _fetch_chunk_frame(
    zone: ZoneConfig, chunk: Chunk, *, sleep_seconds: float
) -> pd.DataFrame:
    fetch_start = (chunk.month_start - pd.Timedelta(days=LEAD_CONTEXT_DAYS)).strftime(
        "%Y-%m-%d"
    )
    fetch_end_ts = chunk.month_end + pd.Timedelta(days=TAIL_CONTEXT_DAYS)
    now = pd.Timestamp.now(tz="UTC")
    if fetch_end_ts > now:
        fetch_end_ts = now  # can't fetch actuals that don't exist yet
    fetch_end = fetch_end_ts.strftime("%Y-%m-%d")

    return fetch_zone_frame(zone, fetch_start, fetch_end, sleep_seconds=sleep_seconds)


def run_chunk(
    zone: ZoneConfig,
    chunk: Chunk,
    store: FeatureStore,
    *,
    group: str,
    version: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    frame = _fetch_chunk_frame(zone, chunk, sleep_seconds=sleep_seconds)

    owned = frame.reset_index()
    in_month = (owned[TIME_COLUMN] >= chunk.month_start) & (
        owned[TIME_COLUMN] <= chunk.month_end
    )
    owned = owned[in_month]
    if owned.empty:
        raise SourceError(f"{chunk.key}: built frame has no rows in the chunk's month")

    store.write(owned, group, version)

    return {
        "chunk_key": chunk.key,
        "zone_id": zone.zone_id,
        "year": chunk.year,
        "month": chunk.month,
        "row_count": int(len(owned)),
        "first_time_utc": owned[TIME_COLUMN].min().isoformat(),
        "last_time_utc": owned[TIME_COLUMN].max().isoformat(),
        "completed_at_utc": datetime.now(UTC).isoformat(),
    }


def run_backfill(
    *,
    start: datetime,
    end: datetime,
    zone_ids: list[str] | None = None,
    force: bool = False,
    sleep_seconds: float = 2.0,
    manifest_path: Path = MANIFEST_PATH,
) -> BackfillResult:
    config = get_config()
    zones = [z for z in get_zones() if zone_ids is None or z.zone_id in zone_ids]
    if not zones:
        raise ValueError(f"no matching zones for {zone_ids!r}")

    store = get_store()
    manifest = {} if force else load_manifest(manifest_path)
    result = BackfillResult()

    chunks = [
        Chunk(zone.zone_id, year, month)
        for zone in zones
        for year, month in month_range(start, end)
    ]

    for chunk in chunks:
        zone = next(z for z in zones if z.zone_id == chunk.zone_id)
        if not force and chunk.key in manifest:
            result.skipped.append(chunk.key)
            continue
        try:
            record = run_chunk(
                zone,
                chunk,
                store,
                group=config.store.feature_group,
                version=config.store.version,
                sleep_seconds=sleep_seconds,
            )
        except (SourceError, ValueError, KeyError) as exc:
            print(f"[warn] {chunk.key} failed: {exc}", file=sys.stderr)
            result.failed.append((chunk.key, str(exc)))
            continue

        append_manifest(record, manifest_path)
        result.completed.append(chunk.key)
        print(json.dumps(record))

    return result


def _null_rates_by_zone(
    store: FeatureStore, group: str, start: datetime, end: datetime, zone_ids: list[str]
) -> dict[str, dict[str, float]]:
    """Per-column null fraction, per zone, over the requested window.

    Row presence (below) proves a row was written; it says nothing about
    whether the columns in that row are actually populated. Without this, a
    six-month source gap in `boundary_layer_height` (ADR-015) reads as "zero
    gaps" simply because a row exists for every hour. Only columns with a
    nonzero null rate are included, so the report stays focused on real gaps
    rather than 240-odd zero entries.
    """
    frame = store.read(group, start, end)
    if frame.empty:
        return {zone_id: {} for zone_id in zone_ids}
    result: dict[str, dict[str, float]] = {}
    for zone_id in zone_ids:
        zone_frame = frame.loc[frame[CITY_COLUMN] == zone_id]
        if zone_frame.empty:
            result[zone_id] = {}
            continue
        rates = zone_frame.isna().mean()
        result[zone_id] = {
            str(col): round(float(rate), 4) for col, rate in rates.items() if rate > 0
        }
    return result


def build_coverage_report(
    *,
    start: datetime,
    end: datetime,
    zone_ids: list[str] | None = None,
    manifest_path: Path = MANIFEST_PATH,
    store: FeatureStore | None = None,
) -> dict[str, Any]:
    """D4 evidence — CLAUDE.md I4/I5: read from the manifest, never typed.

    Row-presence stats (completed/gap months, row counts) come from the
    manifest alone, so this stays usable with no real data on disk. Per-column
    null rates are opt-in via `store` — pass one (e.g. `get_store()`) to have
    the report also describe *data* presence, not just row presence. Omitted
    by default so tests exercising only the manifest logic don't need real
    backfilled data.
    """
    zones = [z for z in get_zones() if zone_ids is None or z.zone_id in zone_ids]
    manifest = load_manifest(manifest_path)
    expected_months = month_range(start, end)

    null_rates: dict[str, dict[str, float]] = {}
    if store is not None:
        config = get_config()
        null_rates = _null_rates_by_zone(
            store, config.store.feature_group, start, end, [z.zone_id for z in zones]
        )

    per_zone: dict[str, Any] = {}
    for zone in zones:
        done = [
            (y, m)
            for (y, m) in expected_months
            if Chunk(zone.zone_id, y, m).key in manifest
        ]
        gaps = [f"{y:04d}-{m:02d}" for (y, m) in expected_months if (y, m) not in done]
        records = [manifest[Chunk(zone.zone_id, y, m).key] for (y, m) in done]
        per_zone[zone.zone_id] = {
            "expected_months": len(expected_months),
            "completed_months": len(done),
            "gap_months": gaps,
            "first_time_utc": min((r["first_time_utc"] for r in records), default=None),
            "last_time_utc": max((r["last_time_utc"] for r in records), default=None),
            "total_rows": sum(r["row_count"] for r in records),
            "null_rates": null_rates.get(zone.zone_id, {}),
        }

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "requested_start": pd.Timestamp(start).strftime("%Y-%m-%d"),
        "requested_end": pd.Timestamp(end).strftime("%Y-%m-%d"),
        "zones": per_zone,
    }


def write_coverage_report(report: dict[str, Any], path: Path = COVERAGE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    config = get_config()
    default_start = datetime.strptime(config.sources.backfill_start, "%Y-%m-%d").replace(
        tzinfo=UTC
    )
    default_end = datetime.now(UTC) - timedelta(days=2)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=default_start.strftime("%Y-%m-%d"))
    parser.add_argument("--end", default=default_end.strftime("%Y-%m-%d"))
    parser.add_argument(
        "--zones", default=None, help="comma-separated zone ids (default: all)"
    )
    parser.add_argument("--force", action="store_true", help="redo already-done chunks")
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="skip fetching; just regenerate reports/metrics/coverage.json",
    )
    args = parser.parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    zone_ids = args.zones.split(",") if args.zones else None

    had_failures = False
    if not args.coverage_only:
        result = run_backfill(
            start=start,
            end=end,
            zone_ids=zone_ids,
            force=args.force,
            sleep_seconds=args.sleep_seconds,
        )
        had_failures = bool(result.failed)
        print(
            json.dumps(
                {
                    "completed": len(result.completed),
                    "skipped": len(result.skipped),
                    "failed": result.failed,
                }
            )
        )

    report = build_coverage_report(
        start=start, end=end, zone_ids=zone_ids, store=get_store()
    )
    write_coverage_report(report)
    print(f"coverage report written to {COVERAGE_PATH}")

    return 1 if had_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
