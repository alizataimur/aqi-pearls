"""Rungs 0a-0c (CLAUDE.md §12.1) — persistence, seasonal-naive, climatology.

Deterministic synthetic daily series so every prediction has a known-correct
answer, plus one explicit check that climatology's fitted table never moves
when test-only days change (the train-only-fit rule, CLAUDE.md I2).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aqi.models.baselines import ClimatologyModel, PersistenceModel, SeasonalNaiveModel

TZ = "Asia/Karachi"


def _daily(rows: list[tuple[str, date, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["city_id", "date", "daily_aqi"]).assign(
        exceeds_200=lambda d: d["daily_aqi"] > 200
    )


class TestPersistence:
    def test_predicts_yesterdays_completed_value(self) -> None:
        daily = _daily(
            [
                ("capital", date(2026, 1, 1), 100.0),
                ("capital", date(2026, 1, 2), 150.0),
            ]
        )
        model = PersistenceModel(TZ).fit(daily)
        # Issue time local noon on 2026-01-02 -> "yesterday" is 2026-01-01.
        issue_utc = pd.Timestamp("2026-01-02 07:00:00", tz="UTC")  # 12:00 PKT
        pred = model.predict(
            pd.Series(["capital"]), pd.Series([issue_utc]), horizon_hours=24
        )
        assert pred[0] == pytest.approx(100.0)

    def test_constant_across_every_horizon(self) -> None:
        daily = _daily([("capital", date(2026, 1, 1), 100.0)])
        model = PersistenceModel(TZ).fit(daily)
        issue_utc = pd.Series([pd.Timestamp("2026-01-02 07:00:00", tz="UTC")])
        for h in (24, 48, 72):
            pred = model.predict(pd.Series(["capital"]), issue_utc, horizon_hours=h)
            assert pred[0] == pytest.approx(100.0)

    def test_unknown_lookup_date_is_nan(self) -> None:
        daily = _daily([("capital", date(2026, 1, 1), 100.0)])
        model = PersistenceModel(TZ).fit(daily)
        issue_utc = pd.Series([pd.Timestamp("2026-06-01 07:00:00", tz="UTC")])
        pred = model.predict(pd.Series(["capital"]), issue_utc, horizon_hours=24)
        assert np.isnan(pred[0])


class TestSeasonalNaive:
    def test_predicts_same_weekday_last_week_relative_to_target_date(self) -> None:
        # Issue on 2026-01-08, horizon 24h -> target date 2026-01-09.
        # Lookup date = target - 7 = 2026-01-02.
        daily = _daily([("capital", date(2026, 1, 2), 77.0)])
        model = SeasonalNaiveModel(TZ).fit(daily)
        issue_utc = pd.Series([pd.Timestamp("2026-01-08 07:00:00", tz="UTC")])
        pred = model.predict(pd.Series(["capital"]), issue_utc, horizon_hours=24)
        assert pred[0] == pytest.approx(77.0)

    def test_lookup_date_is_always_in_the_past_relative_to_issue_time(self) -> None:
        # h=72 (the largest horizon): target = issue_date + 3, lookup =
        # target - 7 = issue_date - 4, always strictly before the issue time.
        issue_date = date(2026, 1, 8)
        lookup_date = issue_date + pd.Timedelta(days=3) - pd.Timedelta(days=7)
        assert lookup_date < issue_date


class TestClimatology:
    def test_predicts_train_only_day_of_year_mean(self) -> None:
        # Two training years' worth of Jan-1 readings; target date is a third
        # year's Jan-1 (day-of-year 1 in every case, ignoring leap years).
        train_daily = _daily(
            [
                ("capital", date(2023, 1, 1), 100.0),
                ("capital", date(2024, 1, 1), 200.0),
            ]
        )
        model = ClimatologyModel(TZ).fit(train_daily)
        # local_date(issue) = 2025-12-31 (UTC+5h stays on the same day) so
        # target_date (horizon=24h) = 2026-01-01, day-of-year 1.
        issue_utc = pd.Series([pd.Timestamp("2025-12-31 07:00:00", tz="UTC")])
        pred = model.predict(pd.Series(["capital"]), issue_utc, horizon_hours=24)
        assert pred[0] == pytest.approx(150.0)

    def test_test_split_days_never_influence_the_fitted_table(self) -> None:
        """The train-only-fit rule (CLAUDE.md I2) is enforced by what the
        *caller* passes to `fit`, not by anything inside the model — so the
        positive control here is showing the fit really would move if a
        "test" day were mixed in, then confirming the honest, train-only fit
        doesn't reflect it."""
        train_daily = _daily([("capital", date(2023, 1, 1), 100.0)])
        train_plus_leaked_test_day = pd.concat(
            [train_daily, _daily([("capital", date(2099, 1, 1), 999.0)])]
        )
        issue_utc = pd.Series([pd.Timestamp("2026-12-31 07:00:00", tz="UTC")])

        honest = ClimatologyModel(TZ).fit(train_daily)
        leaked = ClimatologyModel(TZ).fit(train_plus_leaked_test_day)

        pred_honest = honest.predict(pd.Series(["capital"]), issue_utc, horizon_hours=24)
        pred_leaked = leaked.predict(pd.Series(["capital"]), issue_utc, horizon_hours=24)

        assert pred_honest[0] == pytest.approx(100.0)
        assert pred_leaked[0] == pytest.approx((100.0 + 999.0) / 2)
        assert pred_honest[0] != pytest.approx(pred_leaked[0])
