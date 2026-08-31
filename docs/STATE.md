# STATE — current build position

> Read CLAUDE.md first for rules and contracts. This file is *position only*.
> Update it at the end of every session (CLAUDE.md §19).

**Stage:** 2 — Make the data real, in progress / Session 3 (store + backfill) — CLOSED
**Updated:** 2026-08-31
**Repo status:** public, pushed — https://github.com/alizataimur/aqi-pearls

---

## 🟢 clock-starter recovered — ADR-012, confirmed before session 3 started

The root cause was found and fixed (see ADR-012 below) before this session began:
CI's stdlib YAML fallback parser didn't strip quotes the way PyYAML does, so
`aqicn_station: "@11739"` parsed with the quote characters still attached and
every capture 404'd. Fixed in `fda36a5`, with `tests/test_yaml_fallback.py`
now asserting the two parsers agree on the whole structure, not just `lat`.

**Confirmed live**, via the public Checks/Actions API (no admin rights needed):
run #18, `workflow_dispatch`, triggered by the fix commit `fda36a5`, **succeeded**
at 2026-08-31T14:44:11Z — the first green run since #1. Runs #2–#17 (2026-08-27
20:59 → 2026-08-31 13:35, all on stale pre-fix trees — most on `aa315de`, the
last, #17, on `5d50928`) are the permanent, unrecoverable ~90-hour ledger gap
this ADR describes; nothing after the fix has failed. Commit `b8e0a21`
("surface failure diagnostics via GITHUB_STEP_SUMMARY") **never actually got a
scheduled run** — checked `commits/b8e0a21/check-runs` directly: only the
unrelated `ci` workflow ran on that SHA. It was superseded by the real fix
(`fda36a5`) before its first hourly tick landed, so its diagnostic step was
never exercised by a live failure — the root cause was instead found by
reading the code (the quote-stripping bug), not by waiting on that summary.

**Re-checked at 2026-08-31T15:51Z** (mid-session-3, in response to a direct
ask to verify this): still only 18 runs total, nothing scheduled since #17
(13:35Z). The next hourly tick (~15:07Z) hadn't appeared in the Actions API by
15:51Z — 44 minutes past due, longer than the "10–30 min is normal" queuing
CLAUDE.md §13 describes. Not treated as a new failure (no failed run exists to
diagnose), but worth confirming next session whether that tick eventually
landed green, or never fired at all.

Per CLAUDE.md's anti-pattern list, this was checked *before* any session-3 code
was written, not skipped past.

---

## Session 3 — Store, backfill, feature pipeline — CLOSED (D3 🟡, D4 ✅, D1 🟡)

Goal (`docs/RUNBOOK.md` §2.1): feature store (Hopsworks + Parquet behind one
`Protocol`, identical test suite both backends), resumable chunked backfill to
2022-08-04, `pipelines/feature_pipeline.py` wired to the session-2 builder, and
its hourly workflow. Closes D3, D4, and D1 (which session 1 deliberately left
partial per ADR-010).

