"""`scripts/render_static_snapshot.py` — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_static_snapshot.py"
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()
_HAS_LADDER = (REPO_ROOT / "reports" / "metrics" / "ladder.json").exists()

pytestmark = pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY and _HAS_LADDER),
    reason="needs the real feature store + registered models + ladder.json",
)


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("render_static_snapshot", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_snapshot_has_every_top_level_section() -> None:
    module = _load_module()
    snapshot = module.build_snapshot()
    assert set(snapshot.keys()) == {
        "current",
        "forecast",
        "explain",
        "metrics",
        "ledger",
        "health_guidance",
    }
    assert set(snapshot["current"].keys()) == {"capital", "lahore"}
    for zone_forecast in snapshot["forecast"].values():
        assert [h["horizon_hours"] for h in zone_forecast["horizons"]] == [24, 48, 72]
    for zone_explain in snapshot["explain"].values():
        assert set(zone_explain.keys()) == {"24", "48", "72"}
