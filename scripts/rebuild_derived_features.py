"""One-off: recompute lag/rolling/physics columns in the already-populated
feature store after the session-4 min_periods fix (`docs/DECISIONS.md`
ADR-015).

`builder.py`'s rolling windows used to accept `min_periods=1`, so a window
straddling the confirmed `boundary_layer_height` source gap (2024-01-01 to
2024-06-30, both zones) produced a value from whatever few real points fell
in the window — indistinguishable from a genuine full-window statistic. The
fix changes this to `min_periods=window`; this script re-derives every
affected column from the base hourly series **already sitting in the store**
and overwrites them via the store's normal idempotent upsert.

Deliberately does not re-fetch anything from Open-Meteo: the fix only changes
how already-fetched, already-merged hourly columns get windowed, so every
input this script needs (pm2_5, boundary_layer_height, temperature_850hPa,
...) is already correct in the store. Re-running the full network backfill
for a windowing-only fix would cost hours against a live API for zero change
to the inputs. Only `_add_lag_rolling`'s roll_* outputs, `_add_derived`'s
ratio + nowcast + change-rate outputs, and `add_physics_features`'s outputs
are touched; `fc_*` future covariates and `target_*` columns are untouched
because they don't depend on min_periods at all.
"""

from __future__ import annotations

import argparse

import pandas as pd

from aqi.config import get_config, get_zones
from aqi.features.builder import _add_derived, _add_lag_rolling
from aqi.features.calendar_pk import load_festival_dates
from aqi.features.physics import add_physics_features
from aqi.features.spec import load_raw_spec
from aqi.store import TIME_COLUMN, get_store


def _derived_column_names(raw_spec: dict) -> set[str]:
    names: set[str] = set()
    lags = raw_spec["lags"]
    for base in lags["base_features"]:
        for h in lags["lag_hours"]:
            names.add(f"{base}_lag_{h}h")
    rolling = raw_spec["rolling"]
    for base in rolling["base_features"]:
        for window in rolling["windows_hours"]:
            for stat in rolling["stats"]:
                names.add(f"{base}_roll_{stat}_{window}h")
    names.update(
        {
            "hourly_aqi_nowcast",
            "aqi_change_rate_1h",
            "aqi_change_rate_3h",
            "aqi_change_rate_24h",
            "pm25_pm10_ratio_roll_24h",
        }
    )
    names.update(
        {
            "inversion_proxy",
            "stagnation_index",
            "ventilation_index",
            "boundary_layer_height_is_missing",
            "stagnation_index_is_missing",
            "ventilation_index_is_missing",
            "wind_from_sector_N",
            "wind_from_sector_E",
            "wind_from_sector_S",
            "wind_from_sector_W",
            "crop_burning_season",
            "crop_burning_day_count",
            "festival_flag",
            "heating_season",
        }
    )
    return names


def rebuild_zone(zone_id: str, group: str) -> pd.DataFrame:
    store = get_store()
    far_past = pd.Timestamp("2020-01-01", tz="UTC")
    far_future = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    frame = store.read(group, far_past.to_pydatetime(), far_future.to_pydatetime())
    frame = frame[frame["city_id"] == zone_id].copy()
    if frame.empty:
        raise ValueError(f"no rows for zone {zone_id!r} in group {group!r}")

    frame[TIME_COLUMN] = pd.to_datetime(frame[TIME_COLUMN], utc=True)
    frame = frame.sort_values(TIME_COLUMN).set_index(TIME_COLUMN)
    frame.index.name = "time_utc"

    raw_spec = load_raw_spec()
    stale = _derived_column_names(raw_spec) & set(frame.columns)
    base = frame.drop(columns=list(stale))

    rebuilt = _add_lag_rolling(base, raw_spec)
    rebuilt = _add_derived(rebuilt)
    rebuilt = add_physics_features(rebuilt, load_festival_dates())

    return rebuilt.reset_index()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zones", default=None, help="comma-separated zone ids (default: all)")
    args = parser.parse_args(argv)

    config = get_config()
    zone_ids = args.zones.split(",") if args.zones else [z.zone_id for z in get_zones()]
    store = get_store()

    for zone_id in zone_ids:
        rebuilt = rebuild_zone(zone_id, config.store.feature_group)
        store.write(rebuilt, config.store.feature_group, config.store.version)
        print(f"{zone_id}: rewrote {len(rebuilt)} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
