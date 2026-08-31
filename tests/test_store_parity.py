"""Store parity suite (CLAUDE.md §11.1, D3).

Both backends must satisfy the exact same behaviour: idempotent upsert on
`(city_id, time_utc)`, a bounded `read()`, and `read_latest()` windowing off
the true latest timestamp in the store, not wall-clock "now". The suite is
written once, parametrised over both backends, and run against each — a
backend-specific test file would let the two silently drift apart, which is
exactly what CLAUDE.md §11.1 says not to let happen.

The Hopsworks half is collected but **skipped** unless `HOPSWORKS_API_KEY`
and `HOPSWORKS_PROJECT` are both set (no Hopsworks project exists yet — see
docs/RUNBOOK.md §5 and `src/aqi/store/hopsworks_store.py`'s module
docstring). Skipping is the honest outcome here: mocking the SDK would prove
the code parses, not that Hopsworks accepts it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from aqi.store.base import CITY_COLUMN, TIME_COLUMN
from aqi.store.parquet_store import ParquetFeatureStore

GROUP = "aqi_features_test"


def _make_parquet_store(tmp_path: Path) -> ParquetFeatureStore:
    return ParquetFeatureStore(root=tmp_path / "feature_store")


def _make_hopsworks_store() -> object:
    from aqi.store.hopsworks_store import HopsworksFeatureStore

    return HopsworksFeatureStore()


_HOPSWORKS_CONFIGURED = bool(
    os.environ.get("HOPSWORKS_API_KEY") and os.environ.get("HOPSWORKS_PROJECT")
)


def _sample_frame(
    start: str = "2024-01-31T22:00", n: int = 6, city: str = "capital", offset: int = 0
) -> pd.DataFrame:
    times = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            TIME_COLUMN: times,
            CITY_COLUMN: city,
            "pm2_5": [float(i + offset) for i in range(n)],
        }
    )


@pytest.fixture(params=["parquet", "hopsworks"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[object]:
    if request.param == "hopsworks" and not _HOPSWORKS_CONFIGURED:
        pytest.skip(
            "HOPSWORKS_API_KEY / HOPSWORKS_PROJECT not set — no project exists "
            "yet (docs/RUNBOOK.md §5)"
        )
    if request.param == "parquet":
        yield _make_parquet_store(tmp_path)
    else:
        yield _make_hopsworks_store()


class TestStoreParity:
    def test_write_then_read_round_trips(self, store: object) -> None:
        df = _sample_frame()
        store.write(df, GROUP, 1)  # type: ignore[attr-defined]

        out = store.read(  # type: ignore[attr-defined]
            GROUP,
            df[TIME_COLUMN].min().to_pydatetime(),
            df[TIME_COLUMN].max().to_pydatetime(),
        )
        assert len(out) == len(df)
        assert set(pd.to_datetime(out[TIME_COLUMN], utc=True)) == set(df[TIME_COLUMN])

    def test_write_is_idempotent_upsert(self, store: object) -> None:
        df = _sample_frame()
        store.write(df, GROUP, 1)  # type: ignore[attr-defined]

        # Re-write the same hours with different values — a duplicate hour
        # must never appear, and the newest write must win (CLAUDE.md §11.1).
        updated = df.copy()
        updated["pm2_5"] = updated["pm2_5"] + 1000
        store.write(updated, GROUP, 1)  # type: ignore[attr-defined]

        out = store.read(  # type: ignore[attr-defined]
            GROUP,
            df[TIME_COLUMN].min().to_pydatetime(),
            df[TIME_COLUMN].max().to_pydatetime(),
        )
        assert len(out) == len(df), "re-running a write must not duplicate rows"
        assert out["pm2_5"].min() >= 1000, "the newer write must win"

    def test_read_latest_windows_off_stored_data_not_wall_clock(
        self, store: object
    ) -> None:
        # Deliberately far in the past — read_latest must anchor on the
        # store's own latest timestamp, never on datetime.now().
        df = _sample_frame(start="2022-08-04T00:00", n=48)
        store.write(df, GROUP, 1)  # type: ignore[attr-defined]

        out = store.read_latest(GROUP, 6)  # type: ignore[attr-defined]
        assert len(out) == 6
        assert pd.to_datetime(out[TIME_COLUMN], utc=True).max() == df[TIME_COLUMN].max()

    def test_two_cities_do_not_collide(self, store: object) -> None:
        capital = _sample_frame(city="capital")
        lahore = _sample_frame(city="lahore", offset=500)
        store.write(capital, GROUP, 1)  # type: ignore[attr-defined]
        store.write(lahore, GROUP, 1)  # type: ignore[attr-defined]

        out = store.read(  # type: ignore[attr-defined]
            GROUP,
            capital[TIME_COLUMN].min().to_pydatetime(),
            capital[TIME_COLUMN].max().to_pydatetime(),
        )
        assert set(out[CITY_COLUMN]) == {"capital", "lahore"}
        assert len(out) == len(capital) + len(lahore)
