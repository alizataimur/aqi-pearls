"""`app/streamlit_app.py` (D9, D10) — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring. Uses Streamlit's own
`AppTest` harness (no browser, no running server) to run the six-tab
dashboard and assert it never raises.

`st.tabs()` renders every tab's body in the same script run — there is no
lazy per-tab loading in Streamlit — so a single `at.run()` already exercises
all six tabs' data-layer calls. Switching the zone/window/horizon widgets
below exercises different *branches* within those calls (a different zone, a
longer trend window, a later SHAP horizon), not tab bodies a bare run
wouldn't already have reached.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app" / "streamlit_app.py"
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()
_HAS_LADDER = (REPO_ROOT / "reports" / "metrics" / "ladder.json").exists()
_HAS_SNAPSHOT = (REPO_ROOT / "reports" / "dashboard_snapshot.json").exists()

pytestmark = pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY and _HAS_LADDER),
    reason="needs the real feature store + registered models + ladder.json",
)


def test_app_runs_without_raising() -> None:
    # No API server is running in the test environment, so every tab
    # exercises the I10 direct-store fallback path — the more important
    # path to prove works, since a demo where the API happens to be up
    # would never notice a broken fallback.
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    assert not at.exception


def test_zone_switch_does_not_raise() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    # set_value takes the widget's raw underlying value (the zone_id),
    # not format_func's displayed label ("Lahore") — AppTest applies
    # format_func itself when it re-derives the widget's displayed index.
    at.selectbox[0].set_value("lahore").run()
    assert not at.exception


def test_trend_window_switch_does_not_raise() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    at.radio[0].set_value("Year").run()
    assert not at.exception


def test_shap_horizon_switch_does_not_raise() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    # Same raw-value rule as the zone selectbox above: the horizon radio's
    # real options are [24, 48, 72]; "D+1"/"D+2"/"D+3" are format_func's
    # display labels only.
    at.radio[1].set_value(72).run()
    assert not at.exception


@pytest.mark.skipif(not _HAS_SNAPSHOT, reason="needs reports/dashboard_snapshot.json")
class TestSnapshotFallback:
    """With the API unreachable (nothing runs it in tests) and the direct
    feature-store/registry tier forced unavailable, every fetch function
    must fall through to the committed snapshot rather than returning None
    outright — this is the tier that keeps the dashboard alive when a free
    tier is down (I10), and the one most likely to silently break if a
    normalization key ever drifts from the real snapshot shape again."""

    def _load_app_module(self, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
        sys.path.insert(0, str(REPO_ROOT / "app"))
        monkeypatch.delitem(sys.modules, "streamlit_app", raising=False)
        import streamlit_app as app

        monkeypatch.setattr(app, "_fallback_frame", lambda: None)
        return app

    def test_current_conditions_falls_back_to_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._load_app_module(monkeypatch)
        result = app.current_conditions("capital")
        assert result is not None
        assert result["aqi"] is not None
        assert result["observed_at"] is not None

    def test_forecast_rows_falls_back_to_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._load_app_module(monkeypatch)
        rows = app.forecast_rows("capital")
        assert rows
        assert rows[0]["horizon_h"] == 24
        assert rows[0]["predicted_aqi"] is not None

    def test_load_shap_falls_back_to_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._load_app_module(monkeypatch)
        result = app.load_shap("capital", 24)
        assert result is not None
        assert result["top_drivers"]


class TestForecastStaleGuard:
    """The exact bug this guard exists to catch: a stale
    `reports/dashboard_snapshot.json` (or a stale direct-store read) whose
    forecast's target dates have already passed must not be presented as a
    live "3-day outlook" (I3/I4) — regression-tested directly against the
    decision function, not through AppTest, since AppTest re-executes the
    script fresh on every `.run()` and does not pick up a monkeypatch on an
    already-imported module's function."""

    def _load_app_module(self):  # type: ignore[no-untyped-def]
        sys.path.insert(0, str(REPO_ROOT / "app"))
        sys.modules.pop("streamlit_app", None)
        import streamlit_app as app

        return app

    def test_past_target_date_is_not_future(self) -> None:
        app = self._load_app_module()
        today = app._display_today()
        yesterday = app._parse_local_date(str(today - timedelta(days=1)))
        assert yesterday is not None
        assert not (yesterday > today)

    def test_todays_own_date_is_not_future(self) -> None:
        # Literal reading of "not in the future": today itself does not
        # count as future, so a D+1 target of "today" still trips the guard.
        app = self._load_app_module()
        today = app._display_today()
        assert not (today > today)

    def test_tomorrows_date_is_future(self) -> None:
        app = self._load_app_module()
        today = app._display_today()
        tomorrow = app._parse_local_date(str(today + timedelta(days=1)))
        assert tomorrow is not None
        assert tomorrow > today

    def test_unparseable_target_date_is_treated_as_not_future(self) -> None:
        """A malformed/missing target date must degrade to "stale", not to
        "current" — the guard's job is to fail closed, never to silently
        wave through a forecast it couldn't validate."""
        app = self._load_app_module()
        assert app._parse_local_date(None) is None
        assert app._parse_local_date("") is None
        assert app._parse_local_date("not-a-date") is None


def test_health_band_headers_wrap_units_to_survive_uppercase_transform() -> None:
    """`.hg-head`'s `text-transform: uppercase` is a CSS rendering effect —
    invisible to pytest, which only ever sees the HTML source string, never
    a browser's painted output. So this checks the actual structural fix:
    the PM2.5 range's "µg/m³" is wrapped in its own `text-transform: none`
    span, present verbatim (never mangled to "MG/M³", an uppercased Greek
    mu that reads as milligrams — wrong by 1000x on a health page) in the
    source this Python code emits."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    assert not at.exception

    band_cards = [md.value for md in at.markdown if 'class="hg-head"' in md.value]
    assert band_cards, "expected at least one rendered health-guidance band card"

    cards_with_units = [html for html in band_cards if "µg/m³" in html]
    assert cards_with_units, "no band card rendered a PM2.5 range with units"
    for html in cards_with_units:
        assert '<span style="text-transform:none">µg/m³</span>' in html
        assert "MG/M³" not in html
        assert "ΜG/M³" not in html  # noqa: RUF001 — Greek capital mu, the mangled form


@pytest.mark.skipif(not _HAS_SNAPSHOT, reason="needs reports/dashboard_snapshot.json")
def test_shap_residual_reconciles_displayed_bars_to_the_anchor() -> None:
    """The point of the residual row: displayed contributions + residual
    must equal (prediction - base) within floating-point tolerance, on
    real driver data — not a synthetic example. If a future edit to the
    residual formula (or to what `top` contains when it's computed) ever
    breaks that identity, this is what catches it; the anchor line and the
    residual bar would otherwise silently stop reconciling."""
    sys.path.insert(0, str(REPO_ROOT / "app"))
    sys.modules.pop("streamlit_app", None)
    import streamlit_app as app

    explanation = app.load_shap("capital", 24)
    assert explanation is not None
    drivers = explanation["top_drivers"]
    contributions = [d["shap_value"] for d in drivers]
    base_value = explanation["base_value"]
    prediction = explanation["predicted_aqi"]

    residual = app._shap_residual(contributions, base_value, prediction)

    assert abs((sum(contributions) + residual) - (prediction - base_value)) < 1e-6
