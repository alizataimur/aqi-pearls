"""Render `reports/dashboard_snapshot.json` — the `--static` mode fallback
(CLAUDE.md §14): a sleeping Streamlit Cloud / HF Space free tier cannot kill
a demo that reads a committed JSON file instead of a live API/store/registry.

Computes exactly what `app/streamlit_app.py`'s data-access functions would
return live (`get_current`, `get_forecast`, `get_explain`, `get_metrics`,
`get_ledger_summary`), for every zone and every horizon, plus the health
guidance table so the Now page needs no live i18n lookup either. Committed
to the repo (like every other artifact under `reports/` — I5): a snapshot
that changes between runs is data drift worth diffing in review, not a
secret to keep local.

Run: `python scripts/render_static_snapshot.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SNAPSHOT_PATH = REPO_ROOT / "reports" / "dashboard_snapshot.json"


def build_snapshot() -> dict[str, object]:
    from aqi.explain.i18n import health_guidance, load_i18n
    from aqi.explain.shap_explain import explain_zone
    from aqi.serving.inference import (
        HORIZONS_HOURS,
        current_reading,
        forecast_zone,
        load_frame_cached,
        zones,
    )
    from aqi.store.ledger import ledger_window

    frame = load_frame_cached()
    zone_list = list(zones())

    current = {}
    forecast = {}
    explain = {}
    for zone in zone_list:
        reading = current_reading(frame, zone.zone_id)
        current[zone.zone_id] = {
            "zone_id": reading.zone_id,
            "time_utc": reading.time_utc.isoformat(),
            "aqi_nowcast": reading.aqi_nowcast,
            "category_en": reading.category_en,
            "category_ur": reading.category_ur,
            "pm2_5": reading.pm2_5,
            "pm10": reading.pm10,
        }

        horizons = forecast_zone(frame, zone.zone_id, zone.timezone)
        forecast[zone.zone_id] = {
            "zone_id": zone.zone_id,
            "serving_model": "lightgbm",
            "horizons": [
                {
                    "horizon_hours": h.horizon_hours,
                    "target_local_date": h.target_local_date,
                    "predicted_aqi": h.predicted_aqi,
                    "category_en": h.category_en,
                    "category_ur": h.category_ur,
                }
                for h in horizons
            ],
        }

        explain[zone.zone_id] = {}
        for horizon_hours in HORIZONS_HOURS:
            result = explain_zone(frame, zone.zone_id, horizon_hours)
            explain[zone.zone_id][str(horizon_hours)] = {
                "zone_id": result.zone_id,
                "horizon_hours": result.horizon_hours,
                "predicted_aqi": result.predicted_aqi,
                "base_value": result.base_value,
                "top_drivers": [
                    {
                        "feature": d.feature,
                        "feature_label_en": d.label_en,
                        "feature_label_ur": d.label_ur,
                        "value": d.value,
                        "shap_value": d.shap_value,
                    }
                    for d in result.top_drivers
                ],
                "briefing_en": result.briefing_en,
                "briefing_ur": result.briefing_ur,
                "explainer_note": result.explainer_note,
            }

    ladder = json.loads(
        (REPO_ROOT / "reports" / "metrics" / "ladder.json").read_text(encoding="utf-8")
    )
    metrics = {
        "champion_model": ladder["champion"]["model_name"],
        "champion_mean_rmse": ladder["champion"]["mean_rmse"],
        "mean_rmse_by_model": ladder["mean_rmse_across_horizons"],
        "generated_at": ladder["generated_at"],
        "split": ladder["split"],
        "primary_metric_note": ladder["primary_metric_note"],
    }

    start, end, n = ledger_window("observed")
    ledger = {
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "row_count": n,
    }

    health_guidance_table = {
        category: {"en": entry["en"], "ur": entry["ur"]}
        for category, entry in load_i18n()["health_guidance"].items()
    }
    # Sanity: every category the snapshot's own readings/forecasts can name
    # must resolve, or the static Now/forecast pages would silently show
    # nothing where the live app shows guidance.
    for category in {r["category_en"] for r in current.values()}:
        assert health_guidance(category) == (
            health_guidance_table.get(category, {}).get("en", ""),
            health_guidance_table.get(category, {}).get("ur", ""),
        )

    return {
        "current": current,
        "forecast": forecast,
        "explain": explain,
        "metrics": metrics,
        "ledger": ledger,
        "health_guidance": health_guidance_table,
    }


def main() -> int:
    snapshot = build_snapshot()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
