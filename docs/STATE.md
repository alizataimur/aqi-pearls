# STATE — current build position

> Read CLAUDE.md first for rules and contracts. This file is *position only*.
> Update it at the end of every session (CLAUDE.md §19).

**Stage:** 4 — Make it usable, in progress / Session 6 (serving, dashboard, explanations, alerts)
— CLOSED (D9 ✅, D10 🟡, D13 ✅, D14 🟡)
**Updated:** 2026-09-01
**Repo status:** public, pushed — https://github.com/alizataimur/aqi-pearls
**Live dashboard:** <https://aqi-pearls-predictor.streamlit.app/> — confirmed rendering on the
**live model path**, not the static-snapshot fallback, after ADR-031's fix (verified via a fresh
`git clone`, not the working tree — see below). Runs entirely on the I10 direct-store/registry
fallback path today, not the API: no FastAPI service is deployed anywhere, so `_api_get()` always
fails over to `serving/inference.py` directly. See D10.
**Held-out test window (I2):** the **2025-26 smog season** (Oct 2025 – Feb 2026) — see ADR-016. It
is the most recent complete season with both `boundary_layer_height` series (observed and
historical-forecast) fully populated; earlier seasons either predate the forecast-BLH archive's
coverage here or fall inside the observed-BLH gap (ADR-015). Session 5's `evaluation/splits.py`
walk-forward folds must end with this season as the final test chunk.
**Live serving model (not the ladder champion):** `/forecast`, `/explain`, the Streamlit app and the
alert rule all run on the registered **LightGBM** model, not SARIMAX (`reports/metrics/ladder.json`'s
champion by backtest RMSE) — SARIMAX's registered artifact only supports retrospective scoring, not
genuine forward prediction. See ADR-025.

---

## Post-session-6 incidents — Streamlit Cloud deploy, three bugs, all fixed, one debt recorded

The first real deploy hit three bugs a purely-local session never surfaces, all
now fixed:

1. **`ModuleNotFoundError: aqi`** — Cloud installs only `requirements.txt`,
   never runs this repo's own `pip install -e .`, so `import aqi...` failed.
   Fixed: `app/streamlit_app.py` puts `src/` on `sys.path` itself, right after
   `from __future__ import annotations` and before any `aqi.*` import.
2. **`FileNotFoundError` on `data/model_registry/.../metadata.json`** — that
   directory (like `data/feature_store/`) is gitignored; a fresh Cloud
   checkout has none of it, only this dev machine's local training-pipeline
   output did. Fixed two ways: (a) force-added the Model Registry and a
   two-month feature-store slice into git as a deadline expedient (ADR-030 —
   **debt, not a design decision**: the real fix is D3, Hopsworks, going
   green); (b) `load_serving_model` (`serving/inference.py`) now raises one
   typed `ModelUnavailableError` instead of a raw `FileNotFoundError`, and
   both `get_forecast()`/`get_explain()` (`app/streamlit_app.py`) catch it and
   fall back to `reports/dashboard_snapshot.json` with a visible
   "live model unavailable" banner — I10 applied to a failure mode the
   original I10 fallback chain hadn't covered (missing *artifact*, not just
   an unreachable API).
3. **The banner from (2) kept showing even after the registry was tracked** —
   `metadata.json`'s `artifact_path` was an absolute, machine-specific path
   (`C:\Users\xesha\Documents\aqi-pearls\...`), baked in by whichever machine
   ran the training pipeline. Correctly tracked by git, present in every
   checkout, and still unresolvable on any *other* machine. Found by cloning
   the repo fresh and reading the actual raised `FileNotFoundError`, not by
   guessing from source (ADR-031) — which also directly checked and **refuted**
   the standing hypothesis that the committed feature-store slice (July+Aug
   only) was too short; that's a real, separate risk, now guarded by a new
   test, but it was not this bug. Fixed: `LocalModelRegistry.
   resolve_artifact_path()` reconstructs a path anchored to *this* checkout
   from just the artifact's filename, fixing every already-committed
   `metadata.json` without needing to regenerate any of them.

