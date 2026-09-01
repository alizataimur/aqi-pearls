"""`app/streamlit_app.py` (D9, D10) — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring. Uses Streamlit's own
`AppTest` harness (no browser, no running server) to run the script
top-to-bottom for every page and assert it never raises."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app" / "streamlit_app.py"
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()
_HAS_LADGER = (REPO_ROOT / "reports" / "metrics" / "ladder.json").exists()
_HAS_SNAPSHOT = (REPO_ROOT / "reports" / "dashboard_snapshot.json").exists()

pytestmark = pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY and _HAS_LADGER),
    reason="needs the real feature store + registered models + ladder.json",
)

PAGES = ["Now", "3-day forecast", "Why", "Model card"]


def test_every_page_runs_without_raising() -> None:
    # No API server is running in the test environment, so every page
    # exercises the I10 direct-store fallback path — the more important
    # path to prove works, since a demo where the API happens to be up
    # would never notice a broken fallback.
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    assert not at.exception

    for page in PAGES:
        at.sidebar.radio[1].set_value(page).run()
        assert not at.exception, f"page {page!r} raised: {at.exception}"


def test_zone_switch_does_not_raise() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("lahore").run()
    assert not at.exception


@pytest.mark.skipif(not _HAS_SNAPSHOT, reason="needs reports/dashboard_snapshot.json")
class TestStaticMode:
    """`--static` reads only the committed snapshot — deliberately gated on
    the snapshot file alone, not the live feature store/registry, since the
    entire point is that this path works when those aren't available."""

    def _load_app_module(self, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
        sys.path.insert(0, str(REPO_ROOT / "app"))
        monkeypatch.delitem(sys.modules, "streamlit_app", raising=False)
        import streamlit_app as app

        app.STATIC_MODE = True
        return app

    def test_get_current_reads_the_snapshot_not_the_live_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        app = self._load_app_module(monkeypatch)
        snapshot = json.loads(
            (REPO_ROOT / "reports" / "dashboard_snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        assert app.get_current("capital") == snapshot["current"]["capital"]

    def test_get_metrics_reads_the_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._load_app_module(monkeypatch)
        metrics = app.get_metrics()
        assert metrics["champion_model"]
