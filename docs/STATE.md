# STATE — current build position

> Read CLAUDE.md first for rules and contracts. This file is *position only*.
> Update it at the end of every session (CLAUDE.md §19).

**Stage:** 2 — Make the data real, in progress / Session 2 (features + leakage test) — CLOSED
**Updated:** 2026-08-31
**Repo status:** public, pushed — https://github.com/alizataimur/aqi-pearls

---

## Stage 0 gate — CLOSED (see session 1 log for detail)

## Session 1 — Open-Meteo sources — CLOSED

Three source modules, schema capture, `conf/config.yaml: backfill_start`
filled from the probe. D1 stays 🟡 — `pipelines/feature_pipeline.py` and its
workflow are session 3's job (a store has to exist first; corrected from an
earlier, wrong "session 2" note — see ADR-010).

---

## Session 2 — Feature + target builder, leakage test — CLOSED (D2 ✅)

| Item | Status | Notes |
|---|---|---|
| `conf/features.yaml` | done | 235 features declared, generator-style (base x lag/window/horizon), not enumerated by hand |
| `src/aqi/features/spec.py` | done | expands the YAML; `FeatureSpec.admitted_at(h)` is ADR-011's rule |
| `conf/calendar_pk.yaml` + `calendar_pk.py` | done | Eid al-Fitr/Adha, Diwali, New Year 2022-2027 — moon-sighting caveat in the file header |
| `src/aqi/features/physics.py` | done, **not yet validated** | all 7 §10 physics features built + unit-tested; correlation against PM2.5 spikes is `notebooks/03_physics_features.ipynb`, session 4 |
| `src/aqi/features/targets.py` | done | `daily_episode` family; 24h-mean AQI maxed across pollutants, per local calendar day; <18/24h days → `NaN`, not a biased mean |
| `src/aqi/features/builder.py` | done | `build_feature_frame` (235 features + 6 targets) and `feature_vector` (the horizon-admission chokepoint) |
| `src/aqi/evaluation/splits.py` | done | walk-forward, purge gap ≥72h enforced (raises below it) — session 5 reuses this, not its own copy |
| `src/aqi/evaluation/scaling.py` | done | dependency-free fit-on-train-only scaler; session 5 may swap in sklearn but must keep the rule |
| `tests/test_features.py` | done | 17 tests — physics, calendar, targets, and a schema test pinning `features.yaml` to the builder's real output |
| `tests/test_no_leakage.py` | done | 8 tests — see below |
| `.github/workflows/ci.yml` | done | dedicated `Leakage test (I1)` step, no longer just folded into `pytest -q` |
| `docs/feature_spec.md` | done | D2's evidence artifact |
| `docs/DELIVERABLES.md` | done | D2 row ✅ |

**D2 evidence, runnable by anyone:**
`pytest tests/test_features.py tests/test_no_leakage.py` → 25 passed.

### The leakage test, specifically (`tests/test_no_leakage.py`)

Four independent checks, per `docs/RUNBOOK.md` §2.1:

1. **Empirical sentinel-corruption** — build a real feature vector for
   `(T, h=24)`, overwrite every CAMS/ERA5 reading strictly after `T` with a
   sentinel (`hist_forecast` deliberately untouched — it's *supposed* to
   describe times after `T`), rebuild, assert every historical feature is
   unchanged. A positive control (corrupt at-or-before `T` instead) proves
   the test isn't vacuous — it does change things.
2. **`min_lag_hours` admission** (ADR-011) — a future covariate built for one
   horizon is never admitted into another horizon's vector; historical
   features are admitted at every horizon.
3. **Purge gap** — `walk_forward_splits` raises below 72h; every real split
   respects it.
4. **Scaler train-only** — `fit_scaler` on a train split with a wildly
   different test split never reflects the test statistics.

All four passed on the first real run against the actual builder — not
retrofitted to make a broken builder look clean.

---

## Decisions made this session