`tests/test_deploy_assets.py` now checks three separate things, because
tracked-ness (bug 2's fix) turned out not to be sufficient on its own (bug 3):
every startup-critical file is **tracked** (`git ls-files`); the committed
feature-store slice **covers enough lookback** (>=2 consecutive tracked
months per zone, guarding the risk ADR-031 checked and ruled out for today
but which remains real); and the registry's artifact paths **resolve
portably** on this checkout, not just exist somewhere. This is the check that
would have caught all three bugs before the deploy; it's in CI now.

**Carried forward as debt (ADR-030):** once D3 is green, `git rm --cached` the
committed registry/feature-store slice, revert to reading them live, and
rewrite `tests/test_deploy_assets.py` to check the live store instead of
`git ls-files`.

---

## Session 6 — Serving, dashboard, explanations, alerts — CLOSED
(D9 🟡, D10 🟡, D13 ✅, D14 🟡)

Goal (deadline-day session brief, D9/D10/D13/D14): a FastAPI service reading the feature store and
Model Registry; a 4-page Streamlit dashboard (Now / 3-day forecast / Why / Model card — the
Scorecard page cut, ledger too sparse to be honest about — ADR-027); SHAP explanations from the
registered LightGBM model (SARIMAX ruled out — the champion by RMSE, but not tree-explainable, and a
KernelExplainer was explicitly out of scope); Telegram alerts on the D+1 forecast crossing 200
(P(AQI>200) unavailable, classifier cut); a `--static` demo mode reading a committed JSON snapshot.
Built and committed in five steps, one commit each, so a late failure couldn't lose earlier work.

| Item | Status | Notes |
|---|---|---|
| `src/aqi/serving/inference.py` | done, new | Shared prediction path — loads the registered LightGBM model (not SARIMAX, ADR-025), builds a live feature row from the zone's latest feature-store hour, serves `/current` and `/forecast`'s numbers |
| `src/aqi/serving/api.py` | done, new, **ran live** | 6 endpoints: `/health`, `/cities`, `/current`, `/forecast`, `/explain`, `/metrics`. `uvicorn aqi.serving.api:app` tested manually; `pytest tests/test_api.py` (8 tests) |
| `src/aqi/serving/schemas.py` | done, new | Pydantic response models, one per endpoint |
| `src/aqi/explain/shap_explain.py` | done, new | `shap.TreeExplainer` on the registered LightGBM model; strict-template briefing sentences (no LLM), English + native Urdu |
| `src/aqi/explain/i18n.py`, `conf/i18n_ur.yaml` | done, new | Hand-written health guidance + alert templates, English/Urdu — **not yet native-speaker reviewed** (ADR-029, flagged in the YAML header too) |
| `app/streamlit_app.py` | done, new, **ran live** | 4 pages. API-first, falls back to `serving/inference.py`/`explain/shap_explain.py` directly on any API failure (I10) — `tests/test_streamlit_app.py` runs every page with **no API server running**, so the fallback path is what's actually tested |
| `src/aqi/alerts/rules.py` | done, new | Episode/all-clear state machine (`data/alerts_state.json`), triggers on D+1 LightGBM forecast > 200 (ADR-026, a documented substitute for CLAUDE.md's real `P(AQI>200) > 0.6` rule) |
| `src/aqi/alerts/telegram.py` | done, new, **not live-tested** | Env-only credentials (I9); `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are both empty (same credential gap as Hopsworks) — send path tested against a mocked `requests.post`, no real message sent yet |
| `scripts/render_static_snapshot.py`, `reports/dashboard_snapshot.json` | done, new, **ran live** | `--static` mode's data source, committed like every other `reports/` artifact (I5) |
| `pyproject.toml` | fixed | `shap` pin bumped 0.46.0 → 0.52.0 (ADR-028, same class of fix as ADR-019's `torch` bump — no MSVC toolchain in this environment); `httpx`, `types-requests` added to `dev` |
| `docs/DECISIONS.md` | done | ADR-025 through ADR-029 |
| `docs/DELIVERABLES.md` | done | D9, D10, D13, D14 rows updated |

**A real architectural finding, not just a status update:** the ladder's metrics champion (SARIMAX)
and the model that actually answers every live question on the dashboard (LightGBM) are different
models, for a structural reason discovered this session — SARIMAX's session-5 registry artifact
can only score a backtest, not forecast a real future day. Documented in ADR-025, in
`serving/inference.py`'s module docstring, and surfaced to the user on every relevant page/response
(`ExplainResponse.explainer_note`, the Why page's warning banner) — not something to notice only by
reading code.

### What's still outstanding after this session

- **D9/D10 stay 🟡** — both run correctly locally (evidenced by passing tests against real data) but
  aren't deployed to a public URL. **Needs Aliza:** Streamlit Community Cloud + an HF Space
  (`docs/RUNBOOK.md` §5, assigned to session 10 — this session did the code, not the deploy).
- **D14 stays 🟡** — code and tests are real and green, but no live Telegram message has been sent.
  **Needs Aliza:** `/newbot` with @BotFather, then supply `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` and
  re-run `python -m aqi.alerts.telegram`.
- **SARIMAX still can't serve a genuine live forecast** — `get_forecast(steps=h, exog=...)` is the
  correct fix (ADR-025) and is a clean, scoped piece of future work, not attempted this session.
- **`conf/i18n_ur.yaml` needs a native-speaker review pass** (ADR-029) — ~16 strings, health guidance
  and alert templates.
- Session 4's leftover notebooks (`02_divergence.ipynb`, `03_physics_features.ipynb`) — still not
  started, unchanged from session 5's handoff.
- D1, D3 stay 🟡 — unchanged, still pending a green `feature-pipeline` Actions run and a live
  Hopsworks project respectively.
- The instructor rubric-confirmation email — still outstanding, every session keeps flagging it.

---

## Session 5 — The ladder: baselines through LSTM, Model Registry — CLOSED
(D5 ✅, D6 ✅, D7 ✅, D12 ✅)

Goal (`docs/RUNBOOK.md` §2.1, tightened by an explicit deadline-day session brief): daily max US AQI
at D+1/D+2/D+3, both zones, from the feature store only. Persistence/seasonal-naive/climatology
baselines, then Ridge, Random Forest, SARIMAX, LightGBM, a small PyTorch LSTM. One split (train =
everything before the 2025-26 smog season minus the 72h purge gap; test = the season itself —
ADR-016). RMSE/MAE/R² per horizon plus a simple precision/recall/F1 table at AQI>200, to
`reports/metrics/ladder.json`. Champion registered locally with metrics attached. Conformal
intervals, Mondrian coverage, lead-time analysis, hyperparameter search and the AQICN benchmark were
explicitly cut for this session (session brief) — differentiator work for a later session, not a
design gap.

| Item | Status | Notes |
|---|---|---|
| `src/aqi/features/spec.py::admitted_columns` | done | Vectorized twin of `builder.feature_vector`'s per-horizon admission check — bulk-selects a horizon's admitted columns from a full frame instead of looping per row. Proven identical to `feature_vector` by a new equivalence test in `tests/test_no_leakage.py` (`TestAdmittedColumnsVectorized`) — the ladder's training matrix can't silently admit a column no single-row lookup would ever have allowed |
| `src/aqi/evaluation/metrics.py` | done, new | `regression_metrics` (RMSE/MAE/R², NaN-pair-safe) and `episode_precision_recall_f1` (AQI>200) — dependency-free, used identically by every rung |
| `src/aqi/models/dataset.py` | done, new | `load_ladder_frame` (both zones, full history, via `get_store()` — I10), `daily_aqi_by_date` (reconstructs a `date -> daily_aqi` series from the stored `target_daily_aqi_h24` column — no raw refetch, ADR-022), `smog_season_split` (the one Split, ADR-016), `build_horizon_matrix` (admitted-columns + zone one-hot + dropna, shared by Ridge/RF/LightGBM), `build_sequence_matrix` (the LSTM's lag-ordered sequence input) |
| `src/aqi/models/baselines.py` | done, new | Persistence, seasonal-naive, climatology (train-only fit) — ADR-022 |
| `src/aqi/models/{linear,forest,gbdt}.py` | done, new | Ridge (sklearn `Pipeline` + `StandardScaler`), Random Forest, LightGBM — small fixed hyperparameters (CLAUDE.md §4) |
| `src/aqi/models/sarimax.py` | done, new | Daily-granularity SARIMAX with leakage-safe `fc_*_h{h}` exogenous covariates, `method="powell"` (this env's scipy is newer than statsmodels==0.14.4 was tested against — ADR-023), positional `RangeIndex` (real BLH/forecast-archive gaps make the daily series non-contiguous — ADR-023) |
| `src/aqi/models/deep.py` | done, new | Small 1-layer PyTorch LSTM (hidden=16, 8 epochs) over an 8-step lag-ordered sequence; train-only target normalization added after a first run scored RMSE 136 without it (ADR-024) |
| `src/aqi/models/registry.py` | done, new | `LocalModelRegistry` — Hopsworks is a credential gap, same as D3's (ADR-020); one entry per `(model_name, horizon_hours)` under `data/model_registry/` (gitignored, regenerable) |
| `src/aqi/pipelines/training_pipeline.py` | done, new, **ran live** | Orchestrates the full ladder; `python -m aqi.pipelines.training_pipeline` |
| `reports/metrics/ladder.json` | done, **generated live** | See results table below |
| `pyproject.toml` | fixed | `torch` pin bumped 2.5.1 → 2.7.1 — this venv runs Python 3.13, and 2.5.1 has no cp313 wheel (ADR-019) |
| `tests/{test_metrics,test_dataset,test_baselines,test_registry}.py` | done, new | Synthetic-fixture unit tests, no network, no real feature store |
| `tests/test_no_leakage.py` | extended | `TestAdmittedColumnsVectorized` — new class, same file (I1: never a new file for a leakage check, always the one CLAUDE.md names) |
| `docs/DECISIONS.md` | done | ADR-019 through ADR-024 |
| `docs/DELIVERABLES.md` | done | D5, D6, D7, D12 rows: ⬜ → ✅ |

**The ladder result, live from `reports/metrics/ladder.json`** (mean RMSE across h24/h48/h72; full
per-horizon RMSE/MAE/R² and precision/recall/F1 at AQI>200 are in the JSON, not reproduced here per
I5):

| Rung | Mean RMSE |
|---|---|
| **sarimax (champion)** | **19.50** |
| lstm | 26.59 |
| ridge | 27.09 |
| lightgbm | 27.45 |
| random_forest | 30.74 |
| persistence | 31.36 |
| climatology | 35.50 |
| seasonal_naive | 38.92 |

**The genuine finding worth carrying into the report:** SARIMAX — the "statistical" rung, not the
GBDT or the LSTM — won at every horizon, degrading only 19.44 → 19.62 RMSE from D+1 to D+3 while
every other rung degrades noticeably more (e.g. LightGBM 21.08 → 31.96). CLAUDE.md I6/§12.1 call this
out explicitly: a ladder where the simple model sometimes wins reads as mature, and this one does.
See ADR-021 and ADR-023 for the two live hypotheses (daily aggregation smoothing vs. under-tuned ML
rungs at a fixed, un-searched hyperparameter grid) — distinguishing them needs the hyperparameter
search CLAUDE.md §4 cuts, so it's future work, not resolved here.

**A real bug caught before it shipped:** the LSTM's first live run scored RMSE 136 (worse than
predicting the mean) from an untrained-net-vs-raw-0-500-scale mismatch. Fixed with train-only target
normalization (ADR-024), verified by re-running against real data, not assumed fixed from the code
change alone.

### What's still outstanding after this session

- **D5's Hopsworks half** — same credential gap as D3, unchanged. `LocalModelRegistry` is real and
  exercised; `HopsworksModelRegistry` doesn't exist yet (ADR-020), deliberately, per the prime
  directive (an untestable stub is worse than an honest gap).
- **Champion selection used mean RMSE, not CLAUDE.md §12.3's median lead time** — that metric needs
  `evaluation/episodes.py` and a populated ledger, both differentiator work cut this session
  (ADR-021). Re-run promotion under the real rule once that machinery exists; the champion may change.
- **Rung 0d (AQICN's own forecast) is not in the ladder** — the ledger is still too sparse
  (`docs/STATE.md`'s divergence-notebook note, carried from session 4) and the benchmark pipeline is
  differentiator #3, cut this session.
- Session 4's leftover items (`02_divergence.ipynb`, `03_physics_features.ipynb`) are still not
  started — untouched this session, still next in line per session 4's own handoff.
- D1, D3 stay 🟡 — unchanged, still pending a green `feature-pipeline` Actions run and a live
  Hopsworks project respectively.
- The instructor rubric-confirmation email and Hopsworks/HF account creation (RUNBOOK §5) — still
  outstanding, every session keeps flagging them.

---

## Session 4 — EDA + physics validation + divergence — PARTIAL (D11 🟡)

Goal (`docs/RUNBOOK.md` §2.1): `01_eda.ipynb`, `02_divergence.ipynb`,
`03_physics_features.ipynb`, plus fixing `reports/metrics/coverage.json` to
report data presence (per-column nulls) rather than just row presence.
**Cut short mid-session under a hard deadline** — instructed to finish
`01_eda.ipynb` only, update docs, commit, and stop. `02_divergence.ipynb` and
`03_physics_features.ipynb` were not started as notebooks (their supporting
machinery — the ledger reader, the null-rate coverage report — was built
first and is done; see below).

**Entering this session, already done and committed** (session 4's earlier
work, prior to this cutoff): ADR-015 (explicit `min_periods=window` on every
rolling stat; `boundary_layer_height_is_missing` / `stagnation_index_is_missing`
/ `ventilation_index_is_missing` flags) and ADR-016 (2025-26 smog season as
the held-out test window — now also called out at the top of this file, not
just in `docs/DECISIONS.md`, since session 5 needs it and shouldn't have to
re-derive it from the raw BLH gap data). Not redone this session, per the
session brief.

| Item | Status | Notes |
|---|---|---|
| `reports/metrics/coverage.json` | done | Now reports **per-column null rates per zone** (nonzero only), not just row presence. Regenerated live: `boundary_layer_height` shows 12.24% null both zones (the ADR-015 gap, now visible in the artifact that's supposed to describe it), `fc_boundary_layer_height_h24` ~51% null (the forecast-archive gap, present before late Aug 2024). Opt-in via a new `store` parameter on `build_coverage_report` so the existing manifest-only unit test stays offline; `main()` always passes `get_store()` in production |
| `src/aqi/store/ledger.py` | done, new | `read_ledger(kind, root)` and `ledger_window(kind, root)` — reads `observed`/`aqicn` JSONL, always excludes `_quarantine/`. Read-only; never writes (I3). `tests/test_ledger.py`, 6 tests |
| `notebooks/01_eda.ipynb` | done | Full backfill window (2022-08 to 2026-09), both zones. Four sections, each with a chart + a markdown finding grounded in numbers the notebook itself computes and prints: monthly climatology (smog-season asymmetry between zones), diurnal profile (a genuine capital-vs-Lahore winter shape difference, flagged as a hypothesis), STL decomposition (residual variance measurably higher in smog season — printed, not just eyeballed), and a full correlation ranking (separates PM10/combustion collinearity from the weaker dispersion signal). Executed end-to-end with `nbclient` before output-clearing (no errors). Cell outputs cleared before commit (CLAUDE.md §16); figures are the durable evidence, saved to `reports/figures/eda_monthly_climatology.png`, `eda_diurnal_profile.png`, `eda_stl_decomposition.png`, `eda_correlation_heatmap.png` |
| `notebooks/02_divergence.ipynb` | **not started** | Cut for time. The ledger currently holds **6 rows** (observed: islamabad×2, lahore×2; aqicn: islamabad×1, lahore×1 — confirmed via `read_ledger`), spanning 2026-08-31 only. `ledger.py` and the null-rate-aware coverage report are the machinery this notebook needs; writing it is a re-run away, not a re-design |
| `notebooks/03_physics_features.ipynb` | **not started** | Cut for time. The question it must answer — does `inversion_proxy` alone carry the dispersion signal `stagnation_index`/`ventilation_index` were meant to add, given the BLH gaps — is unanswered. `01_eda.ipynb`'s correlation section found `inversion_proxy` positively correlated with PM2.5 even unconditionally (r=0.25 capital, r=0.50 Lahore) — a head start, not a substitute for the real spike-conditioned validation |
| `docs/DELIVERABLES.md` | done | D11 row: ⬜ → 🟡, evidence updated to point at the real notebook + figures, outstanding items named |

**A genuine finding worth carrying into the report regardless of which session writes it up:**
`01_eda.ipynb`'s STL decomposition shows residual (unexplained-by-season) variance is **~30% higher
in smog season for the capital and more than double for Lahore**, computed directly from the
decomposition, not eyeballed from a chart. That is direct evidence the hardest-to-predict days
cluster in exactly the season the episode metrics (§12.4) and the held-out test window (I2, ADR-016)
are scored on.

### What's still outstanding after this session

- **`02_divergence.ipynb` and `03_physics_features.ipynb` are the first job of next session.**
  Neither is blocked on anything external — the ledger reader and the null-rate coverage report
  (both built this session) are exactly the machinery they need. `02_divergence.ipynb` must print its
  ledger window and row count at the top and **must not draw a conclusion from 6 rows** — state the
  limitation plainly, build the join-and-compare machinery so it is genuinely re-runnable, and note
  that a real read happens once the ledger has accumulated meaningfully more history (the clock
  starter is hourly and live — see below).
- D1, D3 stay 🟡 — unchanged from session 3, still pending a green `feature-pipeline` Actions run and
  a live Hopsworks project respectively. Not touched this session.
- The instructor rubric-confirmation email and the Hopsworks/HF account creation (RUNBOOK §5) — still
  outstanding, every session keeps flagging them.

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

**Deploy `serving/api.py` to an HF Space and point the live Streamlit app at it** (closes D10). Facts
established this turn, from the code and a live check, not assumed: no FastAPI deployment exists
anywhere (checked `requirements.txt`, `docs/RUNBOOK.md` §5, and the repo/docs for any URL — found
none); the deployed Streamlit app's `API_URL` defaults to `http://localhost:8000` and nothing sets
`AQI_API_URL`, so every `_api_get()` call fails over to the I10 direct-store fallback — confirmed by
reading `app/streamlit_app.py`, not the architecture diagram. **Needs Aliza:** create the HF Space
(`docs/RUNBOOK.md` §5), then set `AQI_API_URL` as a Streamlit Cloud secret pointing at it. Once both
exist, D10 goes ✅.

Also still outstanding:

**Episode metrics + conformal intervals** (differentiators #1/#2, `docs/RUNBOOK.md` §2.1's session 6
— superseded in numbering by this session's serving/dashboard work, but not in scope): CSI,
false-alarm ratio by season, **lead time** (CLAUDE.md §12.3's real primary metric), plus MAPIE
conformal intervals per horizon, Mondrian-grouped by season and AQI band. Both explicitly cut from
session 5 (`docs/DECISIONS.md` ADR-021) and still cut here. Once lead time exists: (a) re-run
champion promotion under the real rule — session 5's champion (SARIMAX, mean RMSE 19.50) was
selected by mean RMSE instead and may not hold; (b) fix `alerts/rules.py` to trigger on
`P(AQI>200) > 0.6` instead of ADR-026's point-forecast substitute. **Before installing `mapie`, note
ADR-019**: `mapie==1.0.1`'s pin has no wheel for this venv's Python 3.13 either (same class of
problem `torch`/`shap` hit — ADR-019, ADR-028) — needs its own version bump or a 3.11 environment.

**SARIMAX live forecasting** (ADR-025): rework `models/sarimax.py` to call
`SARIMAXResults.get_forecast(steps=h, exog=...)` so the ladder's actual metrics champion can serve
`/forecast` and `/explain` instead of the LightGBM substitute session 6 shipped. Scoped, understood,
not attempted under this session's deadline.

Also still outstanding from session 4, not picked up this session:
`02_divergence.ipynb` and `03_physics_features.ipynb`. Both were cut under session 4's hard
deadline, not because of a blocker — `src/aqi/store/ledger.py` (reads the ledger, excludes
quarantine) and the null-rate-aware `reports/metrics/coverage.json` were built specifically so these
two notebooks would be a straightforward write, not a re-design, when picked back up.
`02_divergence.ipynb` must print the ledger's window and row count at the top and explicitly not
draw a conclusion from however few rows exist at that point (CLAUDE.md I4) — re-run it again before
the report is finalized (session 12), once the ledger has real history. `03_physics_features.ipynb`
answers whether `inversion_proxy` alone carries the dispersion signal `stagnation_index`/
`ventilation_index` were meant to add, by correlating each against PM2.5 spikes; `01_eda.ipynb`'s
unconditional correlation numbers (r=0.25 capital, r=0.50 Lahore for `inversion_proxy`) are a head
start, not a substitute.

Also still open from session 3, unchanged:
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
- **New this session:** `/newbot` with @BotFather, then put
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env` and GitHub Secrets
  (RUNBOOK §5) — `alerts/telegram.py` is written and tested against a mock,
  just never exercised against a real chat. Re-run
  `python -m aqi.alerts.telegram` once set to send the real test message.
- **Streamlit Community Cloud app: done** —
  <https://aqi-pearls-predictor.streamlit.app/> is live, confirmed on the
  live model path (ADR-031). D9 is ✅. **Still needed:** the HF Space for the
  FastAPI service (RUNBOOK §5), then set `AQI_API_URL` as a Streamlit Cloud
  secret pointing at it — D10 stays 🟡 until both exist, see "single next
  action" above.

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
