"""FastAPI service (D9, D10) — loads the model (registry) and features
(feature store) and serves predictions. Nothing clever: six endpoints, one
in-process cache for the feature frame (it's a few seconds to build and the
feature store is hourly, not sub-second — CLAUDE.md §13's "queued, not
guaranteed on the minute" applies here too).

Run: `uvicorn aqi.serving.api:app --reload`
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aqi.config import REPO_ROOT, load_cities
from aqi.explain.shap_explain import explain_zone
from aqi.serving.inference import (
    HORIZONS_HOURS,
    SERVING_MODEL_NAME,
    current_reading,
    forecast_zone,
    load_frame_cached,
    zones,
)
from aqi.serving.schemas import (
    CityInfo,
    CurrentResponse,
    DriverContribution,
    ExplainResponse,
    ForecastHorizon,
    ForecastResponse,
    HealthResponse,
    MetricsResponse,
)

LADDER_PATH = REPO_ROOT / "reports" / "metrics" / "ladder.json"
FRAME_CACHE_SECONDS = 600  # feature store is hourly; no point reading more often

app = FastAPI(title="Pearls AQI Predictor", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cached_dataframe: pd.DataFrame | None = None
_cached_at: float = 0.0


def _cached_frame() -> pd.DataFrame:
    global _cached_dataframe, _cached_at
    now = time.time()
    if _cached_dataframe is None or now - _cached_at > FRAME_CACHE_SECONDS:
        _cached_dataframe = load_frame_cached()
        _cached_at = now
    return _cached_dataframe


def _known_zone_ids() -> set[str]:
    return {zone.zone_id for zone in zones()}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/cities", response_model=list[CityInfo])
def cities() -> list[CityInfo]:
    return [
        CityInfo(
            id=city.id,
            name_en=city.name_en,
            name_ur=city.name_ur,
            zone_id=city.zone,
            lat=city.lat,
            lon=city.lon,
        )
        for city in load_cities()
    ]


def _validate_zone(zone_id: str) -> None:
    if zone_id not in _known_zone_ids():
        raise HTTPException(status_code=404, detail=f"unknown zone_id {zone_id!r}")


@app.get("/current", response_model=CurrentResponse)
def current(zone_id: str = "capital") -> CurrentResponse:
    _validate_zone(zone_id)
    reading = current_reading(_cached_frame(), zone_id)
    return CurrentResponse(
        zone_id=reading.zone_id,
        time_utc=reading.time_utc.to_pydatetime(),
        aqi_nowcast=reading.aqi_nowcast,
        category_en=reading.category_en,
        category_ur=reading.category_ur,
        pm2_5=reading.pm2_5,
        pm10=reading.pm10,
    )


@app.get("/forecast", response_model=ForecastResponse)
def forecast(zone_id: str = "capital") -> ForecastResponse:
    _validate_zone(zone_id)
    zone = next(z for z in zones() if z.zone_id == zone_id)
    horizons = forecast_zone(_cached_frame(), zone_id, zone.timezone)
    return ForecastResponse(
        zone_id=zone_id,
        serving_model=SERVING_MODEL_NAME,
        horizons=[
            ForecastHorizon(
                horizon_hours=h.horizon_hours,
                target_local_date=h.target_local_date,
                predicted_aqi=h.predicted_aqi,
                category_en=h.category_en,
                category_ur=h.category_ur,
            )
            for h in horizons
        ],
    )


@app.get("/explain", response_model=ExplainResponse)
def explain(zone_id: str = "capital", horizon_hours: int = 24) -> ExplainResponse:
    _validate_zone(zone_id)
    if horizon_hours not in HORIZONS_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"horizon_hours must be one of {HORIZONS_HOURS}",
        )
    result = explain_zone(_cached_frame(), zone_id, horizon_hours)
    return ExplainResponse(
        zone_id=result.zone_id,
        horizon_hours=result.horizon_hours,
        predicted_aqi=result.predicted_aqi,
        base_value=result.base_value,
        top_drivers=[
            DriverContribution(
                feature=d.feature,
                feature_label_en=d.label_en,
                feature_label_ur=d.label_ur,
                value=d.value,
                shap_value=d.shap_value,
            )
            for d in result.top_drivers
        ],
        briefing_en=result.briefing_en,
        briefing_ur=result.briefing_ur,
        explainer_note=result.explainer_note,
    )


@app.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    if not LADDER_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="reports/metrics/ladder.json missing — run the training pipeline",
        )
    ladder = json.loads(Path(LADDER_PATH).read_text(encoding="utf-8"))
    return MetricsResponse(
        champion_model=ladder["champion"]["model_name"],
        champion_mean_rmse=ladder["champion"]["mean_rmse"],
        mean_rmse_by_model=ladder["mean_rmse_across_horizons"],
        generated_at=ladder["generated_at"],
        split=ladder["split"],
        primary_metric_note=ladder["primary_metric_note"],
    )
