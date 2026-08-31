"""Hopsworks Feature Store backend — the primary backend (CLAUDE.md §11.1, D3).

Feature groups are keyed on `city_id` with `event_time=time_utc`, so
point-in-time joins are correct, per CLAUDE.md §11.1. `city_id` is a zone_id
(ADR-013: `capital` or `lahore`) — identical to `ParquetFeatureStore`, which
is what lets both pass the same suite in `tests/test_store_parity.py`.

Needs `HOPSWORKS_API_KEY` and `HOPSWORKS_PROJECT` (env only, CLAUDE.md I9).
As of session 3 no Hopsworks project has been created yet — RUNBOOK §5
assigns that to Aliza — so this module is written against the documented
4.x SDK surface (`hopsworks.login`, `project.get_feature_store()`,
`get_or_create_feature_group`, `.insert()`, `.filter()`) but has never run
against a live project. `tests/test_store_parity.py` skips this backend's
half of the suite when the two env vars are absent, rather than mocking the
SDK and calling that equivalent — a mock proves the code parses, not that
Hopsworks accepts it. `hopsworks` is an optional dependency (`pyproject.toml`
`data` extra) and is imported lazily so nothing else in the repo breaks when
it isn't installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from aqi.config import get_secrets
from aqi.store.base import CITY_COLUMN, TIME_COLUMN, validate_frame


class HopsworksConfigError(RuntimeError):
    """Raised when HOPSWORKS_API_KEY / HOPSWORKS_PROJECT are not set."""


class HopsworksFeatureStore:
    def __init__(self, api_key: str | None = None, project: str | None = None) -> None:
        secrets = get_secrets()
        self._api_key = api_key or secrets.hopsworks_api_key
        self._project_name = project or secrets.hopsworks_project
        if not self._api_key or not self._project_name:
            raise HopsworksConfigError(
                "HOPSWORKS_API_KEY and HOPSWORKS_PROJECT must both be set — "
                "create the project first (docs/RUNBOOK.md §5)."
            )
        self._fs: Any = None

    def _feature_store(self) -> Any:
        if self._fs is None:
            import hopsworks

            project = hopsworks.login(
                api_key_value=self._api_key, project=self._project_name
            )
            self._fs = project.get_feature_store()
        return self._fs

    def _feature_group(
        self, group: str, version: int, *, df_for_schema: pd.DataFrame | None = None
    ) -> Any:
        fs = self._feature_store()
        if df_for_schema is not None:
            return fs.get_or_create_feature_group(
                name=group,
                version=version,
                primary_key=[CITY_COLUMN],
                event_time=TIME_COLUMN,
                online_enabled=False,
                description=(
                    "Pearls AQI predictor — hourly feature+target rows, "
                    "one per (forecast zone, hour). See ADR-013."
                ),
            )
        return fs.get_feature_group(name=group, version=version)

    def _latest_version(self, group: str) -> int:
        fs = self._feature_store()
        groups = fs.get_feature_groups(group)
        if not groups:
            raise FileNotFoundError(f"no feature group named {group!r} in this project")
        return int(max(fg.version for fg in groups))

    # -- writes ------------------------------------------------------------

    def write(self, df: pd.DataFrame, group: str, version: int) -> None:
        validate_frame(df)
        if df.empty:
            return
        fg = self._feature_group(group, version, df_for_schema=df)
        # Hopsworks upserts offline storage on the declared primary key +
        # event_time, so re-running an hour never duplicates a row
        # (CLAUDE.md §11.1) — matches ParquetFeatureStore's guarantee.
        fg.insert(df, write_options={"wait_for_job": True})

    # -- reads ------------------------------------------------------------

    def read(self, group: str, start: datetime, end: datetime) -> pd.DataFrame:
        fg = self._feature_group(group, self._latest_version(group))
        query = fg.select_all().filter(
            (fg.get_feature(TIME_COLUMN) >= start) & (fg.get_feature(TIME_COLUMN) <= end)
        )
        result: pd.DataFrame = query.read()
        return result

    def read_latest(self, group: str, n_hours: int) -> pd.DataFrame:
        fg = self._feature_group(group, self._latest_version(group))
        df: pd.DataFrame = fg.read()
        if df.empty:
            return df
        times = pd.to_datetime(df[TIME_COLUMN], utc=True)
        latest = times.max()
        # n_hours - 1: inclusive of `latest` itself, matching
        # ParquetFeatureStore — "the latest n_hours" is n_hours rows.
        start = latest - pd.Timedelta(hours=n_hours - 1)
        return df.loc[times >= start].reset_index(drop=True)
