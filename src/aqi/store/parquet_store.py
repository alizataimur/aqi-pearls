"""Parquet feature store — the fallback backend (CLAUDE.md §11.1, D3).

Partitioned `city=/year=/month=`, exactly as §11.1 specifies. ADR-014: writes
inside the repo at `data/feature_store/` rather than a Hugging Face Dataset
repo, because no `HF_TOKEN` has been supplied yet — `root` is a constructor
argument so pointing this at a synced HF checkout later is a one-line change,
not a rewrite. Not a nice-to-have: this is what keeps the demo alive when
Hopsworks' free tier is down (I10), and it is required to pass the identical
suite `HopsworksFeatureStore` does (`tests/test_store_parity.py`).

`write()` is an idempotent upsert: the newest write for a given
`(city_id, time_utc)` wins, so re-running an hour never duplicates a row.
`read()`/`read_latest()` operate on the highest version directory that
exists for a group — the Protocol (CLAUDE.md §11.1) has no version
parameter on read, so "latest version written" is the only sensible default.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from aqi.store.base import CITY_COLUMN, TIME_COLUMN, validate_frame

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "feature_store"


class ParquetFeatureStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    # -- layout ----------------------------------------------------------

    def _group_root(self, group: str) -> Path:
        return self.root / group

    def _latest_version(self, group: str) -> int | None:
        group_root = self._group_root(group)
        if not group_root.exists():
            return None
        versions = [
            int(p.name[1:])
            for p in group_root.iterdir()
            if p.is_dir() and p.name.startswith("v") and p.name[1:].isdigit()
        ]
        return max(versions) if versions else None

    def _partition_path(
        self, group: str, version: int, city_id: str, year: int, month: int
    ) -> Path:
        return (
            self._group_root(group)
            / f"v{version}"
            / f"city={city_id}"
            / f"year={year}"
            / f"month={month:02d}"
            / "data.parquet"
        )

    def _city_ids(self, group: str, version: int) -> list[str]:
        version_root = self._group_root(group) / f"v{version}"
        if not version_root.exists():
            return []
        return sorted(
            p.name.split("=", 1)[1]
            for p in version_root.iterdir()
            if p.is_dir() and p.name.startswith("city=")
        )

    @staticmethod
    def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, int]]:
        months = []
        cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_marker = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end_marker:
            months.append((cursor.year, cursor.month))
            cursor = cursor + pd.DateOffset(months=1)
        return months

    @staticmethod
    def _as_utc(ts: datetime | pd.Timestamp) -> pd.Timestamp:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            return stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC")

    # -- writes ------------------------------------------------------------

    def write(self, df: pd.DataFrame, group: str, version: int) -> None:
        validate_frame(df)
        if df.empty:
            return

        frame = df.copy()
        frame[TIME_COLUMN] = pd.to_datetime(frame[TIME_COLUMN], utc=True)
        frame["_year"] = frame[TIME_COLUMN].dt.year
        frame["_month"] = frame[TIME_COLUMN].dt.month

        for (city_id, year, month), chunk in frame.groupby(
            [CITY_COLUMN, "_year", "_month"], sort=False
        ):
            chunk = chunk.drop(columns=["_year", "_month"])
            path = self._partition_path(
                group, version, str(city_id), int(year), int(month)
            )
            path.parent.mkdir(parents=True, exist_ok=True)

            if path.exists():
                existing = pd.read_parquet(path)
                existing[TIME_COLUMN] = pd.to_datetime(existing[TIME_COLUMN], utc=True)
                combined = pd.concat([existing, chunk], ignore_index=True)
            else:
                combined = chunk

            combined = (
                combined.drop_duplicates(subset=[CITY_COLUMN, TIME_COLUMN], keep="last")
                .sort_values(TIME_COLUMN)
                .reset_index(drop=True)
            )
            combined.to_parquet(path, engine="pyarrow", index=False)

    # -- reads ------------------------------------------------------------

    def read(self, group: str, start: datetime, end: datetime) -> pd.DataFrame:
        version = self._latest_version(group)
        if version is None:
            return pd.DataFrame()

        start_ts = self._as_utc(start)
        end_ts = self._as_utc(end)

        frames = []
        for city_id in self._city_ids(group, version):
            for year, month in self._months_between(start_ts, end_ts):
                path = self._partition_path(group, version, city_id, year, month)
                if path.exists():
                    frames.append(pd.read_parquet(path))
        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        combined[TIME_COLUMN] = pd.to_datetime(combined[TIME_COLUMN], utc=True)
        mask = (combined[TIME_COLUMN] >= start_ts) & (combined[TIME_COLUMN] <= end_ts)
        return (
            combined.loc[mask]
            .sort_values([CITY_COLUMN, TIME_COLUMN])
            .reset_index(drop=True)
        )

    def read_latest(self, group: str, n_hours: int) -> pd.DataFrame:
        version = self._latest_version(group)
        if version is None:
            return pd.DataFrame()
        city_ids = self._city_ids(group, version)
        if not city_ids:
            return pd.DataFrame()

        # Only scan each city's most recent partition to find "now" — a full
        # history scan on every hourly read would grow linearly with the
        # backfill forever, and this store must stay hourly-cheap (CLAUDE.md
        # §13's "queued, not guaranteed on the minute" note applies here too).
        latest_ts: pd.Timestamp | None = None
        for city_id in city_ids:
            city_root = self._group_root(group) / f"v{version}" / f"city={city_id}"
            year_dirs = sorted(
                (p for p in city_root.iterdir() if p.is_dir()), reverse=True
            )
            for year_dir in year_dirs[:1]:
                month_dirs = sorted(
                    (p for p in year_dir.iterdir() if p.is_dir()), reverse=True
                )
                for month_dir in month_dirs[:1]:
                    path = month_dir / "data.parquet"
                    if not path.exists():
                        continue
                    col = pd.read_parquet(path, columns=[TIME_COLUMN])[TIME_COLUMN]
                    ts = pd.to_datetime(col, utc=True).max()
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts

        if latest_ts is None:
            return pd.DataFrame()
        # n_hours - 1: read() is inclusive at both ends, and hourly data
        # means "the latest n_hours" is n_hours rows, not n_hours + 1.
        start = latest_ts - pd.Timedelta(hours=n_hours - 1)
        return self.read(group, start.to_pydatetime(), latest_ts.to_pydatetime())
