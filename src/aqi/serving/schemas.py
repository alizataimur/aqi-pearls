"""Pydantic response models (D9, D10) — one per endpoint, nothing clever."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class CityInfo(BaseModel):
    id: str
    name_en: str
    name_ur: str
    zone_id: str
    lat: float
    lon: float


class CurrentResponse(BaseModel):
    zone_id: str
    time_utc: datetime
    aqi_nowcast: float | None
    category_en: str
    category_ur: str
    pm2_5: float | None
    pm10: float | None


class ForecastHorizon(BaseModel):
    horizon_hours: int
    target_local_date: str
    predicted_aqi: float
    category_en: str
    category_ur: str


class ForecastResponse(BaseModel):
    zone_id: str
    serving_model: str
    horizons: list[ForecastHorizon]


class DriverContribution(BaseModel):
    feature: str
    feature_label_en: str
    feature_label_ur: str
    value: float | None
    shap_value: float


class ExplainResponse(BaseModel):
    zone_id: str
    horizon_hours: int
    predicted_aqi: float
    base_value: float
    top_drivers: list[DriverContribution]
    briefing_en: str
    briefing_ur: str
    explainer_note: str


class MetricsResponse(BaseModel):
    champion_model: str
    champion_mean_rmse: float
    mean_rmse_by_model: dict[str, float]
    generated_at: str
    split: dict[str, str | int]
    primary_metric_note: str
