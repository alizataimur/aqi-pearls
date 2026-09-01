"""Deploy-asset tracking guard (session-6 incident; docs/DECISIONS.md ADR-030,
ADR-031, ADR-033).

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

Tracked-ness is necessary but not sufficient — the deploy went dark a
*second* time even after every file above was correctly tracked, for two
different reasons, so this file guards both:

  1. **Coverage, not just presence** (`TestFeatureStoreCoversLookbackWindow`).
     A tracked slice can still be too short: the longest lag the serving
     model reads is 168h (`conf/features.yaml`), so a single tracked month
     leaves any row in that month's first week short of a full lookback
     window. Requires >=2 tracked, calendar-consecutive months per zone.
  2. **Portability, not just existence** (`TestArtifactPathIsPortable`,
     ADR-031). The actual session-6-part-2 bug: `metadata.json`'s
     `artifact_path` was a machine-specific absolute path baked in by
     whichever machine ran the training pipeline, so it resolved to nothing
     on any other checkout even though the *file* itself was correctly
     tracked and present. Reproduced by cloning fresh and reading the
     exception directly (`docs/DECISIONS.md` ADR-031), not by inspecting
     source and guessing.
  3. **Portability of the data, not just the function that produces it**
     (`TestCommittedArtifactPathsAreClean`, ADR-033). Fixing
     `resolve_artifact_path()` (2, above) didn't migrate the 24 already-
     committed `metadata.json` files it reads — they still held the old
     absolute paths, and the fix's own `Path(...).name` call is OS-native,
     so it only actually worked on this Windows dev machine, not on
     Streamlit Cloud's Linux container. `TestArtifactPathIsPortable` never
     caught this because it also runs on this same machine. This class
     reads the committed *data* directly and checks it can never trigger
     this bug again, independent of which OS runs the check.
"""

from __future__ import annotations

import json
import re
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


class TestFeatureStoreCoversLookbackWindow:
    """`serving/inference.py::build_live_row` reads lag/rolling features up
    to 168h (`conf/features.yaml` lags.lag_hours) old, computed relative to
    the *latest* tracked row — not to "today." A committed slice that's
    tracked but too short (session brief's original hypothesis for the
    session-6-part-2 incident — refuted for that specific bug, see ADR-031,
    but a real risk on its own) would leave every lag/rolling feature NaN
    for the early rows of its first tracked month. Requires >=2 tracked,
    calendar-consecutive months per zone — comfortably more than 168h once
    you're more than a few days into the second month, and cheap to check
    without reading actual row timestamps (which `git ls-files` can't give
    you; this deliberately checks the partition *names*, matching what a
    fresh checkout would have, not local disk state)."""

    _PARTITION_RE = re.compile(
        r"^data/feature_store/aqi_features/v1/city=(?P<zone>[^/]+)/"
        r"year=(?P<year>\d+)/month=(?P<month>\d+)/data\.parquet$"
    )

    def _tracked_months(self, tracked: set[str], zone_id: str) -> list[tuple[int, int]]:
        months = []
        for path in tracked:
            match = self._PARTITION_RE.match(path)
            if match and match.group("zone") == zone_id:
                months.append((int(match.group("year")), int(match.group("month"))))
        return sorted(months)

    @pytest.mark.parametrize("zone_id", ["capital", "lahore"])
    def test_at_least_two_consecutive_tracked_months(
        self, tracked: set[str], zone_id: str
    ) -> None:
        months = self._tracked_months(tracked, zone_id)
        assert len(months) >= 2, (
            f"{zone_id}: only {len(months)} tracked feature-store month(s) "
            f"({months}) under data/feature_store/aqi_features/v1/"
            f"city={zone_id}/ — the serving model needs >=168h (7 days) of "
            "trailing lag/rolling context before its latest row; a single "
            "month risks leaving early rows NaN. Commit one more trailing "
            "month. See docs/DECISIONS.md ADR-030/ADR-031."
        )
        (y1, m1), (y2, m2) = months[-2], months[-1]
        expected_next = (y1, m1 + 1) if m1 < 12 else (y1 + 1, 1)
        assert (y2, m2) == expected_next, (
            f"{zone_id}: the two most recent tracked months {months[-2:]} "
            "are not calendar-consecutive — there's a gap, so lookback "
            "context is missing right at the boundary."
        )


