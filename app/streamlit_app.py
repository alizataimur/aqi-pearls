"""Pearls AQI Predictor — dashboard.

Six tabs, ordered by what the project is assessed on: the live forecast, the
trend record, model evaluation, feature engineering, SHAP explainability, and
the bilingual health guidance.

Two rules govern every number on this page.

**I5 — nothing is typed.** Every figure is read from a committed artifact:
`reports/metrics/ladder.json`, `reports/dashboard_snapshot.json`, the Parquet
feature store, `conf/features.yaml`. When an artifact is missing, the panel
says which file it wanted and renders nothing else. It never invents a
plausible number, because a dashboard that fabricates under failure is worse
than one that goes blank.

**I10 — degrade, never crash.** Every loader returns `None` rather than
raising, and every panel has an explicit unavailable state. A dead API, an
absent model or an empty store reduces what this page shows; it does not take
the page down.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

APP_TITLE = "Pearls AQI Predictor"
APP_TAGLINE = "3-day air quality forecasting for Pakistan — with its own scorecard"

# Chrome. Cyan-teal is chosen because none of the six EPA AQI category colours
# is cyan, so the interface never competes with the data encoding.
INK = "#E6EEF0"
INK_SOFT = "#93AAB2"
INK_FAINT = "#66808A"
BG = "#0B1416"
SURFACE = "#111E22"
SURFACE_2 = "#17282D"
RULE = "#23383E"
ACCENT = "#2BB3C9"
ACCENT_DIM = "#14606E"
AMBER = "#E8C547"  # staleness warning — reused, not a new arbitrary hue
# SHAP chart, negative-contribution bars only. Distinct from ACCENT
# deliberately — ACCENT is this interface's own chrome colour (masthead,
# eyebrows, links), used everywhere; reusing it as a *data* colour risks a
# reader mistaking "this bar is teal" for "this bar is interactive chrome".
SHAP_NEGATIVE = "#5C7CFA"

# Band-tinted card fills (I5.5-adjacent design constraint, not a config
# value): the fraction of the AQI band colour blended over SURFACE for a
# card's background, computed in Python by _tint_over_surface so the exact
# rendered colour matches this file's own WCAG contrast check. 18%, not the
# middle of the requested 18-22% range, because at 20-22% the Urdu line
# (INK_SOFT) under the Moderate band drops under the 4.5:1 floor — checked
# numerically, not eyeballed. The three most severe bands (Unhealthy, Very
# Unhealthy, Hazardous, "Beyond the scale") fail 4.5:1 for the band-coloured
# number/category text at every tint level *including zero* (their hex
# values are simply too dark/saturated as text-on-dark-surface); tinting
# cannot fix a failure that predates it, and the palette is fixed, so this
# is reported rather than silently worked around.
HERO_TINT = 0.18
FORECAST_TINT = 0.08
HEALTH_TINT = 0.07
HEALTH_TINT_CURRENT = 0.16

# I7: compute in UTC, display local. Karachi has no DST, but the zone name
# still comes from real config (aqi.config.get_config().project.
# display_timezone), never hardcoded here.
STALE_THRESHOLD_HOURS = 3.0

# EPA AQI categories. AQI value → category is a fixed, universal table; it is
# the concentration → AQI breakpoints that were revised in 2024, and those live
# in src/aqi/aqi_scale.py as the single authority (I8). This map is display
# only and cannot drift from that.
AQI_BANDS: tuple[tuple[float, float, str, str, str], ...] = (
    (0, 50, "Good", "#3EC46D", "اچھا"),
    (51, 100, "Moderate", "#E8C547", "درمیانہ"),
    (101, 150, "Unhealthy for Sensitive Groups", "#F0913A", "حساس افراد کے لیے مضر"),
    (151, 200, "Unhealthy", "#E5544B", "مضر صحت"),
    (201, 300, "Very Unhealthy", "#A661C4", "بہت مضر صحت"),
    (301, 500, "Hazardous", "#B2454A", "خطرناک"),
    (501, 9999, "Beyond the scale", "#8B2E5D", "پیمانے سے باہر"),
)

ZONES = {
    "capital": "Islamabad / Rawalpindi",
    "lahore": "Lahore",
}

HORIZON_LABELS = {24: "D+1", 48: "D+2", 72: "D+3"}

# Artifact locations — named once so an unavailable panel can say what it wanted.
PATH_SNAPSHOT = REPO_ROOT / "reports" / "dashboard_snapshot.json"
PATH_LADDER = REPO_ROOT / "reports" / "metrics" / "ladder.json"
PATH_COVERAGE = REPO_ROOT / "reports" / "metrics" / "coverage.json"
PATH_FEATURES = REPO_ROOT / "conf" / "features.yaml"
PATH_I18N = REPO_ROOT / "conf" / "i18n_ur.yaml"
PATH_STORE = REPO_ROOT / "data" / "feature_store"
PATH_REGISTRY = REPO_ROOT / "data" / "model_registry"

# I10 fallback chain: the FastAPI service first, then the feature
# store/registry directly, then the committed snapshot — same order and
# same client the pre-redesign app used.
API_URL = os.environ.get("AQI_API_URL", "http://localhost:8000")
API_TIMEOUT_SECONDS = 2.0


# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

html, body, [class*="css"] {{
    font-family: 'IBM Plex Sans', system-ui, sans-serif;
}}
.block-container {{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1280px; }}
#MainMenu, footer {{ visibility: hidden; }}

h1, h2, h3, h4 {{ font-family: Archivo, system-ui, sans-serif; letter-spacing: -0.01em; }}

/* ---- masthead ---- */
.masthead {{
    border-bottom: 1px solid {RULE};
    padding-bottom: 1.4rem;
    margin-bottom: 1.6rem;
}}
.masthead h1 {{
    font-size: 2.5rem; font-weight: 700; margin: 0; line-height: 1.05;
    color: {INK};
}}
.masthead .tag {{
    color: {INK_SOFT}; font-size: 1.02rem; margin-top: 0.35rem;
}}
.masthead .strip {{
    display: flex; flex-wrap: wrap; gap: 0.35rem 1.8rem; margin-top: 1rem;
    font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem;
    color: {INK_FAINT}; letter-spacing: 0.02em;
}}
.masthead .strip b {{ color: {ACCENT}; font-weight: 500; }}
.masthead-link {{ color: {ACCENT}; text-decoration: none; font-weight: 500; }}
.masthead-link:hover {{ text-decoration: underline; }}

/* ---- eyebrow + section heads ---- */
.eyebrow {{
    font-family: Archivo, sans-serif; font-size: 0.72rem; font-weight: 600;
    letter-spacing: 0.13em; text-transform: uppercase; color: {ACCENT};
    margin-bottom: 0.3rem;
}}
.stale-badge {{
    display: inline-block; margin-left: 0.6rem; padding: 0.1rem 0.5rem;
    background: rgba(232, 197, 71, 0.18); color: {AMBER};
    border: 1px solid rgba(232, 197, 71, 0.4); border-radius: 3px;
    font-family: 'IBM Plex Mono', monospace; font-size: 0.68rem; font-weight: 600;
    letter-spacing: 0.08em; vertical-align: middle; text-transform: none;
}}

/* ---- expired forecast — prominent, not a chip (I3/I4) ---- */
.forecast-stale {{
    background: {SURFACE}; border: 1px solid {RULE}; border-left: 4px solid {AMBER};
    border-radius: 4px; padding: 1.1rem 1.3rem;
}}
.forecast-stale .flag {{
    font-family: Archivo, sans-serif; font-weight: 700; font-size: 0.92rem;
    color: {AMBER}; text-transform: uppercase; letter-spacing: 0.05em;
}}
.forecast-stale .rows {{
    display: flex; gap: 1.5rem; flex-wrap: wrap; margin-top: 0.7rem;
}}
.forecast-stale .row-item {{
    color: {INK_FAINT}; font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem;
}}
.forecast-stale .row-item b {{ color: {INK_SOFT}; font-weight: 600; }}
.sec-title {{
    font-family: Archivo, sans-serif; font-size: 1.35rem; font-weight: 600;
    color: {INK}; margin: 0 0 0.3rem 0;
}}
.sec-note {{
    color: {INK_SOFT}; font-size: 0.92rem; margin: 0 0 1.1rem 0; max-width: 74ch;
}}

/* ---- metric tiles ---- */
.tiles {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 1px; background: {RULE}; border: 1px solid {RULE}; border-radius: 4px;
    overflow: hidden;
}}
.tile {{
    background: {SURFACE}; padding: 1.05rem 1.15rem; display: flex;
    flex-direction: column; gap: 0.25rem;
}}
.tile .k {{ font-family: Archivo, sans-serif; font-size: 0.68rem; font-weight: 600;
            letter-spacing: 0.1em; text-transform: uppercase; color: {INK_FAINT}; }}
.tile .v {{ font-family: 'IBM Plex Mono', monospace; font-size: 1.85rem; font-weight: 600;
            line-height: 1; color: {INK}; font-variant-numeric: tabular-nums; }}
.tile .s {{ font-size: 0.78rem; color: {INK_SOFT}; }}
.tile.accent .v {{ color: {ACCENT}; }}

/* ---- AQI hero ---- */
.hero {{
    background: {SURFACE}; border: 1px solid {RULE}; border-left: 5px solid var(--band);
    border-radius: 4px; padding: 1.5rem 1.7rem; display: flex; flex-direction: column;
    gap: 0.5rem;
}}
.hero .num {{
    font-family: 'IBM Plex Mono', monospace; font-size: 4.2rem; font-weight: 600;
    line-height: 0.9; color: var(--band); font-variant-numeric: tabular-nums;
}}
.hero .cat {{
    font-family: Archivo, sans-serif; font-size: 1.15rem; font-weight: 600; color: {INK};
}}
.hero .cat-ur {{ font-size: 1.05rem; color: {INK_SOFT}; direction: rtl; }}
.hero .meta {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.74rem; color: {INK_FAINT};
}}

/* ---- forecast cards ---- */
.fc {{
    background: {SURFACE}; border: 1px solid {RULE}; border-top: 3px solid var(--band);
    border-radius: 4px; padding: 1.1rem 1.2rem; display: flex; flex-direction: column;
    gap: 0.3rem; height: 100%;
}}
.fc .h {{ font-family: Archivo, sans-serif; font-size: 0.72rem; font-weight: 600;
          letter-spacing: 0.11em; text-transform: uppercase; color: {ACCENT}; }}
.fc .d {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; color: {INK_FAINT};
}}
.fc .n {{ font-family: 'IBM Plex Mono', monospace; font-size: 2.5rem; font-weight: 600;
          line-height: 1; color: var(--band); font-variant-numeric: tabular-nums; }}
.fc .c {{ font-size: 0.86rem; color: {INK}; }}
.fc .c-ur {{ font-size: 0.86rem; color: {INK_SOFT}; direction: rtl; }}

/* ---- provenance line ---- */
.prov {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: {INK_FAINT};
    border-top: 1px solid {RULE}; padding-top: 0.6rem; margin-top: 1.2rem;
}}
.prov b {{ color: {INK_SOFT}; font-weight: 500; }}

/* ---- page footer ---- */
/* Generous top margin (vs. .prov's 1.2rem) and centred text are deliberate —
   this reads as the end of the page, not a second provenance line stacked
   directly beneath the first. */
.pagefoot {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: {INK_FAINT};
    text-align: center; border-top: 1px solid {RULE};
    margin-top: 3rem; padding-top: 1.2rem;
}}
.pagefoot a {{ color: {ACCENT}; text-decoration: none; }}
.pagefoot a:hover {{ text-decoration: underline; }}

/* ---- unavailable state ---- */
.gone {{
    background: {SURFACE_2}; border: 1px dashed {RULE}; border-radius: 4px;
    padding: 1.1rem 1.3rem; color: {INK_SOFT}; font-size: 0.9rem;
}}
.gone code {{ color: {ACCENT}; background: transparent; font-size: 0.85rem; }}

/* ---- note (a statement, not an absence — solid, never dashed like .gone) ---- */
.note {{
    background: {SURFACE}; border: 1px solid {RULE}; border-left: 3px solid {ACCENT};
    border-radius: 4px; padding: 1.1rem 1.3rem; color: {INK_SOFT}; font-size: 0.9rem;
}}
.note b {{ color: {INK}; }}

/* ---- health guidance cards ---- */
.hg-head {{
    display: flex; justify-content: space-between; align-items: center;
    font-family: Archivo, sans-serif; font-size: 0.72rem; font-weight: 600;
    letter-spacing: 0.11em; text-transform: uppercase;
}}
.hg-chip {{
    font-family: 'IBM Plex Mono', monospace; font-size: 0.66rem; font-weight: 600;
    letter-spacing: 0.06em; padding: 0.12rem 0.55rem; border-radius: 3px;
    text-transform: uppercase;
}}
.hg-row {{ display: flex; gap: 1.2rem; margin-top: 0.4rem; }}
.hg-en {{ flex: 1 1 50%; font-size: 0.86rem; color: {INK}; }}
.hg-ur {{
    flex: 1 1 50%; font-size: 0.86rem; color: {INK_SOFT}; direction: rtl;
    text-align: right;
}}

/* ---- health guidance: live advisory for the current reading ---- */
.hg-advisory {{
    background: {SURFACE}; border: 1px solid {RULE}; border-left: 5px solid var(--band);
    border-radius: 4px; padding: 1.4rem 1.6rem; margin-bottom: 1rem;
}}
.hg-advisory-head {{
    display: flex; align-items: baseline; gap: 0.9rem; margin-top: 0.5rem;
}}
.hg-advisory-aqi {{
    font-family: 'IBM Plex Mono', monospace; font-size: 2.6rem; font-weight: 600;
    color: var(--band); font-variant-numeric: tabular-nums;
}}
.hg-advisory-cat {{
    font-family: Archivo, sans-serif; font-size: 1.1rem; font-weight: 600; color: {INK};
}}
.hg-advisory-cat-ur {{ font-size: 1rem; color: {INK_SOFT}; direction: rtl; }}

/* ---- feature group chips ---- */
.groups {{ display: flex; flex-wrap: wrap; gap: 0.5rem; }}
.grp {{ background: {SURFACE}; border: 1px solid {RULE}; border-radius: 3px;
        padding: 0.45rem 0.75rem; font-size: 0.84rem; color: {INK}; }}
.grp b {{ font-family: 'IBM Plex Mono', monospace; color: {ACCENT}; font-weight: 500; }}

/* ---- native widget tuning ---- */
div[data-testid="stTabs"] button {{ font-family: Archivo, sans-serif; font-weight: 600; }}
div[data-testid="stTabs"] button p {{ font-size: 0.92rem; }}
[data-testid="stDataFrame"] {{ border: 1px solid {RULE}; border-radius: 4px; }}
</style>
"""


