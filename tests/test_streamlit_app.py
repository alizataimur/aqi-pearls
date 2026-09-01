"""`app/streamlit_app.py` (D9, D10) — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring. Uses Streamlit's own
`AppTest` harness (no browser, no running server) to run the script
top-to-bottom for every page and assert it never raises."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app" / "streamlit_app.py"
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()
_HAS_LADGER = (REPO_ROOT / "reports" / "metrics" / "ladder.json").exists()

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
