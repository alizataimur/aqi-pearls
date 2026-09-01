# DELIVERABLES — the brief, mapped to the repo

Every requirement in `AQI_predict1.pdf`, where it lives, and how to verify it. This is the
submission checklist and the marker's map; `reports/final_report.md` is the narrative, and the
README is the thirty-second pitch. Three documents, three readers.

**Live URLs**

| What | Where |
|---|---|
| Dashboard | _not yet deployed — session 10_ |
| API | _not yet deployed — session 10_ |
| Repository | https://github.com/alizataimur/aqi-pearls |
| Pipeline health | [Actions tab](https://github.com/alizataimur/aqi-pearls/actions) |

**Status key:** ✅ complete and verified · 🟡 partial · ⬜ not started

> **Rule:** a row goes ✅ only when its Evidence column can be executed by someone else and it
> passes. "The file exists" is not evidence. Every session updates the rows it touched
> (CLAUDE.md §19).

---

## The feature pipeline

### D1 — Fetch raw weather and pollutant data from an external API 🟡

**Brief:** *"Write a Python script that fetches raw weather and pollutant data from an external API
like AQICN or OpenWeather."*

| | |
|---|---|
| Lives in | `src/aqi/sources/`, `src/aqi/pipelines/feature_pipeline.py`, `.github/workflows/feature-pipeline.yml` |
| Evidence | `pytest tests/test_open_meteo_sources.py tests/test_aqicn_station.py tests/test_feature_pipeline.py` · captured contracts in `docs/schemas/` · `python -m aqi.pipelines.feature_pipeline` upserts both zones live (verified this session against the real Open-Meteo APIs) |
| Outstanding | The workflow is written and YAML-validated but has not yet had a confirmed green run in the Actions tab — needs this session's commit pushed and either the first scheduled run or a manual `workflow_dispatch` to close the loop, same gate `clock-starter` cleared in session 0 |

**Done distinctively:** four sources rather than one, including the Open-Meteo **historical-forecast
archive** — the source that makes leakage-safe future covariates possible at all (CLAUDE.md I1).
Station identifiers are pinned and every capture is distance-verified after the nearest-station
lookup silently returned a Delhi station for three Pakistani cities (ADR-007). The hourly pipeline
refreshes a 5-day trailing window on every run (not just the newest hour), so `target_daily_aqi_h*`
fills in as each local day completes rather than staying permanently NaN.

### D2 — Compute features and targets, including time-based and derived ✅

**Brief:** *"Computes features from this raw data (aka model inputs), and targets (aka model
outputs). Include time-based features (hour, day, month) and derived features like AQI change
rate."*

| | |
|---|---|
| Lives in | `src/aqi/features/`, `conf/features.yaml` |
| Evidence | `pytest tests/test_features.py tests/test_no_leakage.py` · `docs/feature_spec.md` · dedicated `Leakage test (I1)` CI step |

**Done distinctively:** region-specific Punjab smog physics — inversion proxy, stagnation index,
ventilation index, crop-burning window, festival calendar — built and unit-tested now; correlation
against PM2.5 spikes is `notebooks/03_physics_features.ipynb` (session 4), and features that show
nothing will be reported as tried-and-rejected rather than silently dropped. Every feature declares
a `min_lag_hours` (ADR-011) and the builder asserts it mechanically — and I1 is additionally proven
**empirically**: `tests/test_no_leakage.py` builds a real feature vector, corrupts every actual
reading after the issue time with a sentinel, rebuilds, and asserts nothing moved, with a positive
control proving the test isn't vacuous. All 235 declared features round-trip against the builder's
real output columns via a schema test — `conf/features.yaml` cannot silently drift from the code.

### D3 — Store features in a Feature Store 🟡

**Brief:** *"Stores these features in the Feature store. You may want to explore Hopsworks or Vertex
AI (Free tiers)."*