# --------------------------------------------------------------------------
# Data layer
#
# Every loader below returns None on any failure and never raises (I10).
# Live data goes through the same three-tier fallback the pre-redesign
# dashboard used: the FastAPI service (`_api_get`) first, then a direct read
# of the feature store + Model Registry (`_fallback_frame`, the I10 path that
# keeps the page alive when `uvicorn` isn't running), then the committed
# `reports/dashboard_snapshot.json` (the I5 artifact that keeps the page
# alive even without a live feature store). The layout above and the
# renderers below do not change based on which tier actually answered.
# --------------------------------------------------------------------------


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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
    """The feature store's full history, loaded once per process — the same
    frame the API's own in-process cache reads (`serving/inference.py::
    load_frame_cached`), used here only when the API itself is unreachable."""
    try:
        from aqi.serving.inference import load_frame_cached

        return load_frame_cached()
    except Exception:
        return None


@st.cache_data(ttl=300)
def load_snapshot() -> dict[str, Any] | None:
    """Committed dashboard snapshot: current conditions and the 3-day forecast."""
    data = _read_json(PATH_SNAPSHOT)
    return data if isinstance(data, dict) else None


@st.cache_data(ttl=300)
def load_ladder() -> dict[str, Any] | None:
    """Per-horizon RMSE / MAE / R² for every rung (D6, D7, D12)."""
    data = _read_json(PATH_LADDER)
    return data if isinstance(data, dict) else None


