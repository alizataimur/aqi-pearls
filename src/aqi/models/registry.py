"""Model Registry (D5, CLAUDE.md §11.2) — local backend.

Hopsworks Model Registry is primary per CLAUDE.md §11.2, but
`HOPSWORKS_API_KEY`/`HOPSWORKS_PROJECT` are both empty (the same credential
gap D3's `HopsworksFeatureStore` has — see `docs/STATE.md`), and this
session's brief is explicit: register locally, do not block on Hopsworks.
`LocalModelRegistry` is a real, exercised registry — not a stub — so D5's
Evidence ("registered champion with training window, feature-group version,
git SHA and per-horizon metrics attached") is genuinely satisfiable today.
`HopsworksModelRegistry` is future work once the project exists, following
`store/hopsworks_store.py`'s precedent, not built here (nothing to test it
against yet — CLAUDE.md's prime directive prefers an honest gap over an
unexercised stub).

One entry per `(model_name, horizon_hours)` — that is the actual unit of
"a trained model" in this ladder, since every rung fits a separate estimator
per horizon (different target column, and for the future-covariate features,
a different admitted column set). A model is promoted to **champion** by
flagging every one of its horizon entries and writing a single
`champion.json` pointer, so "the champion" always means "this model's
h24 + h48 + h72 entries, together."
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "model_registry"


@dataclass(frozen=True)
class ModelMetadata:
    model_name: str
    horizon_hours: int
    family: str  # "baseline" | "sklearn" | "statsmodels" | "torch"
    training_window: dict[str, str]
    feature_group_version: int
    git_sha: str
    feature_columns: list[str]
    metrics: dict[str, Any]
    champion: bool
    registered_at: str
    artifact_path: str | None


def current_git_sha(repo_root: Path | None = None) -> str:
    repo_root = repo_root or Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


class LocalModelRegistry:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def _entry_dir(self, model_name: str, horizon_hours: int) -> Path:
        return self.root / f"{model_name}__h{horizon_hours}"

    def register(
        self,
        *,
        model_name: str,
        horizon_hours: int,
        artifact: Any | None,
        family: str,
        training_window: dict[str, str],
        feature_group_version: int,
        git_sha: str,
        feature_columns: list[str],
        metrics: dict[str, Any],
        champion: bool = False,
    ) -> Path:
        entry_dir = self._entry_dir(model_name, horizon_hours)
        entry_dir.mkdir(parents=True, exist_ok=True)

        artifact_path: Path | None = None
        if artifact is not None:
            if family == "torch":
                import torch

                artifact_path = entry_dir / "model.pt"
                torch.save(artifact, artifact_path)
            else:
                import joblib

                artifact_path = entry_dir / "model.joblib"
                joblib.dump(artifact, artifact_path)

        metadata = ModelMetadata(
            model_name=model_name,
            horizon_hours=horizon_hours,
            family=family,
            training_window=training_window,
            feature_group_version=feature_group_version,
            git_sha=git_sha,
            feature_columns=feature_columns,
            metrics=metrics,
            champion=champion,
            registered_at=datetime.now(UTC).isoformat(),
            artifact_path=str(artifact_path) if artifact_path else None,
        )
        (entry_dir / "metadata.json").write_text(
            json.dumps(asdict(metadata), indent=2), encoding="utf-8"
        )
        return entry_dir

    def load_metadata(self, model_name: str, horizon_hours: int) -> dict[str, Any]:
        path = self._entry_dir(model_name, horizon_hours) / "metadata.json"
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def promote_champion(
        self,
        model_name: str,
        horizon_hours: list[int],
        *,
        selection_rule: str,
        selection_value: float,
    ) -> None:
        """Flags every horizon entry of `model_name` as champion and writes
        `champion.json` — CLAUDE.md §11.2's "log every promotion decision,"
        scoped to session 5's single promotion event (no prior champion
        exists yet, so this is a first registration, not a challenger swap)."""
        for h in horizon_hours:
            metadata = self.load_metadata(model_name, h)
            metadata["champion"] = True
            path = self._entry_dir(model_name, h) / "metadata.json"
            path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        pointer = {
            "model_name": model_name,
            "horizons": horizon_hours,
            "selection_rule": selection_rule,
            "selection_value": selection_value,
            "promoted_at": datetime.now(UTC).isoformat(),
        }
        (self.root / "champion.json").write_text(
            json.dumps(pointer, indent=2), encoding="utf-8"
        )
