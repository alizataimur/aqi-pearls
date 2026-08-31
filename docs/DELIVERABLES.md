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
| Lives in | `src/aqi/sources/` — `open_meteo_air.py`, `open_meteo_weather.py`, `open_meteo_hist_forecast.py`, `aqicn.py` |
| Evidence | `pytest tests/test_open_meteo_sources.py tests/test_aqicn_station.py` · captured contracts in `docs/schemas/` |
| Outstanding | `pipelines/feature_pipeline.py` and the hourly workflow — session 3, once a store exists (ADR-010) |

**Done distinctively:** four sources rather than one, including the Open-Meteo **historical-forecast
archive** — the source that makes leakage-safe future covariates possible at all (CLAUDE.md I1).
Station identifiers are pinned and every capture is distance-verified after the nearest-station
lookup silently returned a Delhi station for three Pakistani cities (ADR-007).

### D2 — Compute features and targets, including time-based and derived ⬜

**Brief:** *"Computes features from this raw data (aka model inputs), and targets (aka model
outputs). Include time-based features (hour, day, month) and derived features like AQI change
rate."*

| | |
|---|---|
| Lives in | `src/aqi/features/`, `conf/features.yaml` |
| Evidence | `pytest tests/test_features.py tests/test_no_leakage.py` · `docs/feature_spec.md` |

**Done distinctively:** region-specific Punjab smog physics — inversion proxy, stagnation index,
ventilation index, crop-burning window, festival calendar — each validated against PM2.5 spikes in
`notebooks/03_physics_features.ipynb`, with the ones that show nothing reported as tried-and-rejected.
Every feature declares a `min_lag` and the builder asserts it mechanically, so I1 is enforced by
code rather than by care.

### D3 — Store features in a Feature Store ⬜

**Brief:** *"Stores these features in the Feature store. You may want to explore Hopsworks or Vertex
AI (Free tiers)."*

| | |
|---|---|
| Lives in | `src/aqi/store/hopsworks_store.py`, `src/aqi/store/parquet_store.py` |
| Evidence | Populated feature group; row count recorded in `docs/STATE.md`; `pytest tests/test_store_parity.py` |

**Done distinctively:** two backends behind one `Protocol`, passing an identical test suite. The
fallback is not a nice-to-have — it is what keeps the demo alive when a free tier goes down, and it
is itself an engineering-judgment result worth reporting.

### D4 — Backfill historical (features, targets) over past dates ⬜

**Brief:** *"Run the feature script from step 1 for a range of past dates, to generate training data
for your ML models."*

| | |
|---|---|
| Lives in | `pipelines/backfill.py` |
| Evidence | `reports/metrics/coverage.json` — first date, last date, and every gap |

**Done distinctively:** chunked and resumable per city-month with a manifest, so a multi-year pull
that dies at 80% resumes rather than restarts. Backfill runs to the **probed** CAMS floor of
2022-08-04, not an assumed date.

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