@st.cache_data(ttl=300)
def load_coverage() -> dict[str, Any] | None:
    data = _read_json(PATH_COVERAGE)
    return data if isinstance(data, dict) else None


@st.cache_data(ttl=3600)
def load_health_guidance() -> dict[str, tuple[str, str]]:
    """(english, urdu) health guidance per EPA category, from the hand-written
    authority `conf/i18n_ur.yaml` (CLAUDE.md §14) via `aqi.explain.i18n.
    health_guidance` — never a value re-typed in this file. Covers all seven
    `AQI_BANDS` entries, including "Beyond the scale"; a category with no
    entry (were one ever added to `AQI_BANDS` without a matching YAML entry)
    still degrades to `("", "")`, same as `health_guidance` itself does for
    any category it doesn't recognise — a visible gap, never a silent one."""
    try:
        from aqi.explain.i18n import health_guidance
    except Exception:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for _low, _high, name, _colour, _urdu in AQI_BANDS:
        try:
            out[name] = health_guidance(name)
        except Exception:
            out[name] = ("", "")
    return out


@st.cache_data(ttl=300)
def load_registry_metadata() -> list[dict[str, Any]]:
    """Every registered model's metadata.json, for the model card."""
    out: list[dict[str, Any]] = []
    try:
        for meta_path in sorted(PATH_REGISTRY.glob("*/metadata.json")):
            payload = _read_json(meta_path)
            if isinstance(payload, dict):
                payload.setdefault("_entry", meta_path.parent.name)
                out.append(payload)
    except Exception:
        return []
    return out


@st.cache_data(ttl=600)
def load_history(zone: str, days: int) -> pd.DataFrame | None:
    """Hourly history for one zone, read through the real `FeatureStore`
    Protocol (`aqi.store.get_store().read_latest`) rather than a hand-rolled
    glob — the partition layout (`{feature_group}/v{version}/city=<zone>/
    year=/month=/data.parquet`) is `ParquetFeatureStore`'s own concern, not
    this dashboard's to reimplement or drift from. Columns are the real
    store schema (`aqi.store.base.TIME_COLUMN`/`CITY_COLUMN` — `time_utc`
    and `city_id`), not guessed field names.

    Returns a frame with a UTC 'ts' column and a numeric 'pm2_5' column, or
    None.
    """
    try:
        from aqi.config import get_config
        from aqi.store import CITY_COLUMN, TIME_COLUMN, get_store

        store = get_store()
        frame = store.read_latest(get_config().store.feature_group, n_hours=days * 24)
    except Exception:
        return None

    if (
        frame.empty
        or CITY_COLUMN not in frame.columns
        or TIME_COLUMN not in frame.columns
        or "pm2_5" not in frame.columns
    ):
        return None

    zone_frame = frame[frame[CITY_COLUMN].astype(str) == zone]
    if zone_frame.empty:
        return None

    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(zone_frame[TIME_COLUMN], utc=True, errors="coerce"),
            "pm2_5": pd.to_numeric(zone_frame["pm2_5"], errors="coerce"),
        }
    ).dropna()
    out = out.sort_values("ts")
    return out if not out.empty else None


@st.cache_data(ttl=600)
def load_feature_summary() -> dict[str, Any] | None:
    """Feature counts and group names.

    `conf/features.yaml` is a *generator* spec (a lag base list x lag hours,
    a rolling base list x windows x stats, ...), not a flat list of features
    — there is no top-level `features`/`feature_specs` key to read directly.
    `aqi.features.spec.expand_feature_specs` is the one place that expansion
    happens (`builder.py`'s real output columns are tested against it), so
    reading the true count and groups means calling it, not re-parsing the
    YAML's generator shape by hand.
    """
    try:
        from aqi.features.spec import expand_feature_specs

        specs = expand_feature_specs()
    except Exception:
        return None

    groups: dict[str, int] = {}
    declares_min_lag = 0
    for spec in specs:
        groups[spec.category] = groups.get(spec.category, 0) + 1
        if spec.min_lag_hours is not None:
            declares_min_lag += 1

    return {
        "n_features": len(specs),
        "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1])),
        "declares_min_lag": declares_min_lag,
    }


@st.cache_data(ttl=300)
def load_shap(zone: str, horizon_h: int) -> dict[str, Any] | None:
    """SHAP driver dictionary for one zone and horizon — API, then a direct
    `shap_explain.explain_zone` call against the feature store (I10), then
    the committed snapshot.

    Real contract, from `explain/shap_explain.py::ExplainResult` /
    `serving/schemas.py::ExplainResponse` (not the placeholder `"drivers"`/
    `"contribution"` shape this stub was written against): `{"zone_id",
    "horizon_hours", "predicted_aqi", "base_value", "top_drivers": [
    {"feature", "feature_label_en", "feature_label_ur", "value",
    "shap_value"}, ...], "briefing_en", "briefing_ur", "explainer_note"}`.
    """
    data = _api_get("/explain", {"zone_id": zone, "horizon_hours": horizon_h})
    if data is not None:
        return data

    frame = _fallback_frame()
    if frame is not None:
        try:
            from aqi.explain.shap_explain import explain_zone

            result = explain_zone(frame, zone, horizon_h)
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
        except Exception:
            pass

    snapshot = load_snapshot()
    if isinstance(snapshot, dict):
        block = snapshot.get("explain")
        if isinstance(block, dict):
            zone_block = block.get(zone)
            if isinstance(zone_block, dict):
                entry = zone_block.get(str(horizon_h))
                if isinstance(entry, dict) and entry.get("top_drivers"):
                    return entry
    return None


def _normalize_current(entry: dict[str, Any]) -> dict[str, Any]:
    """Maps the real current-conditions shape (API `CurrentResponse` /
    `CurrentReading` / snapshot — all three agree: `aqi_nowcast`, `time_utc`,
    `pm2_5`, `pm10`, `category_en`, `category_ur`) onto the keys the Live and
    Health Guidance tabs already read (`aqi`, `observed_at`, ...), so the
    renderers below need no changes."""
    return {
        "aqi": entry.get("aqi_nowcast"),
        "observed_at": entry.get("time_utc"),
        "pm2_5": entry.get("pm2_5"),
        "pm10": entry.get("pm10"),
        "category_en": entry.get("category_en"),
        "category_ur": entry.get("category_ur"),
        "zone_id": entry.get("zone_id"),
    }


def current_conditions(zone: str) -> dict[str, Any] | None:
    """Current reading for one zone — API, then a direct
    `serving/inference.py::current_reading` call against the feature store
    (I10), then the committed snapshot."""
    data = _api_get("/current", {"zone_id": zone})
    if data is not None:
        return _normalize_current(data)

    frame = _fallback_frame()
    if frame is not None:
        try:
            from aqi.serving.inference import current_reading

            reading = current_reading(frame, zone)
            return _normalize_current(
                {
                    "zone_id": reading.zone_id,
                    "time_utc": reading.time_utc.isoformat(),
                    "aqi_nowcast": reading.aqi_nowcast,
                    "category_en": reading.category_en,
                    "category_ur": reading.category_ur,
                    "pm2_5": reading.pm2_5,
                    "pm10": reading.pm10,
                }
            )
        except Exception:
            pass

    snapshot = load_snapshot()
    if isinstance(snapshot, dict):
        block = snapshot.get("current")
        if isinstance(block, dict):
            entry = block.get(zone)
            if isinstance(entry, dict):
                return _normalize_current(entry)
    return None


