"""D7 — RMSE/MAE/R2 and the AQI>200 precision/recall/F1 table."""

from __future__ import annotations

import numpy as np
import pytest

from aqi.evaluation.metrics import episode_precision_recall_f1, regression_metrics


class TestRegressionMetrics:
    def test_perfect_predictions_are_zero_error_r2_one(self) -> None:
        y = [10.0, 50.0, 200.0, 75.5]
        m = regression_metrics(y, y)
        assert m.rmse == pytest.approx(0.0)
        assert m.mae == pytest.approx(0.0)
        assert m.r2 == pytest.approx(1.0)
        assert m.n == 4

    def test_known_rmse_mae(self) -> None:
        y_true = [0.0, 0.0, 0.0, 0.0]
        y_pred = [1.0, -1.0, 2.0, -2.0]
        m = regression_metrics(y_true, y_pred)
        assert m.mae == pytest.approx(1.5)
        assert m.rmse == pytest.approx(np.sqrt((1 + 1 + 4 + 4) / 4))

    def test_nan_pairs_are_dropped_not_propagated(self) -> None:
        y_true = [10.0, np.nan, 30.0]
        y_pred = [10.0, 99.0, np.nan]
        m = regression_metrics(y_true, y_pred)
        assert m.n == 1
        assert m.rmse == pytest.approx(0.0)

    def test_all_nan_raises(self) -> None:
        with pytest.raises(ValueError):
            regression_metrics([np.nan, np.nan], [np.nan, np.nan])


class TestEpisodeMetrics:
    def test_perfect_classifier_at_threshold(self) -> None:
        y_true = [250.0, 100.0, 300.0, 50.0]
        y_pred = [260.0, 90.0, 310.0, 40.0]
        m = episode_precision_recall_f1(y_true, y_pred, threshold=200.0)
        assert m.precision == pytest.approx(1.0)
        assert m.recall == pytest.approx(1.0)
        assert m.f1 == pytest.approx(1.0)
        assert m.n_true_positive_days == 2
        assert m.n_predicted_positive_days == 2

    def test_missed_episode_hurts_recall_not_precision(self) -> None:
        y_true = [250.0, 250.0]
        y_pred = [260.0, 100.0]  # one hazardous day predicted safe
        m = episode_precision_recall_f1(y_true, y_pred, threshold=200.0)
        assert m.precision == pytest.approx(1.0)
        assert m.recall == pytest.approx(0.5)

    def test_false_alarm_hurts_precision_not_recall(self) -> None:
        y_true = [250.0, 100.0]
        y_pred = [260.0, 210.0]  # one false alarm
        m = episode_precision_recall_f1(y_true, y_pred, threshold=200.0)
        assert m.recall == pytest.approx(1.0)
        assert m.precision == pytest.approx(0.5)

    def test_no_predicted_positives_gives_zero_precision_not_a_crash(self) -> None:
        y_true = [250.0, 100.0]
        y_pred = [100.0, 90.0]
        m = episode_precision_recall_f1(y_true, y_pred, threshold=200.0)
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.f1 == 0.0
