"""D5 — the local Model Registry backend (`models/registry.py`).

Hopsworks is a genuine credential gap (`docs/STATE.md`) — this file only
exercises `LocalModelRegistry`, the one this session actually runs against.
"""

from __future__ import annotations

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


def test_current_git_sha_returns_a_nonempty_string() -> None:
    sha = current_git_sha()
    assert isinstance(sha, str)
    assert sha != ""