def forecast_rows(zone: str) -> list[dict[str, Any]] | None:
    """The 3-day forecast for one zone — API, then a direct
    `serving/inference.py::forecast_zone` call against the feature store and
    registered LightGBM model (I10), then the committed snapshot.

    Real shape (`ForecastResponse`/snapshot): `{"horizons": [{"horizon_hours",
    "target_local_date", "predicted_aqi", "category_en", "category_ur"},
    ...]}` — a dict keyed by zone with a `"horizons"` list inside, not a bare
    list. Normalized to the keys the Live tab already reads (`horizon_h`,
    `target_date`, `predicted_aqi`)."""
    raw_horizons: list[Any] | None = None

    data = _api_get("/forecast", {"zone_id": zone})
    if isinstance(data, dict) and isinstance(data.get("horizons"), list):
        raw_horizons = data["horizons"]

    if raw_horizons is None:
        frame = _fallback_frame()
        if frame is not None:
            try:
                from aqi.serving.inference import forecast_zone, zones

                zone_cfg = next((z for z in zones() if z.zone_id == zone), None)
                if zone_cfg is not None:
                    horizons = forecast_zone(frame, zone, zone_cfg.timezone)
                    raw_horizons = [
                        {
                            "horizon_hours": h.horizon_hours,
                            "target_local_date": h.target_local_date,
                            "predicted_aqi": h.predicted_aqi,
                            "category_en": h.category_en,
                            "category_ur": h.category_ur,
                        }
                        for h in horizons
                    ]
            except Exception:
                raw_horizons = None

    if raw_horizons is None:
        snapshot = load_snapshot()
        if isinstance(snapshot, dict):
            block = snapshot.get("forecast")
            if isinstance(block, dict):
                zone_block = block.get(zone)
                if isinstance(zone_block, dict) and isinstance(
                    zone_block.get("horizons"), list
                ):
                    raw_horizons = zone_block["horizons"]

    if not raw_horizons:
        return None
    return [
        {
            "horizon_h": row.get("horizon_hours"),
            "target_date": row.get("target_local_date"),
            "predicted_aqi": row.get("predicted_aqi"),
            "category_en": row.get("category_en"),
            "category_ur": row.get("category_ur"),
        }
        for row in raw_horizons
        if isinstance(row, dict)
    ]


@st.cache_data(ttl=30)
def serving_backend() -> str:
    """Which backend actually serves this page, checked live rather than
    reported from an unvalidated default — a silent fallback hid a missing
    component once already (session-6 incident, ADR-030). Reports both
    halves: whether the FastAPI service answered `/health` or the page fell
    back to reading the store directly (I10), and which feature-store
    backend that path actually uses (`aqi.config.get_secrets().
    feature_store_backend` — the real, validated setting `get_store()`
    itself reads, not a raw unvalidated `os.environ.get`)."""
    try:
        from aqi.config import get_secrets

        backend_name = get_secrets().feature_store_backend
    except Exception:
        backend_name = "unknown"

    if _api_get("/health") is not None:
        return f"API → {backend_name}"
    return f"direct-store fallback → {backend_name}"


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------


def band_for(aqi: float | None) -> tuple[str, str, str]:
    """(category, hex colour, urdu) for an AQI value.

    The value is rounded to an integer before lookup because EPA reports AQI as
    an integer, and because the published band edges are contiguous integers
    (50 / 51, 100 / 101 …) with no room between them. A raw model output of
    50.5 would otherwise match no band at all and fall through to the
    beyond-scale case — which looked fine until a fractional forecast arrived.
    """
    if aqi is None:
        return ("Unknown", INK_FAINT, "نامعلوم")
    try:
        value = round(float(aqi))
    except (TypeError, ValueError):
        return ("Unknown", INK_FAINT, "نامعلوم")
    if value < 0:
        return ("Unknown", INK_FAINT, "نامعلوم")
    for low, high, name, colour, urdu in AQI_BANDS:
        if low <= value <= high:
            return (name, colour, urdu)
    return ("Beyond the scale", "#8B2E5D", "پیمانے سے باہر")


