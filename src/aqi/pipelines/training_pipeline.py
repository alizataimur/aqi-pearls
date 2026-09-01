"""Training pipeline (D5) — Feature Store -> train + evaluate the full ladder
-> Model Registry.

Closes D5, D6, D7 and D12 (session 5, `docs/RUNBOOK.md` §2.1). Reads *only*
from the feature store (`models.dataset.load_ladder_frame`, itself
`get_store()`-backed per I10) — never re-fetches a raw source, which is
exactly what D5's brief asks for. One split (`models.dataset.
smog_season_split`, ADR-016): train = everything before the 2025-26 smog
season minus the 72h purge gap (I2), test = the season itself. Every rung —
three baselines, Ridge, Random Forest, SARIMAX, LightGBM, a small PyTorch
LSTM — is evaluated per horizon (D+1/D+2/D+3) on that one split and written to
`reports/metrics/ladder.json` (I5: generated, never typed). The champion is
whichever model has the lowest **mean RMSE across horizons** — see
docs/DECISIONS.md for why this session uses that instead of CLAUDE.md
§12.3's median-lead-time primary metric (the episode/ledger machinery that
metric needs is differentiator work, cut this session) — and, per I6, that
is reported even if a baseline wins.

CLI: `python -m aqi.pipelines.training_pipeline`
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from aqi.config import REPO_ROOT, get_config
from aqi.evaluation.metrics import episode_precision_recall_f1, regression_metrics
from aqi.models import baselines
from aqi.models.dataset import (
    HorizonMatrix,
    SequenceMatrix,
    build_horizon_matrix,
    build_sequence_matrix,
    daily_aqi_by_date,
    daily_split_bounds,
    load_ladder_frame,
    local_dates,
    local_timezone,
    smog_season_split,
)
from aqi.models.deep import LSTMModel
from aqi.models.forest import RandomForestModel
from aqi.models.gbdt import LightGBMModel
from aqi.models.linear import RidgeModel
from aqi.models.registry import LocalModelRegistry, current_git_sha
from aqi.models.sarimax import EXOG_VARIABLES, SarimaxModel

HORIZONS_HOURS = (24, 48, 72)
LADDER_PATH = REPO_ROOT / "reports" / "metrics" / "ladder.json"


def _score(
    y_true: NDArray[np.floating[Any]],
    y_pred: NDArray[np.floating[Any]],
    *,
    n_train: int,
) -> dict[str, Any]:
    true64: NDArray[np.float64] = np.asarray(y_true, dtype=np.float64)
    pred64: NDArray[np.float64] = np.asarray(y_pred, dtype=np.float64)
    reg = regression_metrics(true64, pred64)
    ep = episode_precision_recall_f1(true64, pred64, threshold=200.0)
    return {
        "regression": reg.to_dict(),
        "episode_at_200": ep.to_dict(),
        "n_train": n_train,
    }


def _run_baselines(
    frame: pd.DataFrame, hm: HorizonMatrix, tz_name: str
) -> dict[str, tuple[NDArray[np.float64], Any, int]]:
    daily = daily_aqi_by_date(frame)
    split = smog_season_split()
    train_end_date, _test_start_date, _test_end_date = daily_split_bounds(split, tz_name)
    train_daily = daily[daily["date"] <= train_end_date]

    persistence = baselines.PersistenceModel(tz_name).fit(daily)
    seasonal_naive = baselines.SeasonalNaiveModel(tz_name).fit(daily)
    climatology = baselines.ClimatologyModel(tz_name).fit(train_daily)

    out: dict[str, tuple[NDArray[np.float64], Any, int]] = {}
    for model in (persistence, seasonal_naive, climatology):
        y_pred = model.predict(hm.test_city_id, hm.test_time_utc, hm.horizon_hours)
        out[model.name] = (y_pred, model, len(train_daily))
    return out


def _run_sarimax(
    frame: pd.DataFrame, hm: HorizonMatrix, tz_name: str
) -> dict[str, tuple[NDArray[np.float64], Any, int]]:
    split = smog_season_split()
    train_end_date, test_start_date, test_end_date = daily_split_bounds(split, tz_name)
    model = SarimaxModel()
    daily_preds = model.fit_predict_daily(
        frame, hm.horizon_hours, train_end_date, test_start_date, test_end_date
    )
    lookup = {
        (row.city_id, row.date): row.yhat for row in daily_preds.itertuples(index=False)
    }
    test_dates = local_dates(hm.test_time_utc, tz_name)
    y_pred = np.array(
        [
            lookup.get((cid, d), np.nan)
            for cid, d in zip(hm.test_city_id, test_dates, strict=True)
        ]
    )
    n_train_days = int(
        frame.loc[frame["local_date"] <= train_end_date, "city_id"]
        .drop_duplicates()
        .shape[0]
    )
    return {"sarimax": (y_pred, model, n_train_days)}


def _run_pooled_sklearn(
    hm: HorizonMatrix,
) -> dict[str, tuple[NDArray[np.float64], Any, int]]:
    ridge = RidgeModel().fit(hm.train_x, hm.train_y)
    forest = RandomForestModel().fit(hm.train_x, hm.train_y)
    lightgbm_model = LightGBMModel().fit(hm.train_x, hm.train_y)
    return {
        "ridge": (ridge.predict(hm.test_x).to_numpy(), ridge, len(hm.train_x)),
        "random_forest": (
            forest.predict(hm.test_x).to_numpy(),
            forest,
            len(hm.train_x),
        ),
        "lightgbm": (
            lightgbm_model.predict(hm.test_x).to_numpy(),
            lightgbm_model,
            len(hm.train_x),
        ),
    }


def _run_lstm(
    seq: SequenceMatrix,
) -> tuple[NDArray[np.float64], NDArray[np.float32], Any, int]:
    model = LSTMModel().fit(seq.train_x, seq.train_zone, seq.train_y)
    y_pred = model.predict(seq.test_x, seq.test_zone)
    return y_pred, seq.test_y, model, len(seq.train_x)


def run_training_pipeline() -> dict[str, Any]:
    cfg = get_config()
    tz_name = local_timezone()
    frame = load_ladder_frame()
    split = smog_season_split()
    git_sha = current_git_sha()
    registry = LocalModelRegistry()

    families = {
        "persistence": "baseline",
        "seasonal_naive": "baseline",
        "climatology": "baseline",
        "sarimax": "statsmodels",
        "ridge": "sklearn",
        "random_forest": "sklearn",
        "lightgbm": "sklearn",
        "lstm": "torch",
    }

    ladder: dict[str, dict[str, Any]] = {name: {} for name in families}

    for horizon in HORIZONS_HOURS:
        print(f"[training_pipeline] horizon={horizon}h: building matrices")
        hm = build_horizon_matrix(frame, horizon)
        seq = build_sequence_matrix(frame, horizon)

        predictions: dict[str, tuple[NDArray[np.float64], Any, int]] = {}
        predictions.update(_run_baselines(frame, hm, tz_name))
        predictions.update(_run_sarimax(frame, hm, tz_name))
        predictions.update(_run_pooled_sklearn(hm))

        lstm_pred, lstm_true, lstm_artifact, lstm_n_train = _run_lstm(seq)

        # What each rung actually read — NOT uniformly hm.feature_columns:
        # the baselines and SARIMAX never see the admitted feature matrix at
        # all (CLAUDE.md I5 — a registry entry claiming inputs a model never
        # read would be exactly the kind of "looks right, isn't" evidence
        # gap the audit sweep (RUNBOOK §4) exists to catch).
        daily_reconstruction_note = [
            "daily_aqi (reconstructed from target_daily_aqi_h24, "
            "models.dataset.daily_aqi_by_date)"
        ]
        feature_columns_by_model: dict[str, list[str]] = {
            "persistence": daily_reconstruction_note,
            "seasonal_naive": daily_reconstruction_note,
            "climatology": [*daily_reconstruction_note, "day_of_year"],
            "sarimax": [f"fc_{v}_h{horizon}" for v in EXOG_VARIABLES]
            + [
                "endog lag terms (AR order 2, daily-aggregated "
                f"target_daily_aqi_h{horizon})"
            ],
            "ridge": hm.feature_columns,
            "random_forest": hm.feature_columns,
            "lightgbm": hm.feature_columns,
        }

        for name, (y_pred, artifact, n_train) in predictions.items():
            print(f"[training_pipeline] horizon={horizon}h model={name}: scoring")
            score = _score(hm.test_y.to_numpy(), y_pred, n_train=n_train)
            ladder[name][f"h{horizon}"] = score
            registry.register(
                model_name=name,
                horizon_hours=horizon,
                artifact=artifact,
                family=families[name],
                training_window={
                    "start": frame["time_utc"].min().isoformat(),
                    "train_end": split.train_end.isoformat(),
                },
                feature_group_version=cfg.store.version,
                git_sha=git_sha,
                feature_columns=feature_columns_by_model[name],
                metrics=score,
            )

        lstm_score = _score(lstm_true, lstm_pred, n_train=lstm_n_train)
        ladder["lstm"][f"h{horizon}"] = lstm_score
        registry.register(
            model_name="lstm",
            horizon_hours=horizon,
            artifact=lstm_artifact,
            family=families["lstm"],
            training_window={
                "start": frame["time_utc"].min().isoformat(),
                "train_end": split.train_end.isoformat(),
            },
            feature_group_version=cfg.store.version,
            git_sha=git_sha,
            feature_columns=list(seq.base_features),
            metrics=lstm_score,
        )

    # Champion selection (session 5's rule — see module docstring / ADR):
    # lowest mean RMSE across the three horizons. Baselines are eligible —
    # CLAUDE.md I6: if one wins, that is the reported result.
    mean_rmse = {
        name: float(
            np.mean([ladder[name][f"h{h}"]["regression"]["rmse"] for h in HORIZONS_HOURS])
        )
        for name in ladder
    }
    champion_name = min(mean_rmse, key=lambda n: mean_rmse[n])
    registry.promote_champion(
        champion_name,
        list(HORIZONS_HOURS),
        selection_rule="lowest mean RMSE across h24/h48/h72 (session 5 substitute "
        "for CLAUDE.md §12.3's median-lead-time primary metric)",
        selection_value=mean_rmse[champion_name],
    )
    print(
        f"[training_pipeline] champion: {champion_name} "
        f"(mean RMSE {mean_rmse[champion_name]:.2f})"
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha,
        "feature_group": cfg.store.feature_group,
        "feature_group_version": cfg.store.version,
        "split": {
            "train_end": split.train_end.isoformat(),
            "test_start": split.test_start.isoformat(),
            "test_end": split.test_end.isoformat(),
            "purge_gap_hours": 72,
            "test_window_description": "2025-26 smog season, local calendar (ADR-016)",
        },
        "horizons_hours": list(HORIZONS_HOURS),
        "primary_metric_note": (
            "Session 5 selects the champion by lowest mean RMSE across horizons, "
            "not CLAUDE.md §12.3's median lead time — that metric needs the "
            "episode/ledger machinery cut this session (docs/DECISIONS.md)."
        ),
        "models": ladder,
        "mean_rmse_across_horizons": mean_rmse,
        "champion": {
            "model_name": champion_name,
            "mean_rmse": mean_rmse[champion_name],
        },
    }
    LADDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LADDER_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[training_pipeline] wrote {LADDER_PATH}")
    return report


def main() -> int:
    run_training_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
