# STATE — current build position

> Read CLAUDE.md first for rules and contracts. This file is *position only*.
> Update it at the end of every session (CLAUDE.md §19).

**Stage:** 1 — The ugly slice / Session 1 (sources) — closing out
**Updated:** 2026-08-27
**Repo status:** public, pushed — https://github.com/alizataimur/aqi-pearls

---

## Stage 0 gate — CLOSED

| Item | Status | Notes |
|---|---|---|
| Repo skeleton + config | done | `conf/cities.yaml`, `conf/config.yaml` |
| `aqi_scale.py` + tests | done | 44 tests green; EPA 2024 breakpoints verified against the AQS code table |
| `scripts/clock_starter.py` | done | live-tested |
| `.github/workflows/clock-starter.yml` | done | hourly |
| AQICN token in GitHub Secrets | done | |
| Repo pushed, public | done | confirmed via GitHub API (`private: false`) |
| First green `clock-starter` run | **done** | 2026-08-27T10:45:43Z, `conclusion: success` |
| `make probe` findings recorded | done | `docs/schemas/probe_report.json`; ADR-007, ADR-008 |
| Rubric confirmed with instructor | **still open — needs Aliza** | decides how much of §3 survives; send the email |

**ci.yml is currently red** (`conclusion: failure` on the two most recent runs, both
before this session's fixes). Not yet re-verified against a push — see "single
next action" below. A red `ci` workflow does not carry I3's urgency the way a
red `clock-starter` would, but CLAUDE.md §19 still ranks fixing it above new
scope.

---

## Session 1 — Open-Meteo sources + schema capture (closes part of D1)

| Item | Status | Notes |
|---|---|---|
| `src/aqi/sources/_http.py` | done | shared GET-with-retry, stdlib only |
| `open_meteo_air.py` (CAMS pollutants) | done | domain pinned `cams_global`; 10 hourly vars incl. `us_aqi*` |
| `open_meteo_weather.py` (ERA5 archive) | done | actuals; shares parser with hist-forecast |
| `open_meteo_hist_forecast.py` | done | leakage-safe future covariates (I1) |
| Schema capture | done | `docs/schemas/open_meteo_{air_quality,weather_archive,historical_forecast}.json` |
| Offline unit tests | done | `tests/test_open_meteo_sources.py`, 13 tests, no network in CI |
| `conf/config.yaml: sources.backfill_start` | **filled in** | `2022-08-04`, from the probe — was left `null` since Stage 0 |
| `pipelines/feature_pipeline.py` | **not built** | deliberately deferred — see ADR-010 |
| Hourly feature-pipeline workflow | **not built** | same |

**D1 status: partial.** Sources exist and are schema-captured; the pipeline
that assembles them and the workflow that runs it hourly do not exist yet.
Session 2 closes the rest of D1 alongside D2 (see ADR-010 for why this session
didn't reach further).

---

## Finding that affects session 2 — `temperature_850hPa` gap (ADR-009)

Live-probed: `archive-api.open-meteo.com` (ERA5 actuals) does not serve
`temperature_850hPa` at these coordinates — `hourly_units` reports
`"undefined"` for it and every value is `null`, at every date tested from 10
to 365 days back. `historical-forecast-api.open-meteo.com` serves it fine.
`inversion_proxy` (CLAUDE.md §10 physics table) needs this field. **Session 2
must decide** how to source it for the "actuals" side of feature building —
three options are laid out, undecided, in ADR-009. Don't rediscover this by
re-probing; it's confirmed, not a fluke.

---

## The single next action

1. Push this session's commit, then check whether `ci.yml` goes green — it
   was red on the last two runs (both pre-date this session's mypy/ruff
   fixes, which were applied and verified green locally with the exact pinned
   tool versions). If it's still red after push, that is session 2's first
   task, ahead of any new feature work.
2. Session 2: feature + target builder, `conf/features.yaml` with `min_lag`,
   and `tests/test_no_leakage.py` — and resolve the ADR-009 gap before
   `inversion_proxy` is implemented, not after.
3. Still needs Aliza: the rubric-confirmation email (CLAUDE.md §2 day-one
   action) has not been sent as far as this repo's history shows. It still
   decides how much of §3 survives and should not keep sliding.

---

## Open questions — resolved

1. ~~Do Islamabad and Rawalpindi share one CAMS grid cell?~~ **No** — distinct
   served grid coordinates for all three cities (`probe_report.json`), but the
   underlying PM2.5 *data* is byte-identical for Islamabad/Rawalpindi anyway —
   CAMS global's ~0.4° native resolution can't separate them. One zone, two
   labels (ADR-008).
2. ~~What is the true earliest CAMS date?~~ **2022-08-04**, now set in
   `conf/config.yaml`.
3. ~~What shape is the AQICN feed?~~ Captured to `docs/schemas/aqicn_feed.json`.
   ADR-007's Delhi-station bug is what this probe caught, and station pinning
   plus `verify_station` (tested in `tests/test_aqicn_station.py`) is the fix.
4. **New, from session 1:** does ERA5 archive serve pressure-level variables?
   **No**, not at all, at this endpoint — see ADR-009 above. Open for
   session 2.

---

## Known gaps carried forward

- `tests/test_no_leakage.py` does not exist yet — nothing to leak until
  session 2's feature builder exists. **Session 2 gate item**, tracked in
  `.github/workflows/ci.yml`.
- `make backfill/features/train/...` are stubs that exit 1 by design.
- Deploy targets (Hopsworks, HF Spaces, Streamlit Cloud) not yet created.
- Rawalpindi has no pinned AQICN station (`conf/cities.yaml: aqicn_station:
  null`) — clock starter skips it rather than borrowing Islamabad's
  instrument (ADR-007). Find one with `scripts/diagnose_aqicn.py` when it
  matters for the benchmark.
- The instructor rubric email (CLAUDE.md §2) — no evidence in this repo's
  history that it has been sent.

---

## Log

**2026-08-27 — Stage 0 scaffolded.** Repo skeleton, config, `aqi_scale.py`
with 44 passing tests, clock starter, probe script, two workflows. EPA 2024
PM2.5 breakpoints verified against the authoritative AQS code table; kept the
501–999 band above 325.5 µg/m³ that most implementations clip away (ADR-003).
Clock starter changed from daily to hourly (ADR-002).

**2026-08-27 — Stage 0 closed.** Repo pushed public, AQICN token supplied,
first `clock-starter` run green (10:45:43Z). Probe run live: found the Delhi
station-substitution bug (ADR-007, severity: would have silently invalidated
the whole benchmark) and the Islamabad/Rawalpindi grid-cell finding
(ADR-008). `aqicn.py` rewritten to pin stations by index and verify every
capture before it touches the ledger.

**2026-08-27 — Session 1: Open-Meteo sources.** Built `open_meteo_air.py`,
`open_meteo_weather.py`, `open_meteo_hist_forecast.py` and a shared
`_http.py` retry helper; captured live response shapes to `docs/schemas/`;
13 offline unit tests, no network dependency in CI. Filled in
`conf/config.yaml`'s `backfill_start` from the probe (was left `null` since
Stage 0). Found and recorded ADR-009: the ERA5 archive endpoint does not
serve `temperature_850hPa` at all, which blocks a literal reading of
`inversion_proxy` until session 2 picks a resolution. Scoped session 1 to the
source layer only, deliberately deferring `pipelines/feature_pipeline.py` and
its workflow to session 2 (ADR-010) rather than half-building a pipeline with
nothing yet to feed a store. `make lint` and `make test` green locally
(ruff, ruff format, mypy --strict, pytest — 74 tests).