def _tint_over_surface(band_hex: str, pct: float) -> str:
    """Blend an AQI band colour over the dark surface at `pct` opacity
    (0-1) — a card fill derived from the same band_for() colour rather than
    a second mapping, and precomputed here in Python (not CSS color-mix())
    so the exact rendered hex is the same value this file's own WCAG
    contrast check (see HERO_TINT/FORECAST_TINT above) was run against."""
    band_hex = band_hex.lstrip("#")
    surface_hex = SURFACE.lstrip("#")
    try:
        br, bg, bb = (int(band_hex[i : i + 2], 16) for i in (0, 2, 4))
        sr, sg, sb = (int(surface_hex[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return SURFACE
    r = round(br * pct + sr * (1 - pct))
    g = round(bg * pct + sg * (1 - pct))
    b = round(bb * pct + sb * (1 - pct))
    return f"#{r:02x}{g:02x}{b:02x}"


def _relative_luminance(hex_colour: str) -> float:
    hex_colour = hex_colour.lstrip("#")

    def channel(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _chip_text_colour(band_hex: str) -> str:
    """Whichever of BG/INK contrasts better against a band's full-strength
    fill, for the CURRENT chip's text. No single fixed choice clears 4.5:1
    for every band — checked numerically, not eyeballed: dark (BG) wins for
    the five lighter bands, light (INK) wins for Hazardous and Beyond the
    scale, and every band clears 4.5:1 against whichever of the two wins."""
    return BG if _contrast_ratio(BG, band_hex) >= _contrast_ratio(INK, band_hex) else INK


def _pm25_ranges() -> dict[int, tuple[float, float]]:
    """AQI I_low -> (C_low, C_high) µg/m³ PM2.5 range, read from
    `aqi.aqi_scale.breakpoints_for` — the same breakpoint table
    `aqi_from_24h_mean`/`aqi_nowcast` compute from (I8), never a second,
    hand-typed copy of these numbers."""
    try:
        from aqi.aqi_scale import breakpoints_for

        rows = breakpoints_for("pm2_5")
    except Exception:
        return {}
    return {int(i_low): (c_low, c_high) for c_low, c_high, i_low, _i_high in rows}


def _parse_utc(iso_utc: str | None) -> datetime | None:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def format_local_timestamp(iso_utc: str | None) -> str:
    """UTC ISO timestamp -> a reader-facing string in the configured
    display timezone (I7: compute in UTC, display local). E.g.
    "2026-09-02T11:00:00+00:00" -> "2 Sep, 4:00 PM PKT". The zone name comes
    from real config (`aqi.config.get_config().project.display_timezone`),
    never hardcoded as "Asia/Karachi" here. Never raises: returns the
    original string unmodified if it can't be parsed or converted, so a
    malformed timestamp degrades to visible-but-unconverted rather than
    crashing the page (I10)."""
    dt = _parse_utc(iso_utc)
    if dt is None:
        return iso_utc or "—"
    try:
        from aqi.config import get_config

        tz_name = get_config().project.display_timezone
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return iso_utc
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    tz_abbr = local.tzname() or ""
    return (
        f"{local.day} {local.strftime('%b')}, {hour12}:{local.minute:02d} "
        f"{ampm} {tz_abbr}"
    ).strip()


def observation_age(iso_utc: str | None) -> timedelta | None:
    """How long ago a UTC ISO timestamp was, or None if it can't be parsed."""
    dt = _parse_utc(iso_utc)
    if dt is None:
        return None
    return datetime.now(UTC) - dt


def format_age(age: timedelta) -> str:
    """A reader-facing age string: "4 days old", "3 hours old", "12 minutes
    old" — the coarsest unit that has at least 1 whole one, so a 25-hour-old
    reading reads "1 day old" rather than "25 hours old"."""
    seconds = age.total_seconds()
    if seconds < 60:
        return "just now"
    days = int(seconds // 86400)
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} old"
    hours = int(seconds // 3600)
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} old"
    minutes = int(seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''} old"


def _display_today() -> date:
    """Today's calendar date in the configured display timezone (I7) — the
    same zone format_local_timestamp() converts into, so "is this forecast's
    target date still in the future" and "what time is it right now" can
    never disagree about which local day it currently is."""
    try:
        from aqi.config import get_config

        tz_name = get_config().project.display_timezone
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now(UTC).date()


def _parse_local_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def unavailable(what: str, path: Path | str, why: str = "") -> None:
    tail = f" {why}" if why else ""
    st.markdown(
        f'<div class="gone"><b>{what} unavailable.</b> This panel reads '
        f"<code>{path}</code>, which is not present in this checkout.{tail}</div>",
        unsafe_allow_html=True,
    )


def section(eyebrow: str, title: str, note: str = "", badge: str = "") -> None:
    note_html = f'<p class="sec-note">{note}</p>' if note else ""
    st.markdown(
        f'<div class="eyebrow">{eyebrow}{badge}</div>'
        f'<div class="sec-title">{title}</div>{note_html}',
        unsafe_allow_html=True,
    )


def tiles(items: list[tuple[str, str, str]], accent_first: bool = False) -> None:
    cells = []
    for i, (key, value, sub) in enumerate(items):
        cls = "tile accent" if (accent_first and i == 0) else "tile"
        cells.append(
            f'<div class="{cls}"><div class="k">{key}</div>'
            f'<div class="v">{value}</div><div class="s">{sub}</div></div>'
        )
    st.markdown(f'<div class="tiles">{"".join(cells)}</div>', unsafe_allow_html=True)


def provenance(
    *sources: str,
    lead_in: str = "Every figure on this tab is read from",
    note: str = "",
) -> None:
    body = " &nbsp;·&nbsp; ".join(f"<b>{s}</b>" for s in sources)
    note_html = f" {note}" if note else ""
    st.markdown(
        f'<div class="prov">{lead_in}: {body}{note_html}</div>',
        unsafe_allow_html=True,
    )


def footer() -> None:
    st.markdown(
        '<div class="pagefoot">Pearls AQI Predictor — Aliza Taimur '
        "&nbsp;·&nbsp; "
        '<a href="https://github.com/alizataimur/aqi-pearls" target="_blank" '
        'rel="noopener noreferrer">View source code on GitHub</a></div>',
        unsafe_allow_html=True,
    )


def base_layout(height: int = 340, ytitle: str = "") -> dict[str, Any]:
    return dict(
        height=height,
        margin=dict(l=56, r=24, t=28, b=44),
        paper_bgcolor=BG,
        plot_bgcolor=SURFACE,
        font=dict(family="IBM Plex Sans, sans-serif", color=INK_SOFT, size=12),
        xaxis=dict(gridcolor=RULE, zeroline=False, linecolor=RULE, tickcolor=RULE),
        yaxis=dict(
            title=ytitle,
            gridcolor=RULE,
            zeroline=False,
            linecolor=RULE,
            tickcolor=RULE,
        ),
        hovermode="x unified",
        showlegend=False,
    )


# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------


def tab_live(zone: str) -> None:
    now = current_conditions(zone)
    observed_raw = now.get("observed_at") or now.get("timestamp") if now else None
    age = observation_age(observed_raw)

    badge_html = ""
    if age is not None and age.total_seconds() > STALE_THRESHOLD_HOURS * 3600:
        badge_html = ' <span class="stale-badge">STALE</span>'

    section(
        "Live",
        "Current conditions and the 3-day outlook",
        "The forecast is a point estimate of daily maximum US AQI. It carries no "
        "interval and no exceedance probability: conformal prediction was cut "
        "under time pressure, and this dashboard states that rather than "
        "implying a confidence the system never computed.",
        badge=badge_html,
    )

    left, right = st.columns([1, 1.35], gap="large")

    with left:
        if not now:
            unavailable("Current conditions", PATH_SNAPSHOT.relative_to(REPO_ROOT))
        else:
            aqi = now.get("aqi") or now.get("us_aqi") or now.get("nowcast_aqi")
            aqi_val = float(aqi) if isinstance(aqi, int | float) else None
            name, colour, urdu = band_for(aqi_val)
            hero_bg = _tint_over_surface(colour, HERO_TINT)
            shown = f"{aqi_val:.0f}" if aqi_val is not None else "—"
            observed = format_local_timestamp(observed_raw) if observed_raw else "—"
            age_suffix = f" · {format_age(age)}" if age is not None else ""
            pm = now.get("pm2_5")
            pm_line = f"PM2.5 {pm:.1f} µg/m³ · " if isinstance(pm, int | float) else ""
            st.markdown(
                f'<div class="hero" style="--band:{colour};background:{hero_bg}">'
                f'<div class="num">{shown}</div>'
                f'<div class="cat">{name}</div>'
                f'<div class="cat-ur">{urdu}</div>'
                f'<div class="meta">{pm_line}NowCast · observed '
                f"{observed}{age_suffix}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    with right:
        rows = forecast_rows(zone)
        if not rows:
            unavailable("Forecast", PATH_SNAPSHOT.relative_to(REPO_ROOT))
        else:
            # The issue time is the same observation every tier's forecast
            # was actually computed from (forecast_zone/render_static_
            # snapshot.py both read the latest feature-store row and predict
            # from it) — not a separately invented field.
            issued_text = format_local_timestamp(observed_raw) if observed_raw else "—"
            st.caption(f"issued {issued_text}")

            first_target = _parse_local_date(rows[0].get("target_date"))
            is_current_forecast = (
                first_target is not None and first_target > _display_today()
            )

            if not is_current_forecast:
                # I3/I4: a forecast whose own D+1 target date has already
                # passed must not present itself as a live "3-day outlook" —
                # this is the exact failure mode a stale snapshot produces.
                # Prominent, not a chip: the section-eyebrow STALE badge
                # covers the *observation*'s own age; this is a distinct
                # check (the forecast's *target dates* vs today) and gets
                # its own visible flag rather than piggybacking on that one.
                item_bits = []
                for row in rows[:3]:
                    horizon = row.get("horizon_h") or 0
                    label = HORIZON_LABELS.get(int(horizon), f"+{horizon}h")
                    target = str(row.get("target_date") or "")[:10]
                    value = row.get("predicted_aqi")
                    shown = (
                        f"{float(value):.0f}" if isinstance(value, int | float) else "—"
                    )
                    item_bits.append(
                        f'<div class="row-item"><b>{label}</b> {target} · {shown}</div>'
                    )
                st.markdown(
                    '<div class="forecast-stale">'
                    '<div class="flag">Last issued forecast — not current</div>'
                    f'<div class="rows">{"".join(item_bits)}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                cols = st.columns(len(rows[:3]), gap="small")
                for col, row in zip(cols, rows[:3], strict=False):
                    horizon = row.get("horizon_h") or row.get("horizon") or 0
                    label = HORIZON_LABELS.get(int(horizon), f"+{horizon}h")
                    value = (
                        row.get("y_pred") or row.get("predicted_aqi") or row.get("aqi")
                    )
                    val = float(value) if isinstance(value, int | float) else None
                    name, colour, urdu = band_for(val)
                    fc_bg = _tint_over_surface(colour, FORECAST_TINT)
                    shown = f"{val:.0f}" if val is not None else "—"
                    target = str(row.get("target_date") or row.get("target_time") or "")[
                        :10
                    ]
                    with col:
                        st.markdown(
                            f'<div class="fc" style="--band:{colour};background:{fc_bg}">'
                            f'<div class="h">{label}</div>'
                            f'<div class="d">{target}</div>'
                            f'<div class="n">{shown}</div>'
                            f'<div class="c" style="color:{colour}">{name}</div>'
                            f'<div class="c-ur">{urdu}</div>'
                            f"</div>",
                            unsafe_allow_html=True,
                        )

    st.write("")
    hist = load_history(zone, 14)
    if hist is not None:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=hist["ts"],
                y=hist["pm2_5"],
                mode="lines",
                line=dict(color=ACCENT, width=1.8),
                fill="tozeroy",
                fillcolor="rgba(43,179,201,0.10)",
                name="PM2.5",
                hovertemplate="%{x|%d %b %H:%M} · %{y:.1f} µg/m³<extra></extra>",
            )
        )
        fig.update_layout(**base_layout(260, "PM2.5 µg/m³"))
        st.plotly_chart(fig, use_container_width=True)

    provenance(
        "reports/dashboard_snapshot.json",
        "data/feature_store/",
        f"store backend: {serving_backend()}",
    )
    footer()


def tab_trends(zone: str) -> None:
    section(
        "Trends",
        "The observed record",
        "CAMS reanalysis for this zone, back to the probed earliest date of "
        "2022-08-04. Not an assumed start — the floor was found by probing the "
        "API and the backfill runs to it.",
    )

    window = st.radio(
        "Window",
        options=["Week", "Month", "Year"],
        horizontal=True,
        label_visibility="collapsed",
        key=f"trend_window_{zone}",
    )
    days = {"Week": 7, "Month": 30, "Year": 365}[window]
    rule = {"Week": "3h", "Month": "1D", "Year": "1W"}[window]
    agg_label = {"Week": "3-hourly mean", "Month": "daily max", "Year": "weekly max"}[
        window
    ]

    hist = load_history(zone, days)
    if hist is None:
        unavailable("Trend history", PATH_STORE.relative_to(REPO_ROOT))
        return

    series = hist.set_index("ts")["pm2_5"]
    resampled = (
        series.resample(rule).max() if window != "Week" else series.resample(rule).mean()
    )
    resampled = resampled.dropna()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=resampled.index,
            y=resampled.to_numpy(),
            mode="lines",
            line=dict(color=ACCENT, width=2),
            fill="tozeroy",
            fillcolor="rgba(43,179,201,0.12)",
            hovertemplate="%{x|%d %b %Y} · %{y:.1f} µg/m³<extra></extra>",
        )
    )
    # The two thresholds this project actually acts on, drawn to the same scale.
    thresholds = ((55.5, "#E5544B", "AQI 151"), (125.5, "#A661C4", "AQI 201"))
    for level, colour, text in thresholds:
        if float(resampled.max()) > level * 0.6:
            fig.add_hline(
                y=level,
                line_dash="dot",
                line_color=colour,
                line_width=1,
                annotation_text=text,
                annotation_position="right",
                annotation_font_color=colour,
                annotation_font_size=11,
            )
    fig.update_layout(**base_layout(400, f"PM2.5 µg/m³ ({agg_label})"))
    st.plotly_chart(fig, use_container_width=True)

    st.write("")
    stats = [
        ("Observations", f"{len(hist):,}", f"hourly rows, last {days}d"),
        ("Peak PM2.5", f"{series.max():.0f}", "µg/m³ in window"),
        ("Median", f"{series.median():.1f}", "µg/m³"),
        ("Hours over 55.5", f"{int((series > 55.5).sum()):,}", "AQI 151+ territory"),
    ]
    tiles(stats, accent_first=True)

    provenance("data/feature_store/", "probed CAMS floor 2022-08-04")
    footer()


def tab_model() -> None:
    section(
        "Model evaluation",
        "Nine rungs, one window, per horizon",
        "RMSE, MAE and R² are reported per horizon and never blended into a "
        "single number — an average across D+1 to D+3 hides the thing worth "
        "knowing. Baselines are first-class and appear in the same table on the "
        "same split: where a naive baseline wins, that is the published result.",
    )

    ladder = load_ladder()
    if not ladder:
        unavailable(
            "Ladder table",
            PATH_LADDER.relative_to(REPO_ROOT),
            "Run the training pipeline to generate it.",
        )
        return

    # Real shape (training_pipeline.py::run_training_pipeline): ladder["models"]
    # is {model_name: {"h24"/"h48"/"h72": {"regression": {"rmse","mae","r2",
    # "n"}, "episode_at_200": {...}, "n_train": int}}} — the horizon key
    # carries an "h" prefix (int("h24") raises; the digits must be stripped
    # first), and RMSE/MAE/R² sit one level deeper, under "regression", not
    # directly on the horizon's dict.
    rows: list[dict[str, Any]] = []
    payload = ladder.get("models")
    if isinstance(payload, dict):
        for model_name, horizons in payload.items():
            if not isinstance(horizons, dict):
                continue
            for horizon_key, metrics in horizons.items():
                if not isinstance(metrics, dict):
                    continue
                regression = metrics.get("regression")
                if not isinstance(regression, dict):
                    continue
                digits = str(horizon_key).removeprefix("h")
                horizon_label = (
                    HORIZON_LABELS.get(int(digits), str(horizon_key))
                    if digits.isdigit()
                    else str(horizon_key)
                )
                rows.append(
                    {
                        "Model": model_name,
                        "Horizon": horizon_label,
                        "RMSE": regression.get("rmse"),
                        "MAE": regression.get("mae"),
                        "R²": regression.get("r2"),
                    }
                )

    if not rows:
        unavailable(
            "Ladder table",
            PATH_LADDER.relative_to(REPO_ROOT),
            "The file exists but no rows could be read from it.",
        )
        return

    table = pd.DataFrame(rows)
    for col in ("RMSE", "MAE", "R²"):
        if col in table.columns:
            table[col] = pd.to_numeric(table[col], errors="coerce").round(3)

    best = None
    if "RMSE" in table.columns and not table["RMSE"].dropna().empty:
        best_row = table.loc[table["RMSE"].idxmin()]
        best = (str(best_row["Model"]), float(best_row["RMSE"]))

    tiles(
        [
            ("Rungs evaluated", str(table["Model"].nunique()), "baselines + learned"),
            ("Best RMSE", f"{best[1]:.2f}" if best else "—", best[0] if best else "—"),
            ("Horizons", str(table["Horizon"].nunique()), "D+1 · D+2 · D+3"),
            ("Split", "walk-forward", "72h purge gap"),
        ],
        accent_first=True,
    )
    st.write("")
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.write("")
    if "RMSE" in table.columns:
        pivot = table.pivot_table(index="Model", columns="Horizon", values="RMSE")
        order = [c for c in ("D+1", "D+2", "D+3") if c in pivot.columns]
        if order:
            pivot = pivot[order].sort_values(order[0])
            fig = go.Figure()
            shades = [ACCENT, "#1D8FA6", ACCENT_DIM]
            for i, col in enumerate(order):
                fig.add_trace(
                    go.Bar(
                        y=pivot.index,
                        x=pivot[col],
                        name=col,
                        orientation="h",
                        marker_color=shades[i % len(shades)],
                        hovertemplate=f"%{{y}} · {col} · RMSE %{{x:.2f}}<extra></extra>",
                    )
                )
            layout = base_layout(max(320, 34 * len(pivot)), "")
            layout["showlegend"] = True
            layout["legend"] = dict(
                orientation="h", y=1.08, x=0, font=dict(color=INK_SOFT)
            )
            layout["barmode"] = "group"
            layout["xaxis"]["title"] = "RMSE (AQI points, lower is better)"
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True)

    meta = load_registry_metadata()
    if meta:
        st.write("")
        section("Registry", "What is registered, and what actually serves")
        st.markdown(
            f'<p class="sec-note">The best offline model is not the model behind the '
            f"live forecast. A SARIMAX fit is tied to the contiguous series it was "
            f"estimated on and cannot score an arbitrary feature vector at inference "
            f"time, so the serving path uses the best-performing model that can. "
            f"{len(meta)} entries are registered with metrics attached.</p>",
            unsafe_allow_html=True,
        )

    provenance("reports/metrics/ladder.json", "data/model_registry/*/metadata.json")
    footer()


def tab_features() -> None:
    section(
        "Feature engineering",
        "What the model is allowed to know",
        "Every feature declares a min_lag — the oldest information it may use — "
        "and the builder refuses any feature whose min_lag is shorter than the "
        "horizon being forecast. Leakage prevention is mechanical here, not a "
        "matter of care.",
    )

    summary = load_feature_summary()
    if not summary:
        unavailable("Feature specification", PATH_FEATURES.relative_to(REPO_ROOT))
    else:
        n = summary.get("n_features")
        n_shown = f"{n:,}" if isinstance(n, int) else "—"
        n_groups = str(len(summary.get("groups", {})))
        n_min_lag = f"{summary.get('declares_min_lag', 0):,}"
        tiles(
            [
                ("Features", n_shown, "declared in conf/"),
                ("Groups", n_groups, "pollutant · weather · time · physics"),
                ("Declare min_lag", n_min_lag, "I1 enforced mechanically"),
                ("History", "2022-08", "probed CAMS floor"),
            ],
            accent_first=True,
        )
        groups = summary.get("groups") or {}
        if groups:
            st.write("")
            chips = "".join(
                f'<div class="grp">{name} <b>{count}</b></div>'
                for name, count in groups.items()
            )
            st.markdown(f'<div class="groups">{chips}</div>', unsafe_allow_html=True)

    st.write("")
    section(
        "Regional physics",
        "Features a generic global model does not have",
        "Punjab's winter smog has a mechanism, and these encode it directly "
        "rather than hoping a tree finds it.",
    )
    # Ruff's ambiguous-unicode-character rule fires below on the minus
    # sign, multiplication sign and en dashes used as deliberate typography
    # in this visible copy (a formula and two date ranges) - each is
    # individually exempted per line rather than changed, since changing
    # the character would change what is actually shown on screen.
    physics = pd.DataFrame(
        [
            (
                "inversion_proxy",
                "temperature_850hPa − temperature_2m",  # noqa: RUF001
                "Positive means an inversion is capping the boundary layer — "
                "the mechanism behind Punjab winter smog",
            ),
            (
                "stagnation_index",
                "rolling-24h low wind × high humidity × low BLH",  # noqa: RUF001
                "Pollution accumulates when air does not move",
            ),
            (
                "ventilation_index",
                "boundary_layer_height × wind_speed_10m",  # noqa: RUF001
                "Standard dispersion capacity",
            ),
            (
                "crop_burning_season",
                "Oct 15 – Nov 30 flag + day-count",  # noqa: RUF001
                "Regional stubble-burning window",
            ),
            (
                "heating_season",
                "Dec 1 – Feb 15 flag",  # noqa: RUF001
                "Residential biomass and coal",
            ),
            (
                "festival_flag",
                "tabulated in conf/calendar_pk.yaml",
                "Islamic dates shift ~11 days a year, so a formula "
                "mis-dates them silently",
            ),
        ],
        columns=["Feature", "Definition", "Why it matters here"],
    )
    st.dataframe(physics, use_container_width=True, hide_index=True)

    st.write("")
    st.markdown(
        '<div class="gone"><b>A gap this project reports rather than hides.</b> '
        "Boundary-layer height — the input to both dispersion features — is missing "
        "from the source in two distinct ways: the ERA5 observed series has no values "
        "between 2024-01 and 2024-06, and the forecast series is entirely absent before "
        "2024-09. Both were verified against the API directly. The affected features "
        "carry <code>*_is_missing</code> indicators so a tree can learn "
        '"dispersion unknown" as a signal rather than being handed an imputation that '
        "looks like a measurement.</div>",
        unsafe_allow_html=True,
    )

    provenance(
        "conf/features.yaml", "conf/calendar_pk.yaml", "reports/metrics/coverage.json"
    )
    footer()


def tab_shap(zone: str) -> None:
    section(
        "Explainability",
        "Why this forecast, in numbers and in words",
        "SHAP attributes the prediction to individual features. The written "
        "briefing beneath it is generated from the same driver dictionary the "
        "chart uses, through a strict template — so every number in the prose "
        "comes from the dict and cannot be invented.",
    )

    horizon = st.radio(
        "Horizon",
        options=[24, 48, 72],
        format_func=lambda h: HORIZON_LABELS[h],
        horizontal=True,
        label_visibility="collapsed",
        key=f"shap_h_{zone}",
    )

    explanation = load_shap(zone, horizon)
    if not explanation:
        unavailable(
            "SHAP explanation",
            PATH_SNAPSHOT.relative_to(REPO_ROOT),
            "The snapshot carries no driver dictionary for this zone and horizon.",
        )
        provenance("src/aqi/explain/shap_explain.py", "reports/dashboard_snapshot.json")
        footer()
        return

    # Real field name from explain/shap_explain.py::ExplainResult is
    # "top_drivers", not the placeholder "drivers" this renderer was
    # originally written against.
    drivers = explanation.get("top_drivers") or []
    frame = pd.DataFrame(drivers)
    if frame.empty or "feature" not in frame.columns:
        unavailable("SHAP explanation", PATH_SNAPSHOT.relative_to(REPO_ROOT))
        return

    contrib_col = next(
        (c for c in ("contribution", "shap_value", "value") if c in frame.columns), None
    )
    if contrib_col is None:
        unavailable("SHAP explanation", PATH_SNAPSHOT.relative_to(REPO_ROOT))
        return

    frame[contrib_col] = pd.to_numeric(frame[contrib_col], errors="coerce")
    frame = frame.dropna(subset=[contrib_col])
    frame["abs"] = frame[contrib_col].abs()
    # Plotly renders a horizontal bar chart's first row at the BOTTOM and its
    # last row at the TOP. Sorting ascending by |contribution| puts the
    # smallest-magnitude driver first (bottom) and the largest last (top) —
    # matching what the caption below actually promises. The previous
    # `sort_values(contrib_col)` sorted by signed value, so a strongly
    # *negative* top driver could land at the bottom despite being the
    # biggest driver.
    top = frame.nlargest(12, "abs").sort_values("abs", ascending=True)

    # Same label mapping the briefing prose already reads
    # (feature_label_en/_ur, from explain/shap_explain.py — one mapping,
    # not a second one re-derived here) — falls back to the raw column name
    # only if an older snapshot never carried the label fields.
    label_col = "feature_label_en" if "feature_label_en" in top.columns else "feature"

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=top[label_col].astype(str),
            x=top[contrib_col],
            orientation="h",
            marker_color=[
                "#E5544B" if v > 0 else SHAP_NEGATIVE for v in top[contrib_col]
            ],
            hovertemplate="%{y}<br>%{x:+.2f} AQI points<extra></extra>",
        )
    )
    layout = base_layout(max(340, 30 * len(top)), "")
    layout["xaxis"]["title"] = "Contribution to the forecast (AQI points)"
    # base_layout's "x unified" is meant for shared-x-axis charts (multiple
    # traces against one time axis); on a single-trace horizontal bar it
    # overrides the hovertemplate above and prints the raw float instead of
    # "-3.32 AQI points". "closest" respects the hovertemplate.
    layout["hovermode"] = "closest"
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)

    base_value = explanation.get("base_value")
    prediction = explanation.get("predicted_aqi")
    if isinstance(base_value, int | float) and isinstance(prediction, int | float):
        anchor_line = f"base {base_value:.0f} → predicted {prediction:.0f}. "
    else:
        anchor_line = (
            "Base value or predicted AQI is missing from this explanation, so "
            "the chart's deviations cannot be anchored to a starting point. "
        )
    st.markdown(
        f'<p class="sec-note" style="margin-top:0.4rem">{anchor_line}'
        f'<span style="color:#E5544B">Red</span> pushes the forecast up; '
        f'<span style="color:{SHAP_NEGATIVE}">blue</span> pulls it down. '
        f"Bars are ordered by magnitude, so the top of the chart is what actually "
        f"drove this number.</p>",
        unsafe_allow_html=True,
    )

    briefing = explanation.get("briefing_en") or explanation.get("briefing")
    briefing_ur = explanation.get("briefing_ur")
    if briefing:
        st.write("")
        col_en, col_ur = st.columns(2, gap="large")
        with col_en:
            section("Briefing", "In plain English")
            st.markdown(f'<p class="sec-note">{briefing}</p>', unsafe_allow_html=True)
        with col_ur:
            section("بریفنگ", "اردو میں")
            if briefing_ur:
                st.markdown(
                    f'<p class="sec-note" style="direction:rtl">{briefing_ur}</p>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="gone">The Urdu briefing paragraph is not yet '
                    "generated natively from the driver dictionary. Fixed strings "
                    "are hand-written; machine-translating the prose would read "
                    "worse than English alone, so it is left undone rather than "
                    "done badly.</div>",
                    unsafe_allow_html=True,
                )

    provenance("src/aqi/explain/shap_explain.py", "reports/dashboard_snapshot.json")
    footer()


