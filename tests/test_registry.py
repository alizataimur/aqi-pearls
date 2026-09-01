"""D5 — the local Model Registry backend (`models/registry.py`).

Hopsworks is a genuine credential gap (`docs/STATE.md`) — this file only
exercises `LocalModelRegistry`, the one this session actually runs against.
"""

from __future__ import annotations

import json
from pathlib import Path

from aqi.models.registry import LocalModelRegistry, current_git_sha


class TestLocalModelRegistry:
    def test_register_writes_metadata_and_joblib_artifact(self, tmp_path: Path) -> None:
        registry = LocalModelRegistry(root=tmp_path)
        entry_dir = registry.register(
            model_name="ridge",
            horizon_hours=24,
            artifact={"coef": [1.0, 2.0]},
            family="sklearn",
            training_window={"start": "2022-08-04T00:00:00+00:00"},
            feature_group_version=1,
            git_sha="deadbeef",
            feature_columns=["pm2_5", "pm10"],
            metrics={"regression": {"rmse": 12.3}},
        )
        assert (entry_dir / "metadata.json").exists()
        assert (entry_dir / "model.joblib").exists()

        metadata = registry.load_metadata("ridge", 24)
        assert metadata["model_name"] == "ridge"
        assert metadata["horizon_hours"] == 24
        assert metadata["git_sha"] == "deadbeef"
        assert metadata["metrics"]["regression"]["rmse"] == 12.3
        assert metadata["champion"] is False

    def test_register_with_no_artifact_writes_metadata_only(self, tmp_path: Path) -> None:
        registry = LocalModelRegistry(root=tmp_path)
        entry_dir = registry.register(
            model_name="sarimax",
            horizon_hours=24,
            artifact=None,
            family="statsmodels",
            training_window={"start": "2022-08-04T00:00:00+00:00"},
            feature_group_version=1,
            git_sha="deadbeef",
            feature_columns=[],
            metrics={"regression": {"rmse": 19.4}},
        )
        assert (entry_dir / "metadata.json").exists()
        assert not (entry_dir / "model.joblib").exists()
        metadata = registry.load_metadata("sarimax", 24)
        assert metadata["artifact_path"] is None

    def test_promote_champion_flags_every_horizon_and_writes_pointer(
        self, tmp_path: Path
    ) -> None:
        registry = LocalModelRegistry(root=tmp_path)
        for h in (24, 48, 72):
            registry.register(
                model_name="lightgbm",
                horizon_hours=h,
                artifact=None,
                family="sklearn",
                training_window={},
                feature_group_version=1,
                git_sha="deadbeef",
                feature_columns=[],
                metrics={"regression": {"rmse": 20.0}},
            )
        registry.promote_champion(
            "lightgbm",
            [24, 48, 72],
            selection_rule="lowest mean RMSE",
            selection_value=20.0,
        )
        for h in (24, 48, 72):
            assert registry.load_metadata("lightgbm", h)["champion"] is True
        assert (tmp_path / "champion.json").exists()

    def test_a_model_never_flagged_champion_stays_false(self, tmp_path: Path) -> None:
        registry = LocalModelRegistry(root=tmp_path)
        registry.register(
            model_name="persistence",
            horizon_hours=24,
            artifact=None,
            family="baseline",
            training_window={},
            feature_group_version=1,
            git_sha="deadbeef",
            feature_columns=[],
            metrics={"regression": {"rmse": 27.6}},
        )
        registry.register(
            model_name="lightgbm",
            horizon_hours=24,
            artifact=None,
            family="sklearn",
            training_window={},
            feature_group_version=1,
            git_sha="deadbeef",
            feature_columns=[],
            metrics={"regression": {"rmse": 20.0}},
        )
        registry.promote_champion(
            "lightgbm", [24], selection_rule="lowest mean RMSE", selection_value=20.0
        )
        assert registry.load_metadata("persistence", 24)["champion"] is False


class TestResolveArtifactPath:
    """ADR-033 (session-6 third incident): `resolve_artifact_path` must
    parse a stored `artifact_path` the same way regardless of which OS
    actually runs it. The bug: `Path(stored).name` is OS-native — it
    correctly strips a Windows absolute path down to a filename only on
    Windows, and returns the *whole string unchanged* on Linux (Streamlit
    Cloud), because `pathlib.PosixPath` never treats backslash as a
    separator. `PureWindowsPath(stored).name` is used instead, precisely
    because it is *not* OS-native — it always parses backslash/drive-letter
    syntax, on every platform, so this test's assertions hold no matter
    what OS pytest happens to be running on right now."""

    def test_new_filename_only_format_round_trips(self, tmp_path: Path) -> None:
        registry = LocalModelRegistry(root=tmp_path)
        registry.register(
            model_name="lightgbm",
            horizon_hours=24,
            artifact={"anything": True},
            family="sklearn",
            training_window={},
            feature_group_version=1,
            git_sha="deadbeef",
            feature_columns=[],
            metrics={},
        )
        resolved = registry.resolve_artifact_path("lightgbm", 24)
        assert resolved == tmp_path / "lightgbm__h24" / "model.joblib"

    def test_legacy_windows_absolute_path_resolves_to_the_filename_only(
        self, tmp_path: Path
    ) -> None:
        # Hand-write a metadata.json exactly like the pre-migration
        # committed files had (session-6, ADR-031/ADR-033) — an absolute
        # Windows path baked in by whichever machine ran the training
        # pipeline — and confirm resolve_artifact_path still recovers just
        # the filename, joined onto *this* registry's own entry_dir.
        entry_dir = tmp_path / "lightgbm__h24"
        entry_dir.mkdir(parents=True)
        (entry_dir / "model.joblib").write_bytes(b"not a real model")
        (entry_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "artifact_path": (
                        r"C:\Users\xesha\Documents\aqi-pearls\data\model_registry"
                        r"\lightgbm__h24\model.joblib"
                    )
                }
            ),
            encoding="utf-8",
        )
        registry = LocalModelRegistry(root=tmp_path)
        resolved = registry.resolve_artifact_path("lightgbm", 24)
        assert resolved == entry_dir / "model.joblib"
        assert resolved.exists()

    def test_legacy_windows_path_with_forward_slash_root_also_resolves(
        self, tmp_path: Path
    ) -> None:
        # A belt-and-braces case: some tooling normalizes Windows paths to
        # forward slashes (`C:/Users/.../model.joblib`). PureWindowsPath
        # accepts both separators, so this must resolve identically.
        entry_dir = tmp_path / "lightgbm__h24"
        entry_dir.mkdir(parents=True)
        (entry_dir / "model.joblib").write_bytes(b"not a real model")
        (entry_dir / "metadata.json").write_text(
            json.dumps({"artifact_path": "C:/Users/xesha/anywhere/model.joblib"}),
            encoding="utf-8",
        )
        registry = LocalModelRegistry(root=tmp_path)
        resolved = registry.resolve_artifact_path("lightgbm", 24)
        assert resolved == entry_dir / "model.joblib"

    def test_no_artifact_path_resolves_to_none(self, tmp_path: Path) -> None:
        entry_dir = tmp_path / "sarimax__h24"
        entry_dir.mkdir(parents=True)
        (entry_dir / "metadata.json").write_text(
            json.dumps({"artifact_path": None}), encoding="utf-8"
        )
        registry = LocalModelRegistry(root=tmp_path)
        assert registry.resolve_artifact_path("sarimax", 24) is None


def test_current_git_sha_returns_a_nonempty_string() -> None:
    sha = current_git_sha()
    assert isinstance(sha, str)
    assert sha != ""
