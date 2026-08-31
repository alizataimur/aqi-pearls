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

### D5 — Feature Store → train and evaluate → Model Registry ⬜

**Brief:** *"Fetches historical (features, targets) from the Feature Store. Trains and evaluates the
best ML model possible. Stores the trained model in the Model Registry."*

| | |
|---|---|
| Lives in | `pipelines/training_pipeline.py`, `src/aqi/models/registry.py` |
| Evidence | Registered champion carrying training window, feature-group version, git SHA and per-horizon metrics |

**Done distinctively:** promotion is **gated and logged** — a challenger replaces the champion only
if it improves the primary metric without regressing hazardous-event recall. A model without its
metrics attached cannot be promoted at all.

### D6 — Scikit-learn (Random Forest, Ridge) and TensorFlow/PyTorch ⬜

**Brief:** *"Experiment with Scikit-learn models (Random Forest, Ridge Regression) and
TensorFlow/PyTorch for advanced models."*

| | |
|---|---|
| Lives in | `src/aqi/models/` |
| Evidence | The ladder table in `reports/metrics/ladder.json`, rendered in the report |

**Done distinctively:** every rung evaluated on one identical window against four baselines
including AQICN's own published forecast — the incumbent a citizen would otherwise use.

### D7 — Evaluate using RMSE, MAE and R² ⬜

**Brief:** *"Evaluate performance using RMSE, MAE, and R²."*

| | |
|---|---|
| Lives in | `evaluation/metrics.py`, `evaluation/episodes.py` |
| Evidence | `reports/metrics/ladder.json` — per horizon, never blended |

**Done distinctively:** reported **per horizon**, plus episode metrics — precision, recall, critical
success index, false-alarm ratio and **lead time** on AQI > 200 days. Average error is dominated by
ordinary days when AQI is 80 and the model guesses 82; nobody in Rawalpindi is asking that question.
Lead time is the project's primary metric because it answers whether a warning arrived in time to
act on.

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

### D9 — App loads model and features from the store and shows predictions ⬜

**Brief:** *"Your app loads the model and features from the Feature Store, computes model
predictions and shows them on a simple and descriptive dashboard."*

| | |
|---|---|
| Lives in | `app/streamlit_app.py` |
| Evidence | Public URL, openable in a private window |

**Done distinctively:** the headline is a probability and an interval — *"72% chance AQI exceeds 200
on Friday"* — never a bare number. A live scorecard shows performance against AQICN **including the
days we lose**.

### D10 — Streamlit/Gradio and Flask/FastAPI ⬜

**Brief:** *"Use Streamlit/Gradio and Flask/FastApi for the web app."*

| | |
|---|---|
| Lives in | `app/` (Streamlit) + `src/aqi/serving/api.py` (FastAPI) |
| Evidence | Both deployed; the dashboard's network calls hit the API |

**Done distinctively:** the UI falls back to reading the store directly when the API is down (I10),
and a `--static` mode renders the whole dashboard from committed JSON so a demo cannot be killed by
a sleeping free tier.

---

## Guidelines

### D11 — Perform EDA to identify trends ⬜

| | |
|---|---|
| Lives in | `notebooks/01_eda.ipynb`, `notebooks/02_divergence.ipynb` |
| Evidence | Rendered notebooks, referenced in the report |

**Done distinctively:** includes a **model-vs-station divergence analysis** — quantifying how much
CAMS reanalysis and real instruments disagree at these coordinates. Not published for
Rawalpindi/Islamabad before, and it exists because the project measures the disagreement rather than
pretending it away (ADR-001).

### D12 — A variety of models, statistical through deep learning ⬜

| | |
|---|---|
| Lives in | `src/aqi/models/` |
| Evidence | The ladder: persistence, seasonal-naive, climatology, AQICN, Ridge, Random Forest, SARIMAX, LightGBM, LSTM |

**Done distinctively:** baselines are first-class. If persistence wins at some horizon, that is the
published result — a ladder where the simple model sometimes wins reads as mature, and one where
the fancy model always wins reads as leaky.

### D13 — SHAP or LIME for feature importance ⬜

| | |
|---|---|
| Lives in | `explain/shap_explain.py`, `explain/briefing.py` |
| Evidence | The "Why" page rendering real SHAP contributions |

**Done distinctively:** narrated in plain English **and Urdu**. SHAP values become a structured
dict, the dict fills a strict template, and the LLM only rephrases — so every number comes from the
model and hallucination is structurally impossible. Works with no LLM key at all.

### D14 — Alerts for hazardous AQI levels ⬜

| | |
|---|---|
| Lives in | `src/aqi/alerts/` |
| Evidence | A received Telegram message |

**Done distinctively:** triggered on `P(AQI>200) > 0.6` rather than a point forecast crossing 200 —
the uncertainty thesis applied to a decision someone actually makes.

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
