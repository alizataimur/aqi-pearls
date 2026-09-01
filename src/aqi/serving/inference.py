"""Live inference — the one place both the API (D9/D10) and the Streamlit
app's I10 direct-store fallback get a prediction from.

**Serving model: LightGBM, not SARIMAX**, even though SARIMAX is the ladder's
champion by backtest RMSE (`reports/metrics/ladder.json`). SARIMAX's session-5
registry artifact only supports retrospective one-step scoring against
already-known outcomes (`SarimaxModel.fit_predict_daily` extends state with
*actual* endog values) — it was built to evaluate a backtest, not to forecast
days that haven't happened yet, and reworking it to call
`get_forecast(steps=..., exog=...)` for genuine forward prediction is out of
scope for this session's deadline (`docs/DECISIONS.md`). LightGBM's
`.predict(row)` needs no such rework: give it a feature row, get a number.
Two registered models, different jobs — SARIMAX is the reported metrics
champion, LightGBM is what actually answers "what will Thursday look like."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import joblib
import pandas as pd

from aqi.aqi_scale import category_for
from aqi.config import CityConfig, ZoneConfig, get_zones, load_cities
from aqi.models.dataset import load_ladder_frame
from aqi.models.registry import LocalModelRegistry

HORIZONS_HOURS = (24, 48, 72)
SERVING_MODEL_NAME = "lightgbm"


@dataclass(frozen=True)
class LoadedModel:
    horizon_hours: int
    model: Any
    feature_columns: list[str]
    metrics: dict[str, Any]


def load_serving_model(
    horizon_hours: int, registry: LocalModelRegistry | None = None
) -> LoadedModel:
    registry = registry or LocalModelRegistry()
    metadata = registry.load_metadata(SERVING_MODEL_NAME, horizon_hours)
    artifact_path = metadata["artifact_path"]
    if artifact_path is None:
        raise RuntimeError(
            f"{SERVING_MODEL_NAME} h{horizon_hours} has no artifact in the "
            "registry — run `python -m aqi.pipelines.training_pipeline` first"
        )
    model = joblib.load(artifact_path)
    return LoadedModel(
        horizon_hours=horizon_hours,
        model=model,
        feature_columns=metadata["feature_columns"],
        metrics=metadata["metrics"],
    )


def zones() -> tuple[ZoneConfig, ...]:
    return get_zones()


def cities_by_zone() -> dict[str, list[CityConfig]]:
    out: dict[str, list[CityConfig]] = {}
    for city in load_cities():
        out.setdefault(city.zone, []).append(city)
    return out


def latest_row(frame: pd.DataFrame, zone_id: str) -> pd.Series[Any]:
    zone_frame = frame[frame["city_id"] == zone_id]
    if zone_frame.empty:
        raise ValueError(f"no feature-store rows for zone {zone_id!r}")
    return zone_frame.sort_values("time_utc").iloc[-1]


def build_live_row(
    row: pd.Series[Any], zone_id: str, feature_columns: list[str]
) -> pd.DataFrame:
    """The exact column shape a registered LightGBM model was trained on
    (`models.dataset._with_zone_dummies`): admitted feature values from the
    zone's latest available hour, plus a one-hot zone indicator."""
    values: dict[str, Any] = {}
    for col in feature_columns:
        if col.startswith("zone_"):
            values[col] = 1.0 if col == f"zone_{zone_id}" else 0.0
        else:
            values[col] = row.get(col)
    return pd.DataFrame([values], columns=feature_columns)


@dataclass(frozen=True)
class CurrentReading:
    zone_id: str
    time_utc: pd.Timestamp
    aqi_nowcast: float | None
    category_en: str
    category_ur: str
    pm2_5: float | None
    pm10: float | None


def current_reading(frame: pd.DataFrame, zone_id: str) -> CurrentReading:
    row = latest_row(frame, zone_id)
    nowcast = row.get("hourly_aqi_nowcast")
    aqi_value = None if pd.isna(nowcast) else float(nowcast)
    category_en, category_ur = (
        category_for(round(aqi_value))
        if aqi_value is not None
        else ("Unknown", "نامعلوم")
    )
    return CurrentReading(
        zone_id=zone_id,
        time_utc=row["time_utc"],
        aqi_nowcast=aqi_value,
        category_en=category_en,
        category_ur=category_ur,
        pm2_5=None if pd.isna(row.get("pm2_5")) else float(row["pm2_5"]),
        pm10=None if pd.isna(row.get("pm10")) else float(row["pm10"]),
    )


@dataclass(frozen=True)
class HorizonForecast:
    horizon_hours: int
    target_local_date: str
    predicted_aqi: float
    category_en: str
    category_ur: str


def forecast_zone(
    frame: pd.DataFrame, zone_id: str, timezone: str
) -> list[HorizonForecast]:
    row = latest_row(frame, zone_id)
    local_date = row["local_date"]
    out = []
    for horizon in HORIZONS_HOURS:
        loaded = load_serving_model(horizon)
        live_row = build_live_row(row, zone_id, loaded.feature_columns)
        predicted = float(loaded.model.predict(live_row)[0])
        category_en, category_ur = category_for(round(predicted))
        target_date = local_date + pd.Timedelta(days=horizon // 24)
        out.append(
            HorizonForecast(
                horizon_hours=horizon,
                target_local_date=target_date.isoformat(),
                predicted_aqi=predicted,
                category_en=category_en,
                category_ur=category_ur,
            )
        )
    return out


def load_frame_cached() -> pd.DataFrame:
    """A thin, cache-friendly wrapper — callers (the API, the Streamlit
    fallback) are the ones who decide *how* to cache this; this function
    just does the read."""
    return load_ladder_frame()