class TestArtifactPathIsPortable:
    """Regression test for the actual session-6-part-2 bug (ADR-031):
    `metadata.json`'s `artifact_path` used to be an absolute,
    machine-specific path (e.g. `C:\\Users\\...\\data\\model_registry\\...`),
    which resolved to nothing on any other checkout even though the file
    itself was correctly tracked — `TestModelRegistryArtifactsTracked` above
    passed the whole time. Found by cloning the repo fresh and reading the
    raised exception directly, not by inspecting source and guessing
    (`docs/DECISIONS.md` ADR-031)."""

    @pytest.mark.parametrize("horizon", SERVING_HORIZONS)
    def test_resolved_path_is_anchored_to_this_checkout(self, horizon: int) -> None:
        from aqi.models.registry import LocalModelRegistry

        registry = LocalModelRegistry()
        resolved = registry.resolve_artifact_path(SERVING_MODEL, horizon)
        assert resolved is not None, f"{SERVING_MODEL} h{horizon} has no artifact_path"
        expected_dir = registry.root / f"{SERVING_MODEL}__h{horizon}"
        assert resolved.parent == expected_dir, (
            f"resolve_artifact_path returned {resolved}, not anchored under "
            f"{expected_dir} — a stored absolute artifact_path is leaking "
            "through instead of being re-resolved to this checkout."
        )
        assert resolved.exists(), (
            f"{resolved} does not exist on this checkout — tracked-ness "
            "(TestModelRegistryArtifactsTracked) is not the same as "
            "resolving to a real, loadable file."
        )


class TestCommittedArtifactPathsAreClean:
    """Regression test for the *data*, not the code (ADR-033) — the third
    incident. `TestArtifactPathIsPortable` above tests
    `resolve_artifact_path()`, the function; it passed throughout this bug,
    because it ran on this Windows dev machine, where `Path(...).name`
    happens to parse backslash-separated paths correctly. Streamlit Cloud
    runs Linux, where it does not: `pathlib.PosixPath("C:\\...\\model.
    joblib").name` returns the *entire string unchanged*, since backslash
    isn't a separator to `PosixPath`. `resolve_artifact_path()` was fixed to
    parse with `PureWindowsPath` explicitly (OS-independent), but every
    already-committed `metadata.json` still held the old absolute-path
    strings until this session's migration — fixing the writer does not
    migrate the data it already wrote. This test reads the *committed*
    `artifact_path` values directly and asserts none of them could ever
    trigger this class of bug again, on any OS, regardless of what
    `resolve_artifact_path()` does with them."""

    _DRIVE_LETTER = re.compile(r"^[A-Za-z]:")

    def _tracked_metadata_paths(self, tracked: set[str]) -> list[str]:
        return sorted(
            p
            for p in tracked
            if p.startswith("data/model_registry/") and p.endswith("/metadata.json")
        )

    def test_every_committed_metadata_json_has_a_clean_artifact_path(
        self, tracked: set[str]
    ) -> None:
        metadata_paths = self._tracked_metadata_paths(tracked)
        assert metadata_paths, "no tracked data/model_registry/*/metadata.json found"

        offenders = []
        for rel_path in metadata_paths:
            content = json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
            stored = content.get("artifact_path")
            if stored is None:
                continue
            if (
                self._DRIVE_LETTER.match(stored)
                or "\\" in stored
                or stored.startswith("/")
            ):
                offenders.append((rel_path, stored))

        assert not offenders, (
            "committed metadata.json with a non-portable artifact_path "
            "(drive letter, backslash, or leading slash) — this is exactly "
            "the session-6 third incident (docs/DECISIONS.md ADR-033), "
            "recurring:\n"
            + "\n".join(f"  {path}: {value!r}" for path, value in offenders)
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