| Item | Status | Notes |
|---|---|---|
| `src/aqi/config.py` | done | Pydantic settings — `AppConfig` from `conf/config.yaml`, `Secrets` from env (I9), `load_zones()`/`get_zones()` implementing ADR-013 |
| `src/aqi/store/base.py` | done | `FeatureStore` Protocol, `validate_frame` shared precondition |
| `src/aqi/store/parquet_store.py` | done, **populated live** | `city=/year=/month=` partitions, idempotent upsert, `read_latest` anchored on stored data not wall-clock |
| `src/aqi/store/hopsworks_store.py` | written, **not live-tested** | 4.x SDK surface; needs `HOPSWORKS_API_KEY`/`HOPSWORKS_PROJECT` — both empty in `.env`, no project created yet |
| `tests/test_store_parity.py` | done | 4 passed (Parquet); Hopsworks half skipped (credentials absent), not mocked |
| `src/aqi/pipelines/common.py` | done | `fetch_zone_frame` — the fetch-three-sources-and-build shared by backfill and the hourly pipeline |
| `src/aqi/pipelines/backfill.py` | done, **ran to completion** | chunked per zone-month, manifest-resumable; killed and resumed once mid-run (see below) with zero reprocessing |
| `src/aqi/pipelines/feature_pipeline.py` | done, unit-tested | rolling fetch/write windows; live-integration-tested manually against real Open-Meteo (not yet via a green Actions run) |
| `.github/workflows/feature-pipeline.yml` | done, **not yet run in Actions** | hourly, mirrors `clock-starter.yml`'s shape incl. issue open/close on failure |
| `.github/workflows/clock-starter.yml` | fixed | completed 484b1ef's half-finished issue-open/close step — the commit message claimed it, the workflow never had it |
| `docs/DECISIONS.md` | done | ADR-013 (zone-level store/backfill granularity), ADR-014 (Parquet fallback commits in-repo, no `HF_TOKEN` yet) |
| `.gitignore` | fixed | `data/feature_store/` and `data/backfill_manifest.jsonl` were being silently excluded by the existing `data/*` rule — added exceptions before anything could be lost to it |
| `reports/metrics/coverage.json` | done | D4 evidence, generated not typed (I5) |
| `docs/DELIVERABLES.md` | done | D1, D3, D4 rows updated |

**D4 evidence, confirmed live:** `python -m aqi.pipelines.backfill --coverage-only` (or read
`reports/metrics/coverage.json` directly) → both zones, 2022-08-04 through 2026-08-29,
**49/49 months, zero gaps**, 35,808 rows each (71,616 total, 245 columns, ~61MB committed Parquet).

**D3 evidence, confirmed live:** `pytest tests/test_store_parity.py` → 4 passed, 4 skipped (Hopsworks,
no credentials). `data/feature_store/aqi_features/v1/` holds the real backfilled data above.

### A real bug the backfill run caught (not a unit-test artifact)

`aqi_nowcast` (`src/aqi/aqi_scale.py`) checked `v is not None` to skip missing hourly readings, but
`_add_derived` (`builder.py`) feeds it raw numpy array slices where a gap is `NaN`, not `None` — the
distinction pandas/numpy vs. this module's own test fixtures never exercised. The very first backfill
chunk (`capital:2022-08`) has a lag window reaching back to 2022-07-24, before the probed CAMS floor
of 2022-08-04, so its pm2_5 window was genuinely mixed None-context/NaN-context and it crashed
`_truncate`'s `math.floor(nan * factor)`. Fixed by normalizing both `None` and `NaN` to "missing" at
the top of `aqi_nowcast`; regression-tested in `tests/test_aqi_scale.py`
(`test_nan_float_is_treated_as_missing_like_none`, `test_all_nan_window_returns_none`). The backfill
process was killed mid-run (to apply the fix) and restarted with `force=False` — it skipped every
already-completed chunk from the manifest and resumed exactly where it left off, which is the
resumability contract working as designed, caught live rather than only in `tests/test_backfill.py`.

### What's still outstanding after this session

- **D1 stays 🟡, D3 stays 🟡** — both because of the same class of gap: code is written and tested,
  but has not yet had a *green GitHub Actions run* as its evidence (D1: `feature-pipeline.yml` has
  never executed in CI; D3: Hopsworks has never been exercised at all, live or in CI). Pushing this
  session's commit and either waiting for the next scheduled hour or running
  `workflow_dispatch` manually closes D1's gap the same way session 0 closed clock-starter's.
- **Hopsworks is a genuine credential block**, not a design gap — `HOPSWORKS_API_KEY` and
  `HOPSWORKS_PROJECT` are both empty in `.env`, and RUNBOOK §5 assigns creating that project to
  Aliza. `HopsworksFeatureStore` is written and structurally reviewed against the documented 4.x SDK
  but literally cannot be exercised without an account. **Needs Aliza:** create the Hopsworks
  project, put the two secrets in `.env` and GitHub Secrets, then re-run
  `pytest tests/test_store_parity.py` — the Hopsworks half will stop skipping automatically.
