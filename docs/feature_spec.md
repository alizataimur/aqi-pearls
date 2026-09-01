# Feature specification (D2 evidence)

Source of truth: `conf/features.yaml`, loaded and expanded by
`src/aqi/features/spec.py`. This file is the human-readable rendering of
that expansion — `tests/test_features.py::TestBuilderSchema` asserts the two
never drift apart (every declared feature is a real builder output column,
and every real column is declared somewhere). 238 features, 6 targets, one
city-hour per row.

Built by `src/aqi/features/builder.py::build_feature_frame`, consuming the
three raw hourly DataFrames session 1's source modules produce (CAMS
pollutants, ERA5 actuals, historical-forecast-as-issued) for one city.

---

## `min_lag_hours` — the mechanical enforcement of I1

Full reasoning: `docs/DECISIONS.md` ADR-011. Short version:

| Value | Meaning | Admitted at horizon `h` |
|---|---|---|
| `null` (historical) | Built only from data timestamped `<= T` (current reading, lag, rolling stat, physics index, calendar flag) | Always |
| an integer (future covariate) | A historical-forecast value describing conditions *at* `T + min_lag_hours`, as issued at `T` | Only when `h == min_lag_hours` exactly |

`src/aqi/features/builder.py::feature_vector(frame, issue_time, horizon_hours)`
is the single chokepoint that applies this rule — it is what
`tests/test_no_leakage.py` attacks directly, alongside the empirical
sentinel-corruption test (the primary I1 guard; this table is the secondary,
metadata-level one).

---

## Categories

| Category | Count | Notes |
|---|---|---|
| Pollutant | 8 | CAMS, current hourly reading. `pm2_5, pm10, o3, no2, so2, co, dust, aerosol_optical_depth` |
| Weather | 15 | ERA5 actuals, `temperature_850hPa` filled from historical-forecast (ADR-009). Wind direction as `sin`/`cos`/`u`/`v`, never raw degrees |
| Time | 9 | Cyclical `sin`/`cos` pairs in **local** (Asia/Karachi) time — hour, day-of-week, day-of-year, month — plus `is_weekend`. Never a raw integer |
| Lag | 70 | 10 base features (6 pollutants + 4 weather) x 7 lags (1, 3, 6, 12, 24, 48, 168h) |
| Rolling | 84 | 7 base features x 3 windows (6, 24, 72h) x 4 stats (mean, max, min, std) |
| Derived | 5 | `hourly_aqi_nowcast` (EPA NowCast, §9.2) + AQI change rate at 1h/3h/24h (D2's explicit requirement) + `pm25_pm10_ratio_roll_24h` (combustion vs. dust signature) |
| Physics | 14 | §10's region-specific table plus 3 `*_is_missing` flags (session 4) — see below |
| Future covariate | 33 | 11 weather variables x 3 horizons (24, 48, 72h), `fc_{variable}_h{h}` |
| **Total** | **238** | |

## Targets — 6, not counted as features

`daily_episode` family (CLAUDE.md §9.1). `aqi_from_24h_mean`'s own docstring
already fixed the definition in Stage 0 — the daily target is the 24-hour-mean
AQI, maxed **across pollutant sub-indices** for that local calendar day
(§9.2), not maxed across hours. `src/aqi/features/targets.py` implements it;
days built from fewer than 18 of 24 hours are `NaN` rather than a biased mean.

| Column | Horizon | Definition |
|---|---|---|
| `target_daily_aqi_h24` / `h48` / `h72` | D+1 / D+2 / D+3 | `overall_aqi` for the local calendar day `h` hours ahead |
| `target_exceeds_200_h24` / `h48` / `h72` | D+1 / D+2 / D+3 | `target_daily_aqi > 200` — the `daily_episode_clf` label |

---

## Physics features (`src/aqi/features/physics.py`, `calendar_pk.py`)

| Feature | Definition | Status |
|---|---|---|
| `inversion_proxy` | `temperature_850hPa - temperature_2m` | `temperature_850hPa` sourced from historical-forecast (ADR-009) |
| `stagnation_index` | rolling-24h `1/(1+wind) * 1/(1+BLH) * (humidity/100)` | Engineering-judgment composite — validate in `notebooks/03_physics_features.ipynb` (session 4) |
| `ventilation_index` | `boundary_layer_height * wind_speed_10m` | Standard dispersion metric |
| `boundary_layer_height_is_missing`, `stagnation_index_is_missing`, `ventilation_index_is_missing` | 1 when the underlying value is `NaN` | Session 4: BLH has a confirmed source gap (2024-01-01 to 2024-06-30, both zones — see `notebooks/03_physics_features.ipynb`). Rolling windows now require a full window (`min_periods=window`, no longer `1`), so a window straddling the gap is genuinely `NaN` rather than a near-empty-window value that looks like a real measurement; these flags carry that fact through whatever later imputation a non-tree model needs |
| `crop_burning_season`, `crop_burning_day_count` | Oct 15 - Nov 30 flag + day count | From `local_date`, not a formula |
| `festival_flag` | Eid al-Fitr, Eid al-Adha, Diwali, New Year | `conf/calendar_pk.yaml` — see its header for the moon-sighting caveat |
| `heating_season` | Dec 1 - Feb 15, spanning New Year | |
| `wind_from_sector_{N,E,S,W}` | One-hot, meteorological "from" convention | |

Every one of these is a candidate for the cut list (CLAUDE.md §1.3) if
session 4's correlation check against PM2.5 spikes shows nothing — that
outcome gets documented as a finding, not silently dropped.

---

## Not yet built

- `pipelines/feature_pipeline.py` and its hourly workflow — session 3
  (`docs/RUNBOOK.md` §2.1; a store has to exist before a pipeline that
  upserts into one is meaningful).
- Backfill to the CAMS floor (2022-08-04) — session 3.
- `evaluation/splits.py` and `evaluation/scaling.py` exist and are tested by
  `tests/test_no_leakage.py`, but are not yet wired into a training loop —
  that's session 5's ladder.