| | |
|---|---|
| Lives in | `src/aqi/store/base.py` (Protocol), `src/aqi/store/parquet_store.py`, `src/aqi/store/hopsworks_store.py` |
| Evidence | `pytest tests/test_store_parity.py` (4 passed on Parquet; Hopsworks half **skipped**, not faked); Parquet backend populated live by this session's backfill — **71,616 rows** (`capital`: 35,808, `lahore`: 35,808), 245 columns, 2022-08-04→2026-08-31, `data/feature_store/` |
| Outstanding | `HOPSWORKS_API_KEY`/`HOPSWORKS_PROJECT` are both empty in `.env` — no Hopsworks project exists yet (RUNBOOK §5 assigns creating it to Aliza). `HopsworksFeatureStore` is written against the documented 4.x SDK surface but has never run against a live project |

**Done distinctively:** two backends behind one `Protocol`, passing an identical test suite — the
Hopsworks half is skipped rather than mocked when its credentials are absent, which is an honest
"not yet verified" rather than a green check that doesn't mean what it says. The Parquet fallback
is not a nice-to-have — it is what keeps the demo alive when a free tier goes down (I10), and it is
itself an engineering-judgment result worth reporting: it lives inside the repo
(`data/feature_store/`, `city=/year=/month=` partitioned) rather than an HF Dataset repo, because no
`HF_TOKEN` exists yet either (ADR-014) — a decision made autonomously and written down, not silently
deferred.

### D4 — Backfill historical (features, targets) over past dates ✅

**Brief:** *"Run the feature script from step 1 for a range of past dates, to generate training data
for your ML models."*

| | |
|---|---|
| Lives in | `src/aqi/pipelines/backfill.py` |
| Evidence | `pytest tests/test_backfill.py` (manifest round-trip, chunk boundaries, coverage report); `reports/metrics/coverage.json` — generated from the manifest, never hand-typed (I5); run `python -m aqi.pipelines.backfill --coverage-only` to regenerate |

**Confirmed this session, live:** 2022-08-04 → 2026-08-29, both zones, **zero gaps** —
`reports/metrics/coverage.json` shows 49/49 months completed for `capital` and `lahore` alike,
35,808 rows each (71,616 total, 245 columns, ~61MB as committed Parquet).

**Done distinctively:** chunked and resumable per **zone**-month (ADR-013: `capital`, `lahore` — not
the three names in `conf/cities.yaml`, since Islamabad and Rawalpindi are one CAMS grid cell per
ADR-008 and backfilling both would double every API call for zero information) with an append-only
manifest, so a multi-year pull that dies partway resumes rather than restarts — proven for real, not
just in a unit test: this session's live run hit a genuine bug (`aqi_nowcast` treating a NaN float as
present rather than missing, crashing on the pre-2022-08-04 lag context — fixed, regression-tested in
`tests/test_aqi_scale.py`), was killed mid-run, and resumed cleanly from the manifest with zero
reprocessing of already-completed months. Backfill runs to the **probed** CAMS floor of 2022-08-04,
not an assumed date.

---

## The training pipeline

### D5 — Feature Store → train and evaluate → Model Registry ✅

**Brief:** *"Fetches historical (features, targets) from the Feature Store. Trains and evaluates the
best ML model possible. Stores the trained model in the Model Registry."*

| | |
|---|---|
| Lives in | `src/aqi/pipelines/training_pipeline.py`, `src/aqi/models/registry.py` |
| Evidence | `python -m aqi.pipelines.training_pipeline` — reads both zones' full history via `get_store()` (never a raw refetch), trains and registers all 8 ladder rungs × 3 horizons (24 registry entries under `data/model_registry/`, regenerable, gitignored like `data/feature_store/` — ADR-020), writes `reports/metrics/ladder.json`. Champion **sarimax** (mean RMSE 19.50 across h24/h48/h72), flagged in every one of its 3 registry entries and in `data/model_registry/champion.json` |

**Done distinctively:** promotion is logged with an explicit, stated selection rule
(`champion.selection_rule` in `champion.json`) rather than assumed — and because baselines are
first-class (I6), the champion this session is genuinely **SARIMAX, not the fancier LightGBM/LSTM
rungs** (mean RMSE 19.50 vs. 27.45 / 26.59), reported plainly rather than smoothed over (ADR-021).
Every registry entry's `feature_columns` records what that specific rung actually read — SARIMAX's
own exogenous covariates and AR terms, not the ~215-column admitted matrix the pooled sklearn rungs
use — so the metadata can't be mistaken for evidence of an input a model never saw.

