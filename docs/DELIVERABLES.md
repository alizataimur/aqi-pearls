# DELIVERABLES — the brief, mapped to the repo

Every requirement in `AQI_predict1.pdf`, where it lives, and how to verify it. This is the
submission checklist and the marker's map; `reports/final_report.md` is the narrative, and the
README is the thirty-second pitch. Three documents, three readers.

**Live URLs**

| What | Where |
|---|---|
| Dashboard | <https://aqi-pearls-predictor.streamlit.app/> — live, on the I10 direct-store fallback path (no API deployed yet, see D10) |
| API | _not yet deployed_ — no HF Space exists; runs and is tested locally (`uvicorn aqi.serving.api:app`) |
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
before differentiator #1's fuller episode metrics (CSI, false-alarm ratio, **lead time**) land.
`evaluation/episodes.py` does not exist — CLAUDE.md §12.3's actual primary metric (median lead time)
and the rest of §12.4's episode metrics are cut, not deferred-and-forgotten (`docs/DECISIONS.md`
ADR-021); RUNBOOK §2.1's session-6 scope (episodes + conformal) has not been picked up as of this
session either — session 6 built serving/dashboard/explanations/alerts instead.

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

### D9 — App loads model and features from the store and shows predictions ✅

**Brief:** *"Your app loads the model and features from the Feature Store, computes model
predictions and shows them on a simple and descriptive dashboard."*

| | |
|---|---|
| Lives in | `app/streamlit_app.py`, `src/aqi/serving/inference.py` |
| Evidence | **Live:** <https://aqi-pearls-predictor.streamlit.app/> — open it in a private window; loads the registered LightGBM model (`data/model_registry/`) and the feature store, shows current AQI, a 3-day point forecast, a SHAP explanation and the model card, on the **live model path** (not the fallback banner — verified via a fresh `git clone`, see below). `pytest tests/test_streamlit_app.py tests/test_inference.py tests/test_deploy_assets.py` — runs every page against real local data with no API server up (the I10 fallback path), plus asserts every startup-critical file is tracked, path-portable, and covers enough lookback |
| Post-deploy fixes | The first live deploy hit three Cloud-only bugs, all fixed same-session — see `docs/STATE.md`'s "Post-session-6 incidents" and `docs/DECISIONS.md` ADR-030/ADR-031: (1) `ModuleNotFoundError: aqi` (`sys.path` fix); (2) `FileNotFoundError` on the model registry, not tracked by git (force-added as a deadline expedient); (3) still `FileNotFoundError` after (2) — `metadata.json`'s `artifact_path` was an absolute, machine-specific path that resolved to nothing on any other checkout. Root-caused by cloning the repo fresh (not copying the working tree) and reading the real exception, per instruction — not by inspecting source and guessing. `LocalModelRegistry.resolve_artifact_path()` now reconstructs a portable path from just the filename; `load_serving_model`/`get_forecast`/`get_explain` degrade to the static snapshot with a visible banner (I10) if an artifact is genuinely missing or unloadable |

**Done distinctively:** the headline states a point forecast plainly and says *why* it isn't a
probability/interval yet — differentiator #2 (conformal prediction) is cut this session
(`docs/DECISIONS.md`) — rather than fabricating a number CLAUDE.md's example headline implies. The
Model card page states the ledger's real window (start, end, row count) instead of a scorecard built
from under a day of history, which would be dishonest (I4) — see ADR-027.

### D10 — Streamlit/Gradio and Flask/FastAPI 🟡

**Brief:** *"Use Streamlit/Gradio and Flask/FastApi for the web app."* — read literally against the
architecture doc's intent (§10, §14), this means *both frameworks deployed, and the UI calls the
API*, not merely that both exist in the repo.

| | |
|---|---|
| Lives in | `app/streamlit_app.py` (Streamlit) + `src/aqi/serving/api.py` (FastAPI) |
| Evidence | Streamlit: **live** at <https://aqi-pearls-predictor.streamlit.app/>. FastAPI: `uvicorn aqi.serving.api:app` then `curl localhost:8000/health` — runs locally; `pytest tests/test_api.py` (8 tests) passes against real local data. Both frameworks are exercised and tested; only one is deployed |
| Missing for ✅ | **The FastAPI service is not deployed anywhere reachable.** No HF Space (or any other host) exists for it — checked `requirements.txt` (scoped to the Streamlit process alone; no `fastapi`/`uvicorn` in it, confirming no co-located API process), `docs/RUNBOOK.md` §5 (HF Space creation still listed as a pending Aliza action, not done), and the repo/docs for any deployment URL (none found). **Consequently the deployed Streamlit app does not call an API** — confirmed by reading `app/streamlit_app.py`, not assumed: `API_URL = os.environ.get("AQI_API_URL", "http://localhost:8000")` (line 47), and no `AQI_API_URL` is set anywhere in this repo or its deploy config; on Streamlit Cloud there is no process listening on `localhost:8000`, so every `_api_get()` call fails and every page runs on the I10 direct-store/registry fallback (`get_current`/`get_forecast`/`get_explain`/`get_metrics` all fall through past the failed `_api_get`). The live dashboard today is Streamlit-reads-store-directly, not Streamlit-calls-FastAPI |