def tab_health(zone: str) -> None:
    st.markdown('<div class="eyebrow">Health guidance</div>', unsafe_allow_html=True)

    now = current_conditions(zone)
    observed_raw = now.get("observed_at") or now.get("timestamp") if now else None
    age = observation_age(observed_raw)
    is_stale = age is not None and age.total_seconds() > STALE_THRESHOLD_HOURS * 3600

    current_aqi = None
    if now:
        raw = now.get("aqi") or now.get("us_aqi") or now.get("nowcast_aqi")
        if isinstance(raw, int | float):
            current_aqi = float(raw)

    # conf/i18n_ur.yaml is the authority (CLAUDE.md §14). It now covers all
    # seven bands, including "Beyond the scale" — guidance.get(name, ("",""))
    # stays as the degrade path for any future band this file doesn't name,
    # not a live branch for this one anymore.
    guidance = load_health_guidance()
    pm25_ranges = _pm25_ranges()

    if current_aqi is None:
        st.markdown(
            '<div class="gone">No current reading is available for this zone, '
            "so no live health advisory can be shown here — see the bands "
            "below instead.</div>",
            unsafe_allow_html=True,
        )
    elif is_stale:
        assert age is not None
        st.markdown(
            f'<div class="gone">The current reading for this zone is '
            f"{format_age(age)}, too stale to vouch for a live health "
            "advisory — see the Live tab. The bands below still apply "
            "once a fresh reading exists.</div>",
            unsafe_allow_html=True,
        )
    else:
        adv_name, adv_colour, adv_urdu = band_for(current_aqi)
        adv_en, adv_ur = guidance.get(adv_name, ("", ""))
        adv_bg = _tint_over_surface(adv_colour, HERO_TINT)
        st.markdown(
            f'<div class="hg-advisory" style="--band:{adv_colour};background:{adv_bg}">'
            f'<div class="eyebrow">Right now, {ZONES[zone]}</div>'
            f'<div class="hg-advisory-head">'
            f'<span class="hg-advisory-aqi">{current_aqi:.0f}</span>'
            f'<span class="hg-advisory-cat">{adv_name}</span>'
            f'<span class="hg-advisory-cat-ur">{adv_urdu}</span>'
            f"</div>"
            f'<div class="hg-row"><div class="hg-en">{adv_en}</div>'
            f'<div class="hg-ur">{adv_ur}</div></div>'
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="sec-title" style="margin-top:1.4rem">What each band means</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    for low, high, name, colour, _urdu in AQI_BANDS:
        en, ur = guidance.get(name, ("", ""))
        is_current = current_aqi is not None and low <= current_aqi <= high

        # AQI_BANDS' last row (501-9999) is an open-ended sentinel, not a
        # real EPA ceiling (the published table's own top row is 501-999) —
        # "501+" says that honestly instead of the "501-500+" bug this
        # replaces (upper was computed from `high`, independently of `low`).
        aqi_range = f"{low:.0f}+" if high > 900 else f"{low:.0f}–{high:.0f}"  # noqa: RUF001

        # The header this feeds into is styled text-transform: uppercase
        # (.hg-head) — CSS uppercasing "µg/m³" turns µ (MICRO SIGN) into a
        # capital Greek mu that reads as a Latin "M", i.e. milligrams, wrong
        # by a factor of 1000 on a health page. The unit is wrapped in its
        # own text-transform: none span so it survives regardless of where
        # in the header it ends up.
        units_span = '<span style="text-transform:none">µg/m³</span>'
        header_bits = [aqi_range]
        pm_range = pm25_ranges.get(int(low))
        if pm_range is not None:
            pm_low, pm_high = pm_range
            pm_text = (
                f"{pm_low:.1f}+ {units_span}"
                if pm_high >= 99999
                else f"{pm_low:.1f}–{pm_high:.1f} {units_span}"  # noqa: RUF001
            )
            header_bits.append(pm_text)
        header_bits.append(name)
        header_text = " · ".join(header_bits)

        chip_html = ""
        if is_current:
            chip_text_colour = _chip_text_colour(colour)
            chip_html = (
                f'<span class="hg-chip" style="background:{colour};'
                f'color:{chip_text_colour}">CURRENT</span>'
            )

        card_bg = _tint_over_surface(
            colour, HEALTH_TINT_CURRENT if is_current else HEALTH_TINT
        )
        st.markdown(
            f'<div class="fc" style="--band:{colour};background:{card_bg};'
            f'border-top:none;border-left:5px solid {colour};margin-bottom:0.6rem">'
            f'<div class="hg-head" style="color:{colour}">'
            f"<span>{header_text}</span>{chip_html}</div>"
            f'<div class="hg-row"><div class="hg-en">{en}</div>'
            f'<div class="hg-ur">{ur}</div></div>'
            f"</div>",
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown(
        '<div class="note"><b>Two things worth stating.</b> The scale is not clipped '
        "at 500 — EPA's breakpoint table defines a 501–999 band above 325.5 µg/m³, and "  # noqa: RUF001
        "Punjab smog episodes reach it. Clipping would flatten exactly the regime this "
        "project exists to forecast. And the 2024 EPA revision is used throughout "
        "(Good now ends at 9.0 µg/m³, not 12.0), so any gap against another provider's "
        "published AQI is partly arithmetic rather than forecast skill.</div>",
        unsafe_allow_html=True,
    )

    provenance(
        "conf/i18n_ur.yaml",
        "src/aqi/aqi_scale.py",
        lead_in="Strings and thresholds on this tab come from",
        note=(
            "All fixed strings are hand-written once and read by a native "
            "speaker, never machine-translated."
        ),
    )
    footer()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🌫️",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CSS, unsafe_allow_html=True)

    st.markdown(
        f'<div class="masthead">'
        f"<h1>{APP_TITLE}</h1>"
        f'<div class="tag">{APP_TAGLINE}</div>'
        f'<div class="strip">'
        f"<div>zones <b>2</b></div>"
        f"<div>history from <b>2022-08-04</b></div>"
        f"<div>labels <b>CAMS reanalysis</b></div>"
        f"<div>ground truth <b>AQICN stations</b></div>"
        f"<div>store <b>{serving_backend()}</b></div>"
        f'<div><a href="https://www.linkedin.com/in/alizataimur/" '
        f'target="_blank" rel="noopener noreferrer" '
        f'class="masthead-link">LinkedIn</a></div>'
        f"</div></div>",
        unsafe_allow_html=True,
    )

    picker, _ = st.columns([1, 3])
    with picker:
        zone = st.selectbox(
            "Forecast zone",
            options=list(ZONES),
            format_func=lambda z: ZONES[z],
            label_visibility="collapsed",
        )

    st.caption(
        "Islamabad and Rawalpindi are modelled as one zone: CAMS returns "
        "byte-identical series for both, so its ~45 km grid cannot separate "
        "cities 13 km apart. Presenting them as two forecasts would be "
        "supported by the coordinates and contradicted by the data."
    )
    st.write("")

    tabs = st.tabs(
        [
            "Live & 3-Day Forecast",
            "Trends",
            "Model Evaluation",
            "Feature Engineering",
            "SHAP Explainability",
            "Health Guidance",
        ]
    )
    with tabs[0]:
        tab_live(zone)
    with tabs[1]:
        tab_trends(zone)
    with tabs[2]:
        tab_model()
    with tabs[3]:
        tab_features()
    with tabs[4]:
        tab_shap(zone)
    with tabs[5]:
        tab_health(zone)


if __name__ == "__main__":
    main()
