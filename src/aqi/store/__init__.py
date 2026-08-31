"""Feature store package (CLAUDE.md §11.1, D3).

`get_store()` is the one place pipelines pick a backend, driven by
`FEATURE_STORE_BACKEND` (env, I9) — never a hardcoded backend in a pipeline
module, so switching to Parquet when Hopsworks' free tier is down (I10) is a
one-variable change, not a code change.
"""

from __future__ import annotations

from aqi.config import get_secrets
from aqi.store.base import CITY_COLUMN, TIME_COLUMN, FeatureStore, validate_frame
from aqi.store.parquet_store import ParquetFeatureStore

__all__ = [
    "CITY_COLUMN",
    "TIME_COLUMN",
    "FeatureStore",
    "ParquetFeatureStore",
    "get_store",
    "validate_frame",
]


def get_store(backend: str | None = None) -> FeatureStore:
    chosen = backend or get_secrets().feature_store_backend
    if chosen == "parquet":
        return ParquetFeatureStore()
    if chosen == "hopsworks":
        from aqi.store.hopsworks_store import HopsworksFeatureStore

        return HopsworksFeatureStore()
    raise ValueError(f"unknown FEATURE_STORE_BACKEND: {chosen!r}")
