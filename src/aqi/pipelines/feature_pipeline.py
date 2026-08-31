"""Hourly feature pipeline (CLAUDE.md §13, D1).

Runs every hour: for each forecast zone (ADR-013: `capital`, `lahore`), fetch
a rolling window of recent CAMS/ERA5/historical-forecast data, build features
with the same `build_feature_frame` the backfill and the leakage test use,
and upsert a refresh window of rows into the feature store.

Two windows, not one:

  * **fetch window** — `now - LEAD_CONTEXT_DAYS` to `now + TAIL_CONTEXT_DAYS`,
    wide enough that every lag/rolling feature and every future covariate on
    the write window's rows is computed from real context, not padding.
  * **write window** — `now - WRITE_WINDOW_DAYS` to `now`, the rows actually
    upserted. Refreshing several days back (not just the latest hour) lets
    `target_daily_aqi_h*` fill in as its local calendar day completes — at
    issue time `T`, `target_daily_aqi_h24` is genuinely unknown (I3) and
    correctly NaN; it becomes knowable only once `T+24h` has actually
    happened, which is why every run rewrites the recent past, not just the
    newest hour.

Never writes a row with `time_utc > now` — a future timestamp has no
observed pollutant/weather actuals yet and is not a valid issue-time feature
row (CLAUDE.md I1). One zone failing never aborts the other (CLAUDE.md §8.3),
mirroring `scripts/clock_starter.py`'s per-city isolation.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import pandas as pd

from aqi.config import get_config, get_zones
from aqi.pipelines.common import fetch_zone_frame
from aqi.sources._http import SourceError
from aqi.store import TIME_COLUMN, get_store

LEAD_CONTEXT_DAYS = 8  # covers the 168h lag + all rolling windows
TAIL_CONTEXT_DAYS = 4  # covers the 72h horizon's future covariates
WRITE_WINDOW_DAYS = 5  # refresh window — lets recent targets fill in as they complete


def run_feature_pipeline(*, sleep_seconds: float = 2.0) -> dict[str, Any]:
    config = get_config()
    store = get_store()
    now = pd.Timestamp.now(tz="UTC").floor("h")

    fetch_start = (now - pd.Timedelta(days=LEAD_CONTEXT_DAYS)).strftime("%Y-%m-%d")
    fetch_end = (now + pd.Timedelta(days=TAIL_CONTEXT_DAYS)).strftime("%Y-%m-%d")
    write_start = now - pd.Timedelta(days=WRITE_WINDOW_DAYS)

    succeeded: list[str] = []
    failed: list[list[str]] = []
    row_counts: dict[str, int] = {}

    for zone in get_zones():
        try:
            frame = fetch_zone_frame(
                zone, fetch_start, fetch_end, sleep_seconds=sleep_seconds
            )
        except SourceError as exc:
            print(f"[warn] {zone.zone_id}: {exc}", file=sys.stderr)
            failed.append([zone.zone_id, str(exc)])
            continue

        owned = frame.reset_index()
        owned = owned[(owned[TIME_COLUMN] >= write_start) & (owned[TIME_COLUMN] <= now)]
        if owned.empty:
            failed.append([zone.zone_id, "built frame had no rows in the write window"])
            continue

        store.write(owned, config.store.feature_group, config.store.version)
        succeeded.append(zone.zone_id)
        row_counts[zone.zone_id] = int(len(owned))

    result: dict[str, Any] = {
        "run_at_utc": now.isoformat(),
        "succeeded": succeeded,
        "failed": failed,
        "row_counts": row_counts,
    }
    print(json.dumps(result))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    result = run_feature_pipeline(sleep_seconds=args.sleep_seconds)
    # A partial capture is a success (mirrors clock_starter.py's own rule) —
    # only every zone failing means this hour is a real, unrepeatable gap.
    return 1 if not result["succeeded"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
