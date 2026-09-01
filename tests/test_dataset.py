"""Session-5 data-prep for the ladder (`models/dataset.py`) — no network, no
real feature store; everything here runs on tiny synthetic frames shaped
like the feature store's real output columns.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aqi.evaluation.splits import contains_full_smog_season
from aqi.models.dataset import (
    build_horizon_matrix,
    daily_aqi_by_date,
    daily_split_bounds,
    smog_season_split,
)


class TestSmogSeasonSplit:
    def test_test_window_contains_the_full_smog_season(self) -> None:
        split = smog_season_split()
        assert contains_full_smog_season(split.test_start, split.test_end)

    def test_train_end_is_exactly_one_purge_gap_before_test_start(self) -> None:
        split = smog_season_split()
        assert split.test_start - split.train_end == pd.Timedelta(hours=72)

    def test_rejects_a_purge_gap_below_the_minimum(self) -> None:
        with pytest.raises(ValueError):
            smog_season_split(purge_gap_hours=24)

    def test_daily_split_bounds_are_local_calendar_dates(self) -> None:
        split = smog_season_split()
        train_end, test_start, test_end = daily_split_bounds(split, "Asia/Karachi")
        assert test_start == date(2025, 10, 1)
        assert test_end == date(2026, 2, 28)
        assert train_end < test_start


class TestDailyAqiByDate:
    def _synthetic_frame(self) -> pd.DataFrame:
        # Two local days for one zone: local_date=2025-12-01 rows all carry
        # the same target_daily_aqi_h24 (163) — the constant-per-calendar-day
        # property `builder._add_targets` guarantees for real data.
        rows = []
        for _hour in range(24):
            rows.append(
                {
                    "city_id": "capital",
                    "local_date": date(2025, 12, 1),
                    "target_daily_aqi_h24": 163.0,
                    "target_exceeds_200_h24": False,
                }
            )
        for _hour in range(24):
            rows.append(
                {
                    "city_id": "capital",
                    "local_date": date(2025, 12, 2),
                    "target_daily_aqi_h24": 210.0,
                    "target_exceeds_200_h24": True,
                }
            )
        return pd.DataFrame(rows)

    def test_reconstructs_next_days_daily_aqi(self) -> None:
        frame = self._synthetic_frame()
        daily = daily_aqi_by_date(frame)
        # target_daily_aqi_h24 issued on 2025-12-01 IS the daily_aqi of
        # 2025-12-02 (ADR: h24 = "tomorrow's daily max, as observed").
        row = daily[daily["date"] == date(2025, 12, 2)].iloc[0]
        assert row["daily_aqi"] == pytest.approx(163.0)
        assert bool(row["exceeds_200"]) is False

        row2 = daily[daily["date"] == date(2025, 12, 3)].iloc[0]
        assert row2["daily_aqi"] == pytest.approx(210.0)
        assert bool(row2["exceeds_200"]) is True

    def test_nan_target_rows_are_dropped(self) -> None:
        frame = self._synthetic_frame()
        frame.loc[frame["local_date"] == date(2025, 12, 2), "target_daily_aqi_h24"] = (
            np.nan
        )
        daily = daily_aqi_by_date(frame)
        assert date(2025, 12, 3) not in set(daily["date"])


class TestBuildHorizonMatrix:
    def test_raises_on_empty_frame(self) -> None:
        empty = pd.DataFrame(
            {
                "city_id": pd.Series(dtype="object"),
                "time_utc": pd.Series(dtype="datetime64[ns, UTC]"),
                "local_date": pd.Series(dtype="object"),
                "target_daily_aqi_h24": pd.Series(dtype="float64"),
            }
        )
        with pytest.raises((ValueError, KeyError)):
            build_horizon_matrix(empty, 24)
