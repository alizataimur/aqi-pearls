"""Single source of configuration truth (CLAUDE.md §16).

Loads `conf/config.yaml` and `conf/cities.yaml` once into typed Pydantic
models; secrets and backend selection come from the environment via
`pydantic-settings`. This module requires PyYAML — that is fine for every
pipeline, store and model module written from session 3 onward.
`scripts/clock_starter.py` deliberately does **not** import this: Day 0 stays
stdlib-only so a dependency problem can never block a capture (CLAUDE.md §6),
and keeps its own tiny YAML fallback (ADR-012) rather than depending on this
module existing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = REPO_ROOT / "conf" / "config.yaml"
CITIES_YAML = REPO_ROOT / "conf" / "cities.yaml"
ENV_FILE = REPO_ROOT / ".env"


class CityConfig(BaseModel):
    id: str
    name_en: str
    name_ur: str
    lat: float
    lon: float
    timezone: str
    aqicn_station: str | None = None
    cams_grid: tuple[float, float]
    zone: str


class ZoneConfig(BaseModel):
    """One modelled forecast zone (ADR-013) — `capital` or `lahore`.

    `representative_city` supplies the coordinates fetched from CAMS/ERA5 and
    stored in the feature store; `member_city_ids` is every named city that
    maps onto this zone (`capital` covers Islamabad and Rawalpindi — ADR-008
    found their CAMS series byte-identical). Serving/dashboard code fans a
    zone's forecast back out to every member city; nothing upstream of that
    should ever treat them as independently modelled.
    """

    zone_id: str
    representative_city: CityConfig
    member_city_ids: tuple[str, ...]

    @property
    def lat(self) -> float:
        return self.representative_city.lat

    @property
    def lon(self) -> float:
        return self.representative_city.lon

    @property
    def timezone(self) -> str:
        return self.representative_city.timezone


def load_cities(path: Path = CITIES_YAML) -> list[CityConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [CityConfig(**c) for c in raw["cities"]]


def load_zones(path: Path = CITIES_YAML) -> list[ZoneConfig]:
    """ADR-013: one zone per distinct `zone` value in `conf/cities.yaml`.

    The representative city is whichever member has a pinned AQICN station
    (falls back to the first listed member if none do yet, e.g. a brand-new
    zone before `diagnose_aqicn.py` has found it a station).
    """
    cities = load_cities(path)
    members: dict[str, list[CityConfig]] = {}
    for city in cities:
        members.setdefault(city.zone, []).append(city)

    zones = []
    for zone_id, city_group in members.items():
        representative = next((c for c in city_group if c.aqicn_station), city_group[0])
        zones.append(
            ZoneConfig(
                zone_id=zone_id,
                representative_city=representative,
                member_city_ids=tuple(c.id for c in city_group),
            )
        )
    return zones


class ProjectConfig(BaseModel):
    name: str
    display_timezone: str
    storage_timezone: str


class OpenMeteoConfig(BaseModel):
    air_quality: str
    weather_archive: str
    historical_forecast: str
    live_forecast: str
    domain: str


class SourcesConfig(BaseModel):
    open_meteo: OpenMeteoConfig
    aqicn: dict[str, str]
    backfill_start: str


class StoreConfig(BaseModel):
    backend: Literal["parquet", "hopsworks"]
    feature_group: str
    version: int


class RetryConfig(BaseModel):
    attempts: int
    base_delay_seconds: float
    max_delay_seconds: float


class AppConfig(BaseModel):
    """The sections of `conf/config.yaml` session 3's code actually reads.

    `targets`/`evaluation`/`conformal`/`alerts` exist in the YAML for later
    sessions; Pydantic's default `extra="ignore"` lets this model stay
    partial rather than forcing every section to be modelled before D3/D4
    can ship. Add the matching `BaseModel` here when a session first reads
    one of those sections — never reach for raw dict access instead.
    """

    project: ProjectConfig
    sources: SourcesConfig
    store: StoreConfig
    retry: RetryConfig


def load_config(path: Path = CONFIG_YAML) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AppConfig(**raw)


class Secrets(BaseSettings):
    """Env-var-only settings (CLAUDE.md I9) — never anything from YAML here.

    Reads `.env` locally; in CI these come from GitHub Secrets and there is
    no `.env` file, which `SettingsConfigDict(env_file=...)` tolerates (a
    missing env file is not an error).
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    aqicn_token: str = ""
    feature_store_backend: Literal["parquet", "hopsworks"] = "parquet"
    hopsworks_api_key: str = ""
    hopsworks_project: str = ""
    hf_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    llm_api_key: str = ""
    llm_provider: str = "groq"

    # D14 alerts (CLAUDE.md §14). Email is the default channel — Telegram is
    # blocked in Pakistan by the PTA, the market this product is for
    # (docs/DECISIONS.md ADR-032). Telegram stays supported, e.g. for the
    # maintainer's own monitoring from outside Pakistan.
    alert_channel: Literal["email", "telegram"] = "email"
    alert_email_host: str = ""
    alert_email_port: int = 587
    alert_email_user: str = ""
    alert_email_password: str = ""
    alert_email_to: str = ""


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()


@lru_cache(maxsize=1)
def get_zones() -> tuple[ZoneConfig, ...]:
    return tuple(load_zones())