- **`HF_TOKEN` is the same story** (ADR-014) — until it and an HF Dataset repo exist, the Parquet
  fallback stays committed inside the repo (`data/feature_store/`, ~61MB now and growing hourly).
  Fine for now; worth revisiting if repo size becomes a real problem before submission.
- The instructor rubric-confirmation email (CLAUDE.md §2) — still no evidence in this repo's history
  that it has been sent. Flagged again, as every session has.

---

## Superseded — the old red-streak note (kept for the historical record)

**16 consecutive scheduled failures**, every hour since 2026-08-27T20:59:23Z.
The only success ever was the first manual `workflow_dispatch` run
(2026-08-27T10:45:43Z). Per CLAUDE.md's own anti-pattern list and
`docs/RUNBOOK.md` §6: *"The clock-starter workflow has been red for three
days. Stop everything else."* Every one of those ~90 hours is a permanent,
unrecoverable gap in the benchmark (I3) — AQICN has no history endpoint.

**What's confirmed:** the script succeeds locally, right now, against the
live AQICN API, using the token in this machine's `.env`. So it is not a
code bug that reproduces outside CI. Something is different about the
GitHub Actions environment specifically (most likely candidates: the
`AQICN_TOKEN` repo secret is empty, stale, or was rotated after the one
successful manual run; less likely, AQICN rate-limits or blocks GitHub's
runner IP ranges differently than a residential IP).

**What I could not do:** read the actual failure log. The Actions log
download API returned `403 Must have admin rights to Repository` to this
session's unauthenticated calls — there is no `gh` CLI or token available
here to see the real exception text, only the run's pass/fail conclusion.

**What I did:** commit `b8e0a21` makes `scripts/clock_starter.py` write a
markdown summary (per-city error text, plus the `AQICN_TOKEN` length —
never the value) to `$GITHUB_STEP_SUMMARY` on total failure. That summary
*is* readable through the public Checks API without admin rights
(`GET /repos/alizataimur/aqi-pearls/commits/{sha}/check-runs`, then
`output.summary` on the `capture` check run), which is what let this
session queue a background check of the next scheduled run. If that surfaces
a fixable bug, it'll be fixed and pushed automatically; if it points at the
secret, **only Aliza can fix that** — Settings → Secrets and variables →
Actions → `AQICN_TOKEN`, re-paste a fresh token from
https://aqicn.org/data-platform/token/.

**If you're reading this before the background check reports back:** open
the Actions tab yourself, click the newest failed `clock-starter` run, and
read the `Capture AQICN observation...` step's log directly — that's the
fastest path to ground truth and doesn't depend on this session's queued
check landing first.

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

## Decisions made this session (session 3)

- **ADR-013 (new).** The feature store, backfill manifest and
  `feature_pipeline.py` all key on **zone_id** (`capital`, `lahore`), not the
  three names in `conf/cities.yaml` — ADR-008 already proved Islamabad and
  Rawalpindi are one CAMS grid cell, so storing them separately would double
  every API call for zero information.
- **ADR-014 (new).** The Parquet fallback commits inside the repo
  (`data/feature_store/`) rather than an HF Dataset repo, because
  `HOPSWORKS_API_KEY`, `HOPSWORKS_PROJECT` and `HF_TOKEN` are all empty in
  `.env` — none of session 3's assigned accounts (RUNBOOK §5) exist yet.
  `root` is a constructor argument, so switching to a synced HF checkout
  later is a one-line change.
- **Bug fix, not a design decision:** `aqi_nowcast` treated a NaN `float` as
  present rather than missing — see the "real bug" note above. Fixed at the
  source in `aqi_scale.py` rather than papered over in the caller, since any
  future caller could hit the same gap.
- **Completed a half-finished commit.** `484b1ef` claimed "open a GitHub
  issue on failure, close it on recovery" for `clock-starter.yml` but only
  added the `permissions:` line — the actual steps were missing. Added them,
  and the equivalent in the new `feature-pipeline.yml`.

---

## The single next action