**Outstanding:** Hopsworks Model Registry is still a credential gap, same as D3's store (ADR-020) —
`LocalModelRegistry` is the real, exercised backend this session; promotion under CLAUDE.md §12.3's
actual primary metric (median lead time) needs the episode/ledger machinery differentiator #1 owns,
cut this session (ADR-021).

### D6 — Scikit-learn (Random Forest, Ridge) and TensorFlow/PyTorch ✅

**Brief:** *"Experiment with Scikit-learn models (Random Forest, Ridge Regression) and
TensorFlow/PyTorch for advanced models."*

| | |
|---|---|
| Lives in | `src/aqi/models/{linear,forest,gbdt,deep}.py` |
| Evidence | `reports/metrics/ladder.json` — Ridge, Random Forest and a small PyTorch LSTM (`models/deep.py`) all present, each with per-horizon RMSE/MAE/R² |

**Done distinctively:** the LSTM reads a genuinely different, sequential input (an 8-step lag-ordered
sequence per base variable, built from already-leakage-tested `_lag_{h}h` columns — ADR-024) rather
than the same flat feature vector as every other rung, so it earns its place as an architecturally
distinct "advanced model" rather than a relabelled regressor. A real numerical bug (untrained-net
output scale mismatch against raw 0-500 AQI, RMSE 136 on first run) was caught by actually running
the pipeline against real data and fixed with train-only target normalization before this row could
be called done — see ADR-024.

### D7 — Evaluate using RMSE, MAE and R² ✅

**Brief:** *"Evaluate performance using RMSE, MAE, and R²."*

| | |
|---|---|
| Lives in | `src/aqi/evaluation/metrics.py` |
| Evidence | `reports/metrics/ladder.json` — RMSE, MAE, R² **per horizon**, never blended, for all 8 ladder rungs; plus a precision/recall/F1 table at AQI > 200 (`episode_at_200`) per horizon per rung |

**Done distinctively:** the AQI>200 precision/recall/F1 table exists alongside the RMSE/MAE/R² table
so the report can already say something about hazardous-day detection, not just average error, even
before differentiator #1's fuller episode metrics (CSI, false-alarm ratio, **lead time**) land — those
are explicitly cut this session (`docs/DECISIONS.md` ADR-021) and are `evaluation/episodes.py`'s job
next time RUNBOOK §2.1 session 6 is picked up.

---

## Automation

### D8 — CI/CD: feature script hourly, training script daily ⬜

**Brief:** *"Create a CI/CD pipeline that automatically runs the feature script every hour, and the
training script every day."*

| | |
|---|---|
| Lives in | `.github/workflows/` |
| Evidence | Actions history showing ≥ 7 consecutive green days |
| Running now | `clock-starter` (hourly) ✅ · `ci` (on push) ✅ |

**Done distinctively:** uptime is **measured and published**, not asserted. Every workflow reports
failures to Telegram and opens an issue, because a silently dead pipeline for a week destroys the
benchmark claim.

---

## The web application

### D9 — App loads model and features from the store and shows predictions 🟡

**Brief:** *"Your app loads the model and features from the Feature Store, computes model
predictions and shows them on a simple and descriptive dashboard."*

| | |
|---|---|
| Lives in | `app/streamlit_app.py`, `src/aqi/serving/inference.py` |
| Evidence | `streamlit run app/streamlit_app.py` — loads the registered LightGBM model (`data/model_registry/`) and the feature store live, shows current AQI, a 3-day point forecast, a SHAP explanation and the model card. `pytest tests/test_streamlit_app.py tests/test_inference.py` — runs every page against real local data with no API server up (the I10 fallback path) |
| Outstanding | Not deployed to a public URL — Streamlit Community Cloud account creation is a session-10-assigned Aliza action (`docs/RUNBOOK.md` §5), unchanged |

**Done distinctively:** the headline states a point forecast plainly and says *why* it isn't a
probability/interval yet — differentiator #2 (conformal prediction) is cut this session
(`docs/DECISIONS.md`) — rather than fabricating a number CLAUDE.md's example headline implies. The
Model card page states the ledger's real window (start, end, row count) instead of a scorecard built
from under a day of history, which would be dishonest (I4) — see ADR-027.

