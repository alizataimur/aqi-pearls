"""SHAP feature attribution (D13) — from the registered **LightGBM** model,
never SARIMAX.

SARIMAX is the ladder's champion by backtest RMSE (`reports/metrics/
ladder.json`), but it is a state-space model over a daily-aggregated
exogenous series, not a tree ensemble — `shap.TreeExplainer` cannot explain
it, and a `KernelExplainer` (the model-agnostic fallback) was explicitly
ruled out for this session (slow, unstable on a state-space model with an
autoregressive structure, and not worth the time against today's deadline).
LightGBM is already registered from session 5 and is exactly the kind of
model SHAP's fast, exact tree explainer was built for. `ExplainResult.
explainer_note` carries this distinction into the API/UI so nobody mistakes
"explained" for "the metrics champion, explained" — CLAUDE.md I5's spirit
applied to attribution, not just numbers.

The briefing sentence is a **strict template**, never an LLM call (CLAUDE.md
§14, D13-adjacent): every number in it comes from the driver dict computed
here, so there is nothing for a model to hallucinate. Urdu is generated
**natively** from the same dict via `conf/i18n_ur.yaml`'s hand-written
strings, not translated from the English sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
import shap

from aqi.aqi_scale import category_for
from aqi.config import get_zones
from aqi.explain.i18n import feature_label
from aqi.serving.inference import build_live_row, latest_row, load_serving_model

TOP_N_DRIVERS = 5
EXPLAINER_NOTE = (
    "Feature attribution from the gradient-boosted model (LightGBM), not "
    "SARIMAX. SARIMAX is the ladder's champion by backtest RMSE "
    "(reports/metrics/ladder.json) but is a state-space model, not a tree "
    "ensemble, and shap.TreeExplainer cannot explain it (docs/DECISIONS.md)."
)


@dataclass(frozen=True)
class DriverContribution:
    feature: str
    label_en: str
    label_ur: str
    value: float | None
    shap_value: float


@dataclass(frozen=True)
class ExplainResult:
    zone_id: str
    horizon_hours: int
    predicted_aqi: float
    base_value: float
    top_drivers: list[DriverContribution]
    briefing_en: str
    briefing_ur: str
    explainer_note: str = EXPLAINER_NOTE


def _label_for_column(column: str) -> tuple[str, str]:
    for base_name in _KNOWN_BASE_NAMES:
        if re.search(rf"(^|_){re.escape(base_name)}($|_)", column):
            label_en, label_ur = feature_label(base_name)
            suffix = column.replace(base_name, "", 1).strip("_").replace("_", " ")
            if suffix:
                return f"{label_en} ({suffix})", f"{label_ur} ({suffix})"
            return label_en, label_ur
    pretty = column.replace("_", " ")
    return pretty, pretty


# Longest-first so e.g. "boundary_layer_height" matches before a shorter
# accidental substring would.
_KNOWN_BASE_NAMES = sorted(
    [
        "pm2_5",
        "pm10",
        "wind_speed_10m",
        "boundary_layer_height",
        "temperature_2m",
        "relative_humidity_2m",
        "inversion_proxy",
        "stagnation_index",
        "ventilation_index",
        "hourly_aqi_nowcast",
        "crop_burning_season",
        "heating_season",
    ],
    key=len,
    reverse=True,
)


def _zone_names(zone_id: str) -> tuple[str, str]:
    for zone in get_zones():
        if zone.zone_id == zone_id:
            return zone.representative_city.name_en, zone.representative_city.name_ur
    return zone_id, zone_id


def _briefing(
    zone_id: str,
    horizon_hours: int,
    predicted: float,
    category_en: str,
    category_ur: str,
    drivers: list[DriverContribution],
) -> tuple[str, str]:
    city_en, city_ur = _zone_names(zone_id)
    horizon_days = horizon_hours // 24
    top_two = drivers[:2]
    driver_en = " and ".join(d.label_en for d in top_two) or "no single dominant factor"
    driver_ur = " اور ".join(d.label_ur for d in top_two) or "کوئی ایک غالب عنصر نہیں"

    briefing_en = (
        f"LightGBM predicts {city_en}'s daily max AQI at {predicted:.0f} "
        f"({category_en}) in {horizon_days} day(s), driven mainly by {driver_en}."
    )
    briefing_ur = (
        f"لائٹ جی بی ایم کے مطابق {city_ur} کا یومیہ زیادہ سے زیادہ AQI "
        f"{horizon_days} دن میں {predicted:.0f} ({category_ur}) متوقع ہے، "
        f"جس کی بڑی وجہ {driver_ur} ہے۔"  # noqa: RUF001 (Arabic full stop, correct Urdu)
    )
    return briefing_en, briefing_ur


def explain_zone(
    frame: pd.DataFrame, zone_id: str, horizon_hours: int, top_n: int = TOP_N_DRIVERS
) -> ExplainResult:
    loaded = load_serving_model(horizon_hours)
    row = latest_row(frame, zone_id)
    live_row = build_live_row(row, zone_id, loaded.feature_columns)

    explainer = shap.TreeExplainer(loaded.model.sklearn_model)
    explanation = explainer(live_row)
    values = np.asarray(explanation.values[0], dtype=float)
    base_value = float(np.asarray(explanation.base_values).reshape(-1)[0])
    predicted = base_value + float(values.sum())

    order = np.argsort(-np.abs(values))[:top_n]
    drivers = []
    for idx in order:
        column = loaded.feature_columns[idx]
        label_en, label_ur = _label_for_column(column)
        raw_value = live_row[column].iloc[0]
        drivers.append(
            DriverContribution(
                feature=column,
                label_en=label_en,
                label_ur=label_ur,
                value=None if pd.isna(raw_value) else float(raw_value),
                shap_value=float(values[idx]),
            )
        )

    category_en, category_ur = category_for(round(predicted))
    briefing_en, briefing_ur = _briefing(
        zone_id, horizon_hours, predicted, category_en, category_ur, drivers
    )

    return ExplainResult(
        zone_id=zone_id,
        horizon_hours=horizon_hours,
        predicted_aqi=predicted,
        base_value=base_value,
        top_drivers=drivers,
        briefing_en=briefing_en,
        briefing_ur=briefing_ur,
    )
