"""Deploy-asset tracking guard (session-6 incident; docs/DECISIONS.md ADR-030).

Every file `app/streamlit_app.py` (and its I10 fallback chain) opens at
startup must be tracked by git, or a fresh checkout — like Streamlit
Cloud's — has no data and the FileNotFoundError this test exists to catch
happens in production, not in review.

Runs `git ls-files` directly rather than checking `Path.exists()` on the
working tree: a file can exist locally (this dev machine has run the
training pipeline and the backfill) while being invisible to `git ls-files`
(gitignored, or just never `git add`ed) — exactly the session-6 bug, which
`Path.exists()` would not have caught since the file existed locally the
whole time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVING_MODEL = "lightgbm"
SERVING_HORIZONS = (24, 48, 72)


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.splitlines())


@pytest.fixture(scope="module")
def tracked() -> set[str]:
    return _tracked_files()


class TestModelRegistryArtifactsTracked:
    """`serving/inference.py::load_serving_model` reads exactly these two
    files per horizon for the serving model (ADR-025: LightGBM, not the
    ladder's SARIMAX champion — see that ADR for why)."""

    @pytest.mark.parametrize("horizon", SERVING_HORIZONS)
    def test_metadata_tracked(self, tracked: set[str], horizon: int) -> None:
        path = f"data/model_registry/{SERVING_MODEL}__h{horizon}/metadata.json"
        assert path in tracked, (
            f"{path} is not tracked by git — a fresh checkout (e.g. Streamlit "
            "Cloud) has no model metadata and load_serving_model() will raise "
            "ModelUnavailableError. See docs/DECISIONS.md ADR-030."
        )

    @pytest.mark.parametrize("horizon", SERVING_HORIZONS)
    def test_artifact_tracked(self, tracked: set[str], horizon: int) -> None:
        path = f"data/model_registry/{SERVING_MODEL}__h{horizon}/model.joblib"
        assert path in tracked, (
            f"{path} is not tracked by git — a fresh checkout (e.g. Streamlit "
            "Cloud) has no model artifact and load_serving_model() will raise "
            "ModelUnavailableError. See docs/DECISIONS.md ADR-030."
        )


class TestFeatureStoreSliceTracked:
    """`serving/inference.py::load_frame_cached` needs at least one tracked
    partition per zone, or `load_ladder_frame()` raises "feature store
    returned no rows" on a fresh checkout. Checks for *any* tracked
    partition rather than a specific month, so this doesn't need updating
    every time the committed slice rolls forward."""

    @pytest.mark.parametrize("zone_id", ["capital", "lahore"])
    def test_at_least_one_partition_tracked_per_zone(
        self, tracked: set[str], zone_id: str
    ) -> None:
        prefix = f"data/feature_store/aqi_features/v1/city={zone_id}/"
        matches = [p for p in tracked if p.startswith(prefix)]
        assert matches, (
            f"no tracked feature-store partitions for zone {zone_id!r} under "
            f"{prefix} — a fresh checkout has no data and "
            "load_ladder_frame() will raise ValueError. See "
            "docs/DECISIONS.md ADR-030."
        )


class TestReportsArtifactsTracked:
    """Read directly (not through the API) by the Streamlit app's fallback
    paths: the static snapshot (`--static` mode and the model-unavailable
    fallback) and the ladder for `get_metrics()`'s non-API path."""

    @pytest.mark.parametrize(
        "path",
        ["reports/dashboard_snapshot.json", "reports/metrics/ladder.json"],
    )
    def test_tracked(self, tracked: set[str], path: str) -> None:
        assert path in tracked, f"{path} is not tracked by git"