- **ADR-009 resolved.** `temperature_850hPa` sourced from
  `historical_forecast` unconditionally (past and future) — ERA5 archive
  confirmed to never serve pressure-level output at all at these coordinates
  (not a latency issue). Legal under I1: a forecast-as-issued value for a
  past hour `t` was, by construction, issued at or before `t`.
- **ADR-011 (new).** Pinned `min_lag_hours` semantics precisely — "hours of
  forecast lead time baked in," not "data age" — because the literal
  CLAUDE.md wording breaks on the obvious case (current-reading features).
  Full reasoning in `docs/DECISIONS.md`.
- **ADR-010 corrected.** Its "session 2 closes D1" line was wrong against
  `docs/RUNBOOK.md` §2.1 (written after that ADR), which assigns
  `feature_pipeline.py` to session 3. Corrected in place, not silently.

---

## The single next action

**Session 3** (`docs/RUNBOOK.md` §2.1): feature store (Hopsworks + Parquet
behind one `Protocol`, identical test suite both backends), resumable
chunked backfill to 2022-08-04, `pipelines/feature_pipeline.py` wired to
today's builder, and its hourly workflow — this is what finally closes D1.

Still needs Aliza: the rubric-confirmation email (CLAUDE.md §2) — still no
evidence in this repo's history that it's been sent. Every session keeps
flagging it; it keeps not being the blocker that stops the session, but it
should stop being deferred.

---

## Known gaps carried forward

- `pipelines/feature_pipeline.py`, hourly feature workflow, feature store,
  backfill — all session 3.
- `stagnation_index` is an engineering-judgment composite (rolling-24h
  `1/(1+wind) * 1/(1+BLH) * humidity/100`), not a published index — session
  4's correlation check against PM2.5 spikes is where it earns or loses its
  place, same as every other physics feature.
- Festival dates in `conf/calendar_pk.yaml` are moon-sighting approximations
  (±1 day) — fine for a flag feature, called out explicitly in the file for
  anyone tempted to treat them as exact.
- Rawalpindi still has no pinned AQICN station (`conf/cities.yaml`) —
  unchanged from session 1.
- The instructor rubric email — still not sent, as far as this repo shows.

---

## Log

**2026-08-27 — Stage 0 scaffolded, then closed.** Repo skeleton, config,
`aqi_scale.py` (44 tests, EPA 2024 breakpoints), clock starter, probe script,
two workflows. Repo pushed public, AQICN token supplied, first
`clock-starter` run green. Probe found the Delhi station-substitution bug
(ADR-007) and the Islamabad/Rawalpindi grid-cell finding (ADR-008).

**2026-08-27 — Session 1: Open-Meteo sources.** `open_meteo_air.py`,
`open_meteo_weather.py`, `open_meteo_hist_forecast.py`, shared `_http.py`;
live schema capture; 13 offline tests. Found ADR-009 (ERA5 archive missing
`temperature_850hPa`), left open for session 2.

**2026-08-31 — Session 2: features, targets, leakage test. D2 closed.**
235 declared features + 6 targets, built by `src/aqi/features/builder.py`
from session 1's raw DataFrames. Resolved ADR-009 (source `temperature_850hPa`
from `historical_forecast` unconditionally). Pinned `min_lag_hours` semantics
as ADR-011 after the literal CLAUDE.md wording turned out to forbid using
today's PM2.5 to predict tomorrow — correctness bug caught before it shipped.
Built `evaluation/splits.py` (walk-forward, ≥72h purge) and
`evaluation/scaling.py` (train-only fit) a session early, specifically so
`tests/test_no_leakage.py` could exercise the real thing rather than a stub.
The empirical sentinel-corruption test passed on the first real run.
`ruff check`, `ruff format --check`, `mypy --strict`, `pytest` all green
(93 tests). Corrected a wrong line in ADR-010 (D1 belongs to session 3, not
session 2) in place rather than leaving it silently stale.
