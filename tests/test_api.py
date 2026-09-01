"""`serving/api.py` (D9, D10) — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()
_HAS_LADDER = (REPO_ROOT / "reports" / "metrics" / "ladder.json").exists()

pytestmark = pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY and _HAS_LADDER),
    reason="needs the real feature store + registered models + ladder.json",
)


@pytest.fixture(scope="module")
def client():  # type: ignore[no-untyped-def]
    from aqi.serving.api import app

    return TestClient(app)


def test_health(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cities_lists_all_three(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/cities")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert {"islamabad", "rawalpindi", "lahore"} <= ids


def test_current_known_zone(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/current", params={"zone_id": "capital"})
    assert r.status_code == 200
    body = r.json()
    assert body["zone_id"] == "capital"
    assert body["category_en"]


def test_current_unknown_zone_is_404(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/current", params={"zone_id": "karachi"})
    assert r.status_code == 404


def test_forecast_has_three_horizons(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/forecast", params={"zone_id": "lahore"})
    assert r.status_code == 200
    body = r.json()
    assert body["serving_model"] == "lightgbm"
    assert [h["horizon_hours"] for h in body["horizons"]] == [24, 48, 72]


def test_explain_returns_drivers_and_both_languages(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/explain", params={"zone_id": "capital", "horizon_hours": 24})
    assert r.status_code == 200
    body = r.json()
    assert body["top_drivers"]
    assert body["briefing_en"]
    assert body["briefing_ur"]
    assert "SARIMAX" in body["explainer_note"]


def test_explain_rejects_bad_horizon(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/explain", params={"zone_id": "capital", "horizon_hours": 6})
    assert r.status_code == 400


def test_metrics_names_the_champion(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["champion_model"]
    assert body["champion_mean_rmse"] > 0