**Done distinctively:** the UI falls back to reading the store and calling `serving/inference.py`
directly when the API is unreachable (I10) — proven, not just claimed: `tests/test_streamlit_app.py`
runs every page with no API server running at all, so CI exercises the fallback path, not just the
happy path. That fallback path is, today, also the *only* path the live deployment ever takes — an
honest reason to keep this row 🟡 rather than a defect in the fallback itself. A `--static` mode
(`streamlit run app/streamlit_app.py -- --static`) renders the whole dashboard from
`reports/dashboard_snapshot.json`, a committed artifact, so a sleeping free tier during a live demo
can't take it down.

**Also cut from the brief's D9/D10 vision, and shipped as a substitute (CLAUDE.md §14):** the 3-day
forecast page's headline is a plain point forecast, not *"72% chance AQI exceeds 200 on Friday"* —
that number needs the conformal-prediction layer (differentiator #2), cut this session (ADR-021).
The Scorecard page (CLAUDE.md's original 5th page) is not built at all, replaced by the Model card's
ledger-window statement (ADR-027); four pages ship, not five.

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
| Lives in | `src/aqi/alerts/{rules,notifier,email_sender,telegram}.py`, `.github/workflows/alerts.yml` |
| Evidence | `pytest tests/test_alerts.py` — 20 tests: the episode/all-clear state machine, both `Notifier` send paths (mocked SMTP/HTTP), and `ALERT_CHANNEL` selection. **To send one real live alert to yourself:** set `ALERT_EMAIL_HOST=smtp.gmail.com`, `ALERT_EMAIL_PORT=587`, `ALERT_EMAIL_USER`/`ALERT_EMAIL_PASSWORD` (a Gmail App Password) and `ALERT_EMAIL_TO` in `.env`, then run `python -m aqi.alerts.email_sender` |
| Outstanding | No live email has been sent from this session (no real Gmail App Password available here) — the send path is exercised only against a mocked `smtplib.SMTP`. **Needs Aliza:** an App Password (<https://myaccount.google.com/apppasswords>) and the four `ALERT_EMAIL_*` values, locally in `.env` and as GitHub Secrets for `alerts.yml`. Telegram stays supported (`ALERT_CHANNEL=telegram`) but is not the default — see below |

**The trigger CLAUDE.md §14 specifies — `P(AQI>200) > 0.6` — was the intended design and it was cut,
alongside conformal prediction, under deadline pressure (differentiator #2; `docs/DECISIONS.md`
ADR-021).** No probability of any kind is computed anywhere in this codebase today. What ships
instead: `alerts/rules.py::evaluate()` fires on the **D+1 point forecast crossing 200** — a plain
LightGBM number, not a probability, not a confidence, not an approximation of one — documented as a
substitute, not silently relabelled (ADR-026). The message says "forecast daily max AQI of {aqi},"
never "chance" or "probability" (checked directly against CLAUDE.md §20's "letting the model state
numbers" anti-pattern).

**Done distinctively:** email ships as the default channel because **Telegram is blocked in Pakistan
by the PTA** — a product finding from the user, not from testing, recorded as such
(`docs/DECISIONS.md` ADR-032). The alert *rule* is now structurally channel-agnostic, not just
incidentally so: a `Notifier` Protocol (`alerts/notifier.py`) separates "what triggers an alert and
what it says" (`evaluate()`, `format_message()` — untouched by this refactor) from "how it's
delivered" (`EmailNotifier`/`TelegramNotifier`, selected by `ALERT_CHANNEL`). Deduplication is a
real state machine (`data/alerts_state.json`, committed after every `alerts.yml` run so it survives
Actions' ephemeral runners), firing only on the transition into a hazard episode or back out of it.

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

Four things the brief does not ask for. **Three of the four are still cut, not delivered — see
`docs/DECISIONS.md`, ADR-021 (episodes + conformal, cut with the deadline pressure) and the
differentiator #3 note. I5/I4 apply to this table's own claims, same as to any number in the
report: a "strengthens" column naming a file that does not exist would be exactly the kind of
prose CLAUDE.md I5 exists to prevent.**

| | Strengthens | Status | Where |
|---|---|---|---|
| Episode detection and lead time | D7, D14 | **Not built.** A simple precision/recall/F1 table at AQI>200 shipped instead (D7); the fuller CSI/false-alarm-ratio/lead-time metrics this row describes do not exist | `evaluation/episodes.py` — file does not exist |
| Conformal prediction intervals with per-group coverage | D7, D9 | **Not built.** No interval, band or probability is computed anywhere in this codebase; D9's 3-day forecast page is a plain point forecast | `evaluation/conformal.py` — file does not exist |
| Live public benchmark against AQICN, wins and losses | D7, D9 | **Not built.** The ledger holds under a day of history; a comparison built from that would violate I4. D9's Model card states the ledger's real window instead (ADR-027) | `pipelines/benchmark_pipeline.py` — file does not exist |
| Punjab smog physics features | D2, D11 | **Delivered.** 7 physics features, built and unit-tested (D2); correlation-against-PM2.5-spikes validation (`03_physics_features.ipynb`) is still outstanding (D11) | `src/aqi/features/physics.py` |

## Deliberately not built

Recorded so the omissions read as decisions rather than gaps. Each gets a paragraph in the report's
future-work section — see CLAUDE.md §4: ladder rungs past LSTM, the full ladder on hourly horizons,
drift-triggered retraining, the Evidently dashboard, OpenAQ, more than two forecast zones, the map
page, and hyperparameter search beyond a small fixed grid.