**Session 4** (`docs/RUNBOOK.md` §2.1): EDA notebook (`01_eda.ipynb`) and the
model-vs-station divergence notebook (`02_divergence.ipynb`) — D11's
distinctive answer, and genuinely novel for these coordinates. Also validates
each physics feature against PM2.5 spikes in `03_physics_features.ipynb`
(`stagnation_index` and friends — see "known gaps" below).

Before that, two low-effort loose ends worth five minutes each:
1. Push this session's commit, then confirm one green `feature-pipeline`
   Actions run (manual `workflow_dispatch` is fine) — closes D1 the same way
   session 0 closed clock-starter.
2. Glance at the Actions tab to confirm the first **scheduled** (not manual)
   `clock-starter` run since the ADR-012 fix went green.

Still needs Aliza:
- The rubric-confirmation email (CLAUDE.md §2) — still no evidence in this
  repo's history that it's been sent. Every session keeps flagging it.
- Create the Hopsworks project + put `HOPSWORKS_API_KEY`/`HOPSWORKS_PROJECT`
  in `.env` and GitHub Secrets (RUNBOOK §5) — `HopsworksFeatureStore` is
  written and ready, just never exercised against a live account.

---

## Known gaps carried forward

- **D1, D3 stay 🟡** until a green Actions run exists for `feature-pipeline`
  and until Hopsworks has been exercised live — see session 3's log entry
  for the exact gap on each.
- `stagnation_index` is an engineering-judgment composite (rolling-24h
  `1/(1+wind) * 1/(1+BLH) * humidity/100`), not a published index — session
  4's correlation check against PM2.5 spikes is where it earns or loses its
  place, same as every other physics feature.
- Festival dates in `conf/calendar_pk.yaml` are moon-sighting approximations
  (±1 day) — fine for a flag feature, called out explicitly in the file for
  anyone tempted to treat them as exact.
- Rawalpindi still has no pinned AQICN station (`conf/cities.yaml`) —
  unchanged from session 1. Doesn't block the feature store (ADR-013: it
  shares `capital`'s zone), only the ledger/clock-starter capture.
- The instructor rubric email — still not sent, as far as this repo shows.
- `data/feature_store/` is committed and will keep growing hourly once
  `feature-pipeline.yml` is live — not a problem yet at ~61MB, but worth a
  glance if repo size becomes a submission-week concern.

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

**2026-08-31 — Session 3: store, backfill, feature pipeline. D4 closed, D3 &
D1 advance to 🟡.** Confirmed the clock-starter recovery (ADR-012) live via
the Actions API before starting. Built `src/aqi/config.py` (Pydantic
settings + `load_zones()`), `src/aqi/store/{base,parquet_store,
hopsworks_store}.py` behind one `Protocol`, `tests/test_store_parity.py`
(Hopsworks half honestly skipped, not mocked, since no project exists yet).
Wrote ADR-013 (zone-level granularity — `capital`/`lahore`, not the three
`cities.yaml` names) and ADR-014 (Parquet fallback commits in-repo without
`HF_TOKEN`). Built `src/aqi/pipelines/{common,backfill,feature_pipeline}.py`
and `.github/workflows/feature-pipeline.yml`; also completed `484b1ef`'s
half-finished issue-open/close step in `clock-starter.yml`. **Ran the real
backfill against live Open-Meteo APIs** — 2022-08-04 through 2026-08-29, both
zones, 49/49 months, zero gaps, 71,616 rows / 245 columns / ~61MB committed
Parquet (`reports/metrics/coverage.json`). Caught and fixed a real bug along
the way: `aqi_nowcast` didn't treat NaN as missing the way it treats `None`,
which crashed on the very first backfill chunk's pre-floor lag context —
fixed in `aqi_scale.py`, regression-tested, and the killed-and-resumed run
proved the manifest's resumability contract for real, not just in a unit
test. Also caught `.gitignore`'s `data/*` rule silently excluding the new
store and manifest — fixed before anything could be lost to it. `ruff check`,
`ruff format --check`, `mypy src/`, `pytest` all green (128 passed, 4 skipped
pending Hopsworks credentials). D1 and D3 stay 🟡 pending a green Actions run
and a live Hopsworks project respectively — both are evidence gaps, not code
gaps.