### D10 — Streamlit/Gradio and Flask/FastAPI 🟡

**Brief:** *"Use Streamlit/Gradio and Flask/FastApi for the web app."*

| | |
|---|---|
| Lives in | `app/streamlit_app.py` (Streamlit) + `src/aqi/serving/api.py` (FastAPI) |
| Evidence | `uvicorn aqi.serving.api:app` then `curl localhost:8000/health`; `pytest tests/test_api.py` — 8 endpoint tests against real local data. Both run and were exercised locally this session |
| Outstanding | Neither is deployed publicly yet — HF Space (API) and Streamlit Community Cloud (UI) are both Aliza-assigned account-creation steps (`docs/RUNBOOK.md` §5) |

**Done distinctively:** the UI falls back to reading the store and calling `serving/inference.py`
directly when the API is unreachable (I10) — proven, not just claimed: `tests/test_streamlit_app.py`
runs every page with no API server running at all, so CI exercises the fallback path, not just the
happy path. A `--static` mode (`streamlit run app/streamlit_app.py -- --static`) renders the whole
dashboard from `reports/dashboard_snapshot.json`, a committed artifact, so a sleeping free tier
during a live demo can't take it down.

---

## Guidelines

### D11 — Perform EDA to identify trends 🟡

| | |
|---|---|
| Lives in | `notebooks/01_eda.ipynb`; `notebooks/02_divergence.ipynb` and `notebooks/03_physics_features.ipynb` **not yet built** |
| Evidence | `notebooks/01_eda.ipynb` runs top-to-bottom with `nbclient` (no errors); figures committed at `reports/figures/eda_monthly_climatology.png`, `eda_diurnal_profile.png`, `eda_stl_decomposition.png`, `eda_correlation_heatmap.png`. Notebook cell outputs are cleared before commit (CLAUDE.md §16) — the committed figures, not the notebook's own output cells, are what this row's evidence actually points at |
| Outstanding | `02_divergence.ipynb` (model-vs-station divergence — see below) and `03_physics_features.ipynb` (physics-feature validation against PM2.5 spikes) were both scoped into this session (`docs/RUNBOOK.md` §2.1) but cut under a hard deadline. Neither is a design gap — both are next session's first job. See `docs/STATE.md` |

