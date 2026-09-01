"""Streamlit dashboard (D9, D10). Four pages, not five (session brief cuts
the scorecard — the ledger holds about a day of history, and a scorecard
built from that would be dishonest; the Model card page says so plainly,
with the real ledger window instead of a chart nobody should trust yet):

  1. Now — current AQI, category, health guidance
  2. 3-day forecast — predicted category per horizon (no probability/interval
     yet — differentiator #2 is cut this session, see docs/DECISIONS.md)
  3. Why — the SHAP briefing, English and Urdu, clearly labelled as the
     LightGBM model's attribution, not SARIMAX's (the metrics champion)
  4. Model card — the ladder table, champion, and the ledger's honest window

Reads the FastAPI service first; on any failure (connection refused, non-2xx,
timeout) falls back to calling `serving/inference.py` and
`explain/shap_explain.py` directly against the feature store (I10) — the UI
never goes fully dark just because `uvicorn` isn't running.

`--static` (via `streamlit run app/streamlit_app.py -- --static`) skips both
the API and the live store/registry entirely and reads
`reports/dashboard_snapshot.json` (`scripts/render_static_snapshot.py`) —
CLAUDE.md §14's requirement that a sleeping free tier can't kill the demo.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
API_URL = os.environ.get("AQI_API_URL", "http://localhost:8000")
STATIC_MODE = "--static" in sys.argv
STATIC_SNAPSHOT_PATH = REPO_ROOT / "reports" / "dashboard_snapshot.json"
API_TIMEOUT_SECONDS = 2.0

ZONE_DISPLAY = {"capital": "Islamabad / Rawalpindi", "lahore": "Lahore"}


# -- data access: API -> direct-store fallback -> static snapshot ----------


@st.cache_data(ttl=600)
def _static_snapshot() -> dict[str, Any]:
    return json.loads(STATIC_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        response = requests.get(
            f"{API_URL}{path}", params=params, timeout=API_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result
    except requests.RequestException:
        return None


@st.cache_resource
def _fallback_frame() -> Any:
    from aqi.serving.inference import load_frame_cached

    return load_frame_cached()


def get_current(zone_id: str) -> dict[str, Any]:
    if STATIC_MODE:
        return dict(_static_snapshot()["current"][zone_id])
    data = _api_get("/current", {"zone_id": zone_id})
    if data is not None:
        return data
    from aqi.serving.inference import current_reading

    reading = current_reading(_fallback_frame(), zone_id)
    return {
        "zone_id": reading.zone_id,
        "time_utc": reading.time_utc.isoformat(),
        "aqi_nowcast": reading.aqi_nowcast,
        "category_en": reading.category_en,
        "category_ur": reading.category_ur,
        "pm2_5": reading.pm2_5,
        "pm10": reading.pm10,
    }


def get_forecast(zone_id: str) -> dict[str, Any]:
    if STATIC_MODE:
        return dict(_static_snapshot()["forecast"][zone_id])
    data = _api_get("/forecast", {"zone_id": zone_id})
    if data is not None:
        return data
    from aqi.serving.inference import forecast_zone, zones

    zone = next(z for z in zones() if z.zone_id == zone_id)
    horizons = forecast_zone(_fallback_frame(), zone_id, zone.timezone)
    return {
        "zone_id": zone_id,
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


def get_explain(zone_id: str, horizon_hours: int) -> dict[str, Any]:
    if STATIC_MODE:
        return dict(_static_snapshot()["explain"][zone_id][str(horizon_hours)])
    data = _api_get("/explain", {"zone_id": zone_id, "horizon_hours": horizon_hours})
    if data is not None:
        return data
    from aqi.explain.shap_explain import explain_zone

    result = explain_zone(_fallback_frame(), zone_id, horizon_hours)
    return {
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


def get_metrics() -> dict[str, Any]:
    if STATIC_MODE:
        return dict(_static_snapshot()["metrics"])
    data = _api_get("/metrics")
    if data is not None:
        return data
    ladder_path = REPO_ROOT / "reports" / "metrics" / "ladder.json"
    ladder = json.loads(ladder_path.read_text(encoding="utf-8"))
    return {
        "champion_model": ladder["champion"]["model_name"],
        "champion_mean_rmse": ladder["champion"]["mean_rmse"],
        "mean_rmse_by_model": ladder["mean_rmse_across_horizons"],
        "generated_at": ladder["generated_at"],
        "split": ladder["split"],
        "primary_metric_note": ladder["primary_metric_note"],
    }


def get_ledger_summary() -> dict[str, Any]:
    if STATIC_MODE:
        return dict(_static_snapshot()["ledger"])
    from aqi.store.ledger import ledger_window

    start, end, n = ledger_window("observed")
    return {
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "row_count": n,
    }


def get_health_guidance(category_en: str) -> tuple[str, str]:
    if STATIC_MODE:
        entry = _static_snapshot().get("health_guidance", {}).get(category_en)
        return (entry["en"], entry["ur"]) if entry else ("", "")
    from aqi.explain.i18n import health_guidance

    return health_guidance(category_en)


# -- pages -------------------------------------------------------------


def _zone_picker() -> str:
    zone_id = st.sidebar.radio(
        "Zone", options=["capital", "lahore"], format_func=lambda z: ZONE_DISPLAY[z]
    )
    return zone_id


def page_now(zone_id: str) -> None:
    st.header("Now")
    current = get_current(zone_id)
    col1, col2 = st.columns(2)
    with col1:
        aqi = current["aqi_nowcast"]
        st.metric("Current AQI (NowCast)", f"{aqi:.0f}" if aqi is not None else "N/A")
        st.write(f"**Category:** {current['category_en']} / {current['category_ur']}")
    with col2:
        st.write(f"PM2.5: {current['pm2_5']}")
        st.write(f"PM10: {current['pm10']}")
    st.caption(f"As of {current['time_utc']} UTC")

    guidance_en, guidance_ur = get_health_guidance(current["category_en"])
    if guidance_en:
        st.info(f"{guidance_en}\n\n{guidance_ur}")


def page_forecast(zone_id: str) -> None:
    st.header("3-day forecast")
    st.caption(
        "Point forecast from the registered LightGBM model. No exceedance "
        "probability or interval yet — that's differentiator #2 "
        "(conformal prediction), cut this session (docs/DECISIONS.md)."
    )
    forecast = get_forecast(zone_id)
    for h in forecast["horizons"]:
        days = h["horizon_hours"] // 24
        st.subheader(f"D+{days} — {h['target_local_date']}")
        st.write(
            f"Predicted daily max AQI: **{h['predicted_aqi']:.0f}** "
            f"({h['category_en']} / {h['category_ur']})"
        )


def page_why(zone_id: str) -> None:
    st.header("Why")
    horizon_hours = st.selectbox(
        "Horizon", options=[24, 48, 72], format_func=lambda h: f"D+{h // 24}"
    )
    explanation = get_explain(zone_id, horizon_hours)
    st.warning(explanation["explainer_note"])
    st.write(explanation["briefing_en"])
    st.write(explanation["briefing_ur"])
    st.subheader("Top drivers")
    st.table(
        [
            {
                "Feature": d["feature_label_en"],
                "خصوصیت": d["feature_label_ur"],
                "Value": d["value"],
                "SHAP contribution": round(d["shap_value"], 2),
            }
            for d in explanation["top_drivers"]
        ]
    )


def page_model_card() -> None:
    st.header("Model card")
    metrics = get_metrics()
    st.subheader("Champion")
    st.write(
        f"**{metrics['champion_model']}** — mean RMSE "
        f"{metrics['champion_mean_rmse']:.2f} across h24/h48/h72"
    )
    st.caption(metrics["primary_metric_note"])
    st.subheader("Every ladder rung (mean RMSE across horizons)")
    st.table(
        [
            {"Model": name, "Mean RMSE": round(rmse, 2)}
            for name, rmse in sorted(
                metrics["mean_rmse_by_model"].items(), key=lambda kv: kv[1]
            )
        ]
    )
    st.caption(f"Ladder generated at {metrics['generated_at']}")

    st.subheader("Live scorecard — not shown")
    ledger = get_ledger_summary()
    st.warning(
        "Skipped this session (docs/DECISIONS.md): the observed-conditions "
        "ledger holds "
        f"**{ledger['row_count']} rows**, spanning **{ledger['start']}** to "
        f"**{ledger['end']}**. A win/loss scorecard against AQICN built from "
        "under a day of history would be dishonest (CLAUDE.md I4) — the "
        "clock starter is live and hourly, so this section becomes real once "
        "the ledger has weeks, not hours, behind it."
    )


def main() -> None:
    st.set_page_config(page_title="Pearls AQI Predictor", layout="wide")
    st.sidebar.title("Pearls AQI Predictor")
    if STATIC_MODE:
        st.sidebar.caption("Static mode — reading reports/dashboard_snapshot.json")
    zone_id = _zone_picker()
    page = st.sidebar.radio("Page", ["Now", "3-day forecast", "Why", "Model card"])

    if page == "Now":
        page_now(zone_id)
    elif page == "3-day forecast":
        page_forecast(zone_id)
    elif page == "Why":
        page_why(zone_id)
    else:
        page_model_card()


if __name__ == "__main__":
    main()
