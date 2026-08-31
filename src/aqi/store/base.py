"""Feature store contract (CLAUDE.md §11.1, D3).

Both backends key rows on `(city_id, time_utc)` with idempotent upsert
semantics — re-running an hour must never duplicate rows (CLAUDE.md §11.1).
`city_id` here is a zone_id (ADR-013: `capital` or `lahore`), not one of the
three names in `conf/cities.yaml`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd

TIME_COLUMN = "time_utc"
CITY_COLUMN = "city_id"


class FeatureStore(Protocol):
    def write(self, df: pd.DataFrame, group: str, version: int) -> None: ...

    def read(self, group: str, start: datetime, end: datetime) -> pd.DataFrame: ...

    def read_latest(self, group: str, n_hours: int) -> pd.DataFrame: ...


def validate_frame(df: pd.DataFrame) -> None:
    """Shared precondition both backends check before writing.

    Catches a malformed frame at the store boundary rather than downstream in
    a Parquet partition path or a Hopsworks schema-mismatch error.
    """
    if TIME_COLUMN not in df.columns:
        raise ValueError(f"feature frame is missing the {TIME_COLUMN!r} column")
    if CITY_COLUMN not in df.columns:
        raise ValueError(f"feature frame is missing the {CITY_COLUMN!r} column")
    if df.empty:
        return
    times = pd.to_datetime(df[TIME_COLUMN])
    if times.dt.tz is None:
        raise ValueError(f"{TIME_COLUMN} must be timezone-aware UTC (CLAUDE.md I7)")