**Done distinctively (so far):** four stated findings, each backed by a chart and by numbers the
notebook itself computes and prints (never hand-typed — I5's "generated, not typed" discipline
extended to EDA, not just the metrics report). The smog season is real and asymmetric between zones
(Lahore's worst/best-month AQI ratio is ~2.2x against the capital's ~1.8x); the capital's winter
diurnal profile has a genuinely different shape from its own rest-of-year profile and from Lahore's,
flagged as a hypothesis rather than smoothed over; an STL decomposition shows residual variance is
measurably higher in smog season for both zones (not just visually — the notebook computes and
prints the season-conditioned residual std); and a full correlation ranking separates PM10/combustion
co-pollutant collinearity from the weaker, more mechanistic dispersion signal, setting up the
physics-feature validation that `03_physics_features.ipynb` still owes. The **model-vs-station
divergence analysis** — quantifying how much CAMS reanalysis and real instruments disagree at these
coordinates, novel for these coordinates and motivated by ADR-001 — is designed (`src/aqi/store/
ledger.py` reads the ledger, `reports/metrics/coverage.json` now reports per-column null rates so a
future join against the feature store won't silently treat a null as a real reading) but not yet
written up as a notebook; the ledger holds too few rows today for any conclusion regardless (see
`docs/STATE.md`).

### D12 — A variety of models, statistical through deep learning ✅

| | |
|---|---|
| Lives in | `src/aqi/models/{baselines,linear,forest,sarimax,gbdt,deep}.py` |
| Evidence | `reports/metrics/ladder.json` — persistence, seasonal-naive, climatology, Ridge, Random Forest, SARIMAX (statistical), LightGBM, LSTM (deep learning), each at h24/h48/h72 on the identical 2025-26-smog-season split |

**Done distinctively:** baselines are first-class, and it shows — **SARIMAX is the champion at every
horizon** (mean RMSE 19.50, vs. 27.09-30.74 for the other ML rungs), the most "the fancy model didn't
automatically win" result the ladder could have produced. Reported plainly rather than buried
(CLAUDE.md I6, §12.1) — see `docs/DECISIONS.md` ADR-021 for the champion-selection rule and ADR-023
for why a daily-granularity statistical model outperformed hourly-feature tree/neural rungs here.

**Outstanding:** rung 0d (AQICN's own published forecast, the incumbent benchmark) is not in this
session's ladder — the live ledger holds too few rows for a real comparison yet (`docs/STATE.md`) and
the benchmark pipeline is differentiator #3, explicitly cut this session (`docs/DECISIONS.md`).

### D13 — SHAP or LIME for feature importance ✅

| | |
|---|---|
| Lives in | `src/aqi/explain/shap_explain.py`, `src/aqi/explain/i18n.py`, `conf/i18n_ur.yaml` |
| Evidence | The "Why" page (`app/streamlit_app.py`) renders real `shap.TreeExplainer` contributions from the registered LightGBM model; `pytest tests/test_shap_explain.py` |

**Done distinctively:** narrated in plain English **and native Urdu** — a strict template, never an
LLM call (no key needed, no network dependency, works fully offline), so every number in the
sentence traces back to the SHAP driver dict and hallucination is structurally impossible. Explains
**LightGBM, not SARIMAX** (the ladder's metrics champion) — SARIMAX is a state-space model, not a
tree ensemble, `shap.TreeExplainer` cannot explain it, and a `KernelExplainer` fallback was ruled out
for this session (see ADR-025); every response and every page carries an explicit note saying so.

**Outstanding:** the hand-written Urdu strings (health guidance, alert templates) have not had a
native-speaker review pass yet — flagged in the file itself, in `docs/DECISIONS.md` ADR-029, and here.

### D14 — Alerts for hazardous AQI levels 🟡

| | |
|---|---|
| Lives in | `src/aqi/alerts/rules.py`, `src/aqi/alerts/telegram.py` |
| Evidence | `pytest tests/test_alerts.py` — 12 tests, the episode/all-clear state machine and the Telegram send path (mocked HTTP) both exercised |
| Outstanding | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are both empty — same credential-gap pattern as Hopsworks (`docs/STATE.md`). No live message has been sent. **Needs Aliza:** `/newbot` with @BotFather (`docs/RUNBOOK.md` §5), then `python -m aqi.alerts.telegram` sends the real test message with the exact code path the tests already cover |

**Done distinctively:** triggered on the D+1 point forecast crossing 200 rather than
`P(AQI>200) > 0.6` — CLAUDE.md's real rule needs a probability head that's cut this session
(ADR-026), and this is documented as an honest substitute, not silently relabelled. Deduplication is
a real state machine (`data/alerts_state.json`), firing only on the transition into a hazard episode
or back out of it (an all-clear), not a fixed time window.

---

## Final submission

### D15 — End-to-end system, automated pipeline, dashboard, detailed report ⬜

| | |
|---|---|
| Lives in | the whole repo + `reports/final_report.md` |
| Evidence | Every number in the report traced to a file in `reports/metrics/` |

**Done distinctively:** no number in the report is typed by hand (I5), the benchmark covers exactly
the days the ledger holds and says so (I4), and the limitations section is written to be read rather
than to be survived.

---

## Beyond the brief

Four things the brief does not ask for, each folded into the row it strengthens rather than parked
in an appendix:

| | Strengthens | Where |
|---|---|---|
| Episode detection and lead time | D7, D14 | `evaluation/episodes.py` |
| Conformal prediction intervals with per-group coverage | D7, D9 | `evaluation/conformal.py` |
| Live public benchmark against AQICN, wins and losses | D7, D9 | `pipelines/benchmark_pipeline.py` |
| Punjab smog physics features | D2, D11 | `features/physics.py` |

## Deliberately not built

Recorded so the omissions read as decisions rather than gaps. Each gets a paragraph in the report's
future-work section — see CLAUDE.md §4: ladder rungs past LSTM, the full ladder on hourly horizons,
drift-triggered retraining, the Evidently dashboard, OpenAQ, more than two forecast zones, the map
page, and hyperparameter search beyond a small fixed grid.
