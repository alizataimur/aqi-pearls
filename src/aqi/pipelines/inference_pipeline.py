"""Inference pipeline (D9/D10, CLAUDE.md §13) — hourly: load the serving
model, predict D+1/D+2/D+3 for both zones, **write the forecast ledger at
issue time (I3)**, and publish `reports/dashboard_snapshot.json`.

This is the piece CLAUDE.md §13's architecture table names ("inference-
pipeline... hourly... load champion, predict, write the ledger, publish JSON
for the UI") that never existed before this session — confirmed by grepping
the whole repo for "inference_pipeline"/"inference-pipeline" and finding zero
matches outside CLAUDE.md itself, and by `data/ledger/` holding only
`observed/` and `aqicn/` (`scripts/clock_starter.py`'s captures) with no
third stream for our own issued forecasts. Every hour this ran late, or
didn't run, is a permanent gap in this project's half of the benchmark (I3)
— the same wall-clock argument CLAUDE.md §6 makes for clock-starter, applied
to the side of the ledger nobody had built yet.

Ledger row schema, scoped to what this session can actually compute — a
point forecast, no interval, no exceedance probability (both cut alongside
conformal prediction, ADR-021), so the row does not claim them:

    issued_at_utc, city_id, target_time_utc, horizon_h, model_version,
    y_pred, y_true (null; filled later by a scoring job this session does
    not build)

`target_time_utc` is local midnight of the target calendar day, converted to
UTC — an honest anchor for "which day this is a daily-max forecast for," not
a claim that the target is a point-in-time value.

Publishing the snapshot reuses `scripts/render_static_snapshot.py::
build_snapshot` directly — never a second copy of that schema — passing in
the same feature-store frame already loaded for the ledger write so the
frame is read once, not twice.

CLI: `python -m aqi.pipelines.inference_pipeline`
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from aqi.config import REPO_ROOT
from aqi.models.registry import LocalModelRegistry
from aqi.serving.inference import (
    SERVING_MODEL_NAME,
    forecast_zone,
    load_frame_cached,
    zones,
)

LEDGER_ROOT = REPO_ROOT / "data" / "ledger"


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _model_version(registry: LocalModelRegistry, horizon_hours: int) -> str:
    """`{model_name}@{git_sha}` — identifies which trained artifact actually
    issued a row, not just the family name every horizon shares."""
    try:
        metadata = registry.load_metadata(SERVING_MODEL_NAME, horizon_hours)
        git_sha = str(metadata.get("git_sha") or "unknown")
    except (FileNotFoundError, OSError):
        git_sha = "unknown"
    return f"{SERVING_MODEL_NAME}@{git_sha[:8]}"


def write_ledger_rows(
    frame: pd.DataFrame, issued_at: datetime, root: Path = LEDGER_ROOT
) -> int:
    """One append-only row per (issued_at_utc, city_id, target_time_utc,
    horizon_h, model_version) for every zone/horizon `forecast_zone` (the
    same function the API and dashboard call) predicts right now. Returns
    the number of rows written — the number the caller should report as
    live evidence, not assume."""
    registry = LocalModelRegistry()
    written = 0
    for zone in zones():
        horizons = forecast_zone(frame, zone.zone_id, zone.timezone)
        month = issued_at.strftime("%Y-%m")
        path = root / "predicted" / zone.zone_id / f"{month}.jsonl"
        for horizon in horizons:
            target_date = date.fromisoformat(horizon.target_local_date)
            target_local_midnight = datetime.combine(
                target_date, time.min, tzinfo=ZoneInfo(zone.timezone)
            )
            row = {
                "issued_at_utc": issued_at.isoformat(),
                "city_id": zone.zone_id,
                "target_time_utc": target_local_midnight.astimezone(UTC).isoformat(),
                "horizon_h": horizon.horizon_hours,
                "model_version": _model_version(registry, horizon.horizon_hours),
                "y_pred": horizon.predicted_aqi,
                "y_true": None,
            }
            _append_jsonl(path, row)
            written += 1
    return written


def _write_snapshot(frame: pd.DataFrame) -> Path:
    # Reuses render_static_snapshot.py's own build_snapshot — the schema is
    # defined there once, never duplicated here.
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from render_static_snapshot import (  # type: ignore[import-not-found]
        SNAPSHOT_PATH,
        build_snapshot,
    )

    snapshot = build_snapshot(frame)
    snapshot_path = Path(SNAPSHOT_PATH)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return snapshot_path


def run_inference_pipeline() -> dict[str, Any]:
    # Driven by "what's the latest data" (the feature store's newest row),
    # never by "it is now HH:MM" — CLAUDE.md §13: scheduled runs are queued,
    # not punctual, and forecast_zone already resolves off the frame's own
    # latest timestamp, not the wall clock.
    issued_at = datetime.now(UTC).replace(microsecond=0)
    frame = load_frame_cached()

    n_rows = write_ledger_rows(frame, issued_at)
    snapshot_path = _write_snapshot(frame)

    return {
        "issued_at_utc": issued_at.isoformat(),
        "ledger_rows_written": n_rows,
        "snapshot_path": str(snapshot_path),
    }


def main() -> int:
    result = run_inference_pipeline()
    print(
        f"[inference_pipeline] issued_at={result['issued_at_utc']} "
        f"wrote {result['ledger_rows_written']} ledger rows, "
        f"snapshot -> {result['snapshot_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
