# DECISIONS — ADR log

What was chosen, what was rejected, why. One entry per real choice, written the
day it is made (CLAUDE.md §19). The report's decisions-and-tradeoffs narrative
assembles from here, so writing these as you go is what makes Stage 5 cheap.

---

## ADR-001 — Train on CAMS reanalysis, benchmark against AQICN stations

**Status:** accepted · 2026-07-31

No free dataset offers real measurements with deep history at these
coordinates. Two imperfect options: train on ground stations with sparse, short
history, or train on model reanalysis with long, gap-free history.

**Chosen:** train on Open-Meteo/CAMS reanalysis; use AQICN stations as ground
truth *and* as the incumbent benchmark; and **measure the disagreement between
them** rather than pretending it away.

**Rejected:** training on AQICN history (too short, too gappy for a 3-year
backfill); treating CAMS as ground truth (it is a model, not an instrument).

**Consequence:** the model-vs-station divergence analysis becomes a genuine
contribution rather than an apology. The report must state plainly that labels
are reanalysis, not measurement.

---

## ADR-002 — The clock starter runs hourly, not daily

**Status:** accepted · 2026-08-27
**Supersedes:** the daily cadence in CLAUDE.md §13's first draft.

AQICN publishes no free observation-history endpoint. A once-a-day snapshot
therefore cannot reconstruct a daily **maximum** AQI — and daily max is the
primary target (§9.1) and the quantity the whole episode/alert/benchmark story
rests on.

**Chosen:** hourly capture of the station observation, with the published
forecast block captured on the same schedule and deduplicated by content hash.

**Rejected:** daily capture (loses the intraday peak permanently); reconstructing
daily max from CAMS instead (that is the thing being evaluated, so it cannot
also be the ground truth).

**Consequence:** ~24 small JSONL appends per city per day, committed to the
repo. Cheap. The forecast dedupe keeps the AQICN ledger to roughly one row per
city per day despite the hourly cadence.

---

## ADR-003 — Do not clip AQI at 500

**Status:** accepted · 2026-08-27

EPA's AQS breakpoint table defines a band above the published 0–500 scale:
325.5 µg/m³ and above maps to AQI 501–999. Most implementations clip at 500.

**Chosen:** implement the 501–999 band and expose `AQIResult.exceeds_scale`.

**Rejected:** clipping at 500 to match common practice.

**Consequence:** Punjab smog episodes routinely exceed the published scale.
Clipping would compress exactly the regime this project exists to forecast into
a constant, destroying variance in the target during episodes — the opposite of
the goal. Dashboard copy must handle "beyond the scale" as a category rather
than printing 500.

---

## ADR-004 — EPA 2024 AQI revision, recorded explicitly

**Status:** accepted · 2026-08-27

PM2.5 breakpoints were revised in 2024 (Good now ends at 9.0 µg/m³, not 12.0).
Providers have not all migrated, which produces a systematic offset between our
AQI, Open-Meteo's `us_aqi`, and AQICN's value.

**Chosen:** the 2024 table, transcribed from the authoritative AQS code table
(`aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.html`) on 2026-08-27,
with a regression test asserting 12.0 µg/m³ → AQI 56 so nobody can quietly
reinstate the old table.

**Consequence:** every AQICN comparison must separate a *forecast* difference
from a *conversion* difference (CLAUDE.md I8). The report states the revision.

---

## ADR-005 — Gas sub-indices are converted from µg/m³ to ppm/ppb

**Status:** accepted · 2026-08-27

CAMS reports all pollutants in µg/m³. EPA defines the O₃, NO₂, SO₂ and CO
sub-indices in ppm/ppb. Applying the index directly to µg/m³ is a common and
invisible bug.

**Chosen:** convert at 25 °C / 1 atm in one place (`ugm3_to_epa_units`), with a
test asserting the converted and unconverted indices differ.

**Consequence:** PM2.5 dominates in this region anyway, so this mostly protects
the overall-AQI maximum from a wrong gas sub-index occasionally winning.

---

## ADR-006 — Commit AQICN raw payloads; keep Open-Meteo raw local

**Status:** accepted · 2026-08-27

`CLAUDE.md` §8.3 caches raw responses to `data/raw/` so a transform bug never
requires re-hitting the API. That reasoning is sound locally but breaks in CI:
every workflow run is a fresh container, so an uncommitted cache evaporates the
moment the job ends.

The two sources are not alike. AQICN has no history endpoint — if the extractor
has a bug, the raw payload is the only way to recover the reading, and there is
no second chance to fetch it. Open-Meteo's archive can be re-queried for any
past date at any time.

**Chosen:** commit `data/raw/aqicn/**`; gitignore `data/raw/open_meteo/`.

**Rejected:** committing all raw responses (backfill payloads would add hundreds
of megabytes for data that is re-fetchable on demand); committing none (loses
irreplaceable AQICN readings on every CI run).

**Consequence:** roughly three small JSONL files per day. Trivial in size, and it
means a bug in `extract_observation` is repairable months later by replaying the
raw captures rather than being a permanent hole in the ledger.

---

## ADR-007 — Pin AQICN stations by index; never use geo: lookup

**Status:** accepted · 2026-08-27
**Severity:** would have silently invalidated the entire benchmark.

The Stage 0 dry run returned the same station — *Pooth Khurd, Bawana, Delhi,
India* at (28.78, 77.05) — for Islamabad, Rawalpindi **and** Lahore, with
`status: "ok"`, a plausible AQI, and an identical forecast hash. The `geo:`
endpoint returns the same result as `here`, i.e. AQICN geolocates the caller's
IP and ignores the supplied coordinates. Percent-encoding the semicolon changes
nothing.

Named lookups work correctly and expose stable indices:
`islamabad` → Islamabad US Embassy, idx **11739**; `lahore` → Lahore US
Embassy, idx **11765**.

**Chosen:** pin `@<idx>` per city in `conf/cities.yaml`; `fetch_feed` raises on
any `geo:` argument; every capture calls `verify_station`, which rejects a
station more than 60 km from the configured coordinates before anything is
written to the ledger.

**Rejected:** keeping `geo:` with a wider retry (the coordinates are ignored, so
retrying cannot help); accepting the Delhi station on the argument that Indian
and Pakistani Punjab share environmental drivers — the same probe recorded
Delhi at AQI 183 while Lahore was 34, a factor of five apart in the same hour,
and Islamabad sits on the Potohar plateau outside the Indo-Gangetic basin
entirely. Correlated in smog season is not substitutable hour by hour.

**Consequence:** the wrong-instrument failure is now loud rather than silent.
This is the class of bug I3 and I4 exist to prevent: `status: "ok"` plus a
believable number, no history endpoint to repair it from, and by the time
anyone noticed, weeks of rows labelled `islamabad` describing another country.
Rawalpindi has no pinned station yet and is **skipped**, not given Islamabad's
instrument — one measurement must not be written as two series.

---

## ADR-008 — Islamabad and Rawalpindi are one forecast zone

**Status:** accepted · 2026-08-27
**Settles:** the open question flagged in CLAUDE.md §8.2.

Open-Meteo serves CAMS on a 0.1° output grid and returns distinct coordinates
for the twin cities — 33.700005 for Islamabad, 33.6 for Rawalpindi. That looks
like two resolved locations. It is not: the returned PM2.5 arrays are
**byte-identical**. CAMS global's native ~0.4° resolution cannot separate
cities 13 km apart, and the finer output grid is interpolation, not
information.

**Chosen:** two forecast zones — `capital` (Islamabad + Rawalpindi) and
`lahore`. Model the zone once.

**Rejected:** three independently modelled cities. The coordinates would have
supported the claim; the data does not.

**Consequence:** exactly the two genuinely distinct zones CLAUDE.md §4 called
for, now with proof rather than an assumption. It also sharpens the
model-vs-station divergence analysis: two real instruments inside one model
grid cell quantify how much within-zone variation the model structurally
cannot see — which is a finding worth reporting for Pakistan, where station
density is low and this has not been published.

---

## ADR-012 — The stdlib config parser must match PyYAML exactly

**Status:** accepted · 2026-08-31
**Severity:** 16 consecutive failed scheduled runs, ~90 hours of permanent
ledger gaps. I3 actively violated for the whole window.

`scripts/clock_starter.py` reads `conf/cities.yaml` without requiring PyYAML so
a dependency problem can never block a capture (CLAUDE.md §6). The
`clock-starter` workflow installs nothing, so **CI took the fallback path while
the laptop took the PyYAML path** — two code paths reading one file.

They disagreed. The fallback did not strip quotes from scalars, so
`aqicn_station: "@11739"` parsed as `'"@11739"'`, quote characters included.
That produced `https://api.waqi.info/feed/"@11739"/?token=...`, every city
failed, `captured` was empty and the run exited 1. Locally everything passed,
which is why the investigation went hunting for a missing `AQICN_TOKEN` secret
that was never missing.

**Chosen:** fix `_parse_scalar` to handle quoted strings, booleans, nulls,
ints, floats and inline lists as PyYAML does; strip comments only at a `#`
preceded by whitespace or line start; and add `tests/test_yaml_fallback.py`
asserting the two loaders return **identical structures**. Also reject a
malformed station identifier in `fetch_feed` so the next such bug names itself
instead of returning a 404.

**Rejected:** installing PyYAML in the workflow. It would fix the symptom by
deleting the fallback path from CI, leaving it untested and re-introducing the
dependency risk §6 exists to avoid. The two paths must agree, not be reduced to
one.

**Consequence:** a parity check already existed and passed — it compared `lat`
and nothing else. A partial parity test is worse than none, because it
advertises a guarantee it does not provide. The new test compares whole
structures. The general lesson, worth a line in the report: when two code paths
read the same input, the test that matters is the one asserting they agree, and
it has to compare everything.

---

## ADR-013 — Store and backfill operate per forecast zone, not per named city

**Status:** accepted · 2026-08-31

`conf/cities.yaml` lists three named cities; ADR-008 already established that
Islamabad and Rawalpindi return **byte-identical** CAMS pollutant series (one
~0.4° grid cell, two names). Session 3 has to decide what `city_id` means in
the feature store and the backfill manifest, and the literal reading —
backfill and store all three names — was never actually decided anywhere.

**Chosen:** the feature store, the backfill manifest and `feature_pipeline.py`
all key on **zone_id** (`capital`, `lahore`), not on the three names in
`cities.yaml`. `capital` uses Islamabad's coordinates (it has the pinned AQICN
station and is the zone's CAMS grid reference; Rawalpindi's grid cell is
provably the same series). `conf/cities.yaml` is unchanged and still names all
three — it remains the source of truth for AQICN capture (clock-starter) and
for display (the dashboard shows "Islamabad / Rawalpindi" as one forecast with
two station readings). `src/aqi/config.py`'s `zones()` derives the two zones
from it.

**Rejected:** backfilling and storing Islamabad and Rawalpindi as two
independent feature-store series. It would silently double every CAMS API call
and every stored row for zero informational gain — the two series are
identical by construction — and would let a future session accidentally train
two "different" city models on the same underlying data, which is a worse bug
than the one ADR-008 already found once.

**Consequence:** the feature store contains two `city_id` values (`capital`,
`lahore`), not three. The model ladder (session 5) trains one model per zone.
The dashboard (session 10) must present `capital`'s forecast under both city
names with the divergence caveat from ADR-008, rather than implying two
independently-modelled cities. Worth a explicit line in the report's data
section — it is the same honesty move as ADR-008 itself, carried through to
the store.

---

## ADR-014 — Parquet fallback commits to the repo, not an HF Dataset, until `HF_TOKEN` exists

**Status:** accepted · 2026-08-31

CLAUDE.md §11.1 specifies the Parquet fallback lives on a Hugging Face Dataset
repo in CI (writable with a token) and as plain local Parquet in dev. Session
3 needs the fallback to actually work in CI today, and `.env` currently has
`HOPSWORKS_API_KEY`, `HOPSWORKS_PROJECT` and `HF_TOKEN` all empty — none of the
three accounts RUNBOOK §5 assigns to Aliza for session 3 ("create the Hopsworks
project and the HF dataset repo") have been created yet. That is a credential
block, not a design question, so per the session prompt's own escape hatch the
reasonable default is chosen and recorded here rather than the whole session
stopping to wait for it.

**Chosen:** `ParquetFeatureStore`'s default root is `data/feature_store/` inside
the repo, partitioned `city=/year=/month=/data.parquet` exactly as §11.1
specifies, written by the same GitHub Actions identity that already commits
`data/ledger/` (`clock-starter[bot]`-style commit, rebase-and-retry on push
race). `ParquetFeatureStore` takes the root as a constructor argument, so
pointing it at an HF Dataset repo checkout later is a one-line change in
`feature_pipeline.py`/`backfill.py`, not a rewrite.

**Rejected:** blocking session 3 on Aliza creating the HF account first (the
credential-block escape hatch exists for exactly this, but a repo-committed
Parquet store is a strictly *more* useful interim state than an empty one —
the dashboard's `--static` mode and the whole D4 backfill can run against it
today); requiring `HF_TOKEN` and failing the workflow if it's absent (that
would silently stop D1's hourly workflow the moment this session ends, which
is the same class of mistake session 0 exists to prevent).

**Consequence:** `data/feature_store/**` will grow the repo — acceptable at the
scale of two zones' hourly features (nowhere near AQICN's raw-payload
volume this isn't). If Aliza supplies `HF_TOKEN` and creates the dataset repo,
switching the root to a synced checkout of it is the only change needed;
nothing about `ParquetFeatureStore`'s interface changes. Flagged to Aliza in
this session's five-line report as a "needs me" item, same as Hopsworks.

---

## ADR-015 — Rolling windows require a full window; BLH-derived features get explicit `_is_missing` flags

**Status:** accepted · 2026-08-31 (session 4)

`builder.py`'s `_add_lag_rolling`, its `pm25_pm10_ratio_roll_24h`, and
`physics.py`'s `stagnation_index` all called `.rolling(window, min_periods=1)`.
`boundary_layer_height` has a confirmed source gap — **2024-01-01 through
2024-06-30, both zones, 4,368 hours, identical in both** (checked directly
against the stored data, not assumed) — and a second, independent gap in the
historical-forecast archive's BLH series before 2024-08-29/30/31 depending on
horizon. With `min_periods=1`, a 72h window straddling the observed gap could
be computed from a single real point and still come out looking like a
genuine 72-hour average.

**Chosen:** `min_periods=window` (pandas' own default, made explicit rather
than left implicit) everywhere a rolling stat is computed on hourly data.
Verified the fix's actual effect against the real store: for the 24h window,
46 additional hours go from a partial-window value to `NaN` (23 at the very
start of the 4-year history, where no full window can exist yet regardless of
any gap, plus 23 trailing the BLH gap, where the window still reaches back
into it); for the 72h window the same arithmetic gives 71 + 71 = **142**
additional hours — the number this session's brief flagged before any code
was touched, now confirmed rather than assumed. Also added
`boundary_layer_height_is_missing`, `stagnation_index_is_missing`, and
`ventilation_index_is_missing` (declared in `conf/features.yaml`'s `physics`
list) so a tree model sees an explicit "dispersion unknown" signal instead of
just a `NaN` that a later imputation step (required for Ridge/SARIMAX/LSTM,
none of which take `NaN` directly) could silently turn into a value
indistinguishable from a real measurement.

**Rejected:** leaving `min_periods=1` and relying on downstream models to
notice a value was suspect (nothing downstream had any way to know); a single
blanket `dispersion_unknown` flag covering all three BLH-derived quantities
(collapses information a tree could use — `ventilation_index` and
`boundary_layer_height` go missing on the same 4,368 hours, but
`stagnation_index` goes missing on 4,414 because its own 24h window
straddles the gap edges too, and that distinction is itself informative);
imputing BLH from a regional average or interpolation (CLAUDE.md I10 is about
external dependencies degrading gracefully, not about inventing physical
measurements that were never made).

**Consequence:** the committed feature store (`data/feature_store/`) was
regenerated in place — `scripts/rebuild_derived_features.py` recomputes only
the affected columns from the base hourly series already in the store,
without re-fetching Open-Meteo, and rewrites via the store's normal
idempotent upsert. Row counts are unchanged (35,808 rows/zone); the feature
count for D2's evidence rises from 235 to 238 (`docs/feature_spec.md`
updated). `tests/test_features.py`'s
`test_stagnation_index_highest_when_still_humid_capped` had to grow its
fixture from 1 row to 24 constant rows — a single-row frame is now, correctly,
the missing-window case rather than a valid stagnation reading.

---

## ADR-016 — Held-out test window is the 2025-26 smog season

**Status:** accepted · 2026-08-31 (session 4)

CLAUDE.md I2 requires the final held-out test period to contain a complete
smog season (Oct-Feb). `boundary_layer_height` — the input `stagnation_index`
and `ventilation_index` depend on — has two independent gaps (ADR-015): the
observed series is null 2024-01 to 2024-06, and the historical-forecast
series is null before late August 2024. A smog season is only usable for
evaluating the full feature set, physics features included, if both series
are fully populated across it.

Checked directly against the rebuilt store: 2022-Oct-2023-Feb has no
forecast-BLH at all (predates the archive's coverage here); 2023-Oct-2024-Feb
loses January-February to the observed-BLH gap; 2024-Oct-2025-Feb is the
first fully-populated season on both series, but it is not the *most recent*
complete one — 2025-Oct-2026-Feb also has both series fully populated and the
backfill runs through 2026-08-29, past the end of that season.

**Chosen:** the 2025-26 smog season (Oct 2025 - Feb 2026) is the final
held-out test window for the model ladder (session 5). It is both the most
recent complete season and free of the BLH gap on either series, so no
physics feature the ladder is evaluated on is silently degraded across the
window that decides the primary metric (§12.3).

**Rejected:** 2024-25 as the held-out window — it satisfies I2's completeness
requirement too, but using it as the *final* test period would leave a full
additional complete season (2025-26) sitting unused after it, which is a
worse walk-forward setup than simply using the more recent one.

**Consequence:** session 5's `evaluation/splits.py` walk-forward folds must
end with 2025-26 as the final test chunk. Documented here so the ladder
doesn't have to re-derive this from the raw BLH gap data a second time.

---

## ADR-017 — Session 4 cut short: `01_eda.ipynb` shipped, `02_divergence.ipynb`
## and `03_physics_features.ipynb` deferred to next session

**Status:** accepted · 2026-09-01 (session 4)

Session 4's brief (`docs/RUNBOOK.md` §2.1) scoped three notebooks plus the
`coverage.json` null-rate fix. Partway through — `coverage.json` and
`src/aqi/store/ledger.py` done, `01_eda.ipynb` written and executed,
`02_divergence.ipynb`/`03_physics_features.ipynb` not yet started — an
explicit instruction arrived: finish `01_eda.ipynb` only, update
`STATE.md`/`DELIVERABLES.md`, commit, and stop, because the real-world
deadline is tomorrow.

**Chosen:** ship what was finished (`coverage.json` null rates,
`ledger.py`, `01_eda.ipynb` with its four committed figures), explicitly mark
D11 🟡 rather than pretending the notebook trio is complete, and hand off the
remaining two notebooks as next session's *first* job rather than folding a
rushed, thin version of them into this commit.

**Rejected:** writing thin/placeholder versions of `02_divergence.ipynb` and
`03_physics_features.ipynb` just to have three files present. A placeholder
notebook is worse than an honestly absent one — it would either draw a
conclusion from the ledger's 6 rows (exactly what the session brief said not
to do) or fake a physics-feature validation that never actually ran the
spike-conditioned correlation. CLAUDE.md's prime directive (§1) is evidence
over quantity; a fake D11 sub-item scores worse under audit than a plainly
marked gap.

**Consequence:** D11 stays 🟡 until next session. The supporting machinery
(`ledger.py`, the null-rate coverage report) was deliberately built *before*
the cutoff specifically so the deferred notebooks are a straightforward write
next time, not a re-design — see `docs/STATE.md`'s "single next action".

---

## ADR-018 — Telegram as the alert transport, WhatsApp as the intended one

**Status:** accepted · 2026-09-01

WhatsApp is where this project's users actually are; in Pakistan it dominates
in a way Telegram does not. It is the correct channel for a public-health
alert aimed at Rawalpindi residents.

**Chosen:** ship Telegram. It needs only a bot token, sends immediately, costs
nothing, and can be demonstrated live in seconds.

**Rejected for now:** WhatsApp. Business-initiated messages go through the
Business API, which requires a verified Meta Business account, a dedicated
number, and pre-approved message templates — days of setup, against a project
window measured in hours.

**Consequence:** the alert *rule* — fire on `P(AQI>200) > 0.6`, deduplicate
within an episode, send an all-clear — lives in `alerts/rules.py` and is
independent of transport. Senders are pluggable. Adding WhatsApp is a second
sender, not a redesign. The report states plainly that the shipped channel is
not the right one for the audience, and why.

---

## ADR-019 — `torch` pin bumped from 2.5.1 to 2.7.1 (this venv runs Python 3.13)

**Status:** accepted · 2026-09-01 (session 5)

`pyproject.toml` declares `requires-python = ">=3.11"`, but the actual `.venv`
in this environment resolved to Python 3.13. `torch==2.5.1` (the original
pin) has no published wheel for cp313 — `pip install -e ".[models]"` failed
outright before any session-5 code ran.

**Chosen:** bump the pin to `torch==2.7.1`, the oldest 3.13-compatible release
available from PyPI at session time. `scikit-learn==1.6.0` and
`lightgbm==4.5.0` both installed clean against 3.13 and were left alone.

**Rejected:** pinning the venv to 3.11 instead. Nothing in this repo's own
code requires 3.13; the mismatch is purely an artifact of how this machine's
`.venv` was created, and reprovisioning it was out of scope for a
session with a hard deadline the next day. Flagging here so whoever next
touches environment setup knows the `requires-python` floor and the actual
interpreter have drifted apart.

**Consequence:** `mapie==1.0.1` (already pinned under the same `models`
extra, for the differentiator-2 conformal work) also has no cp313 wheel
(`pandas-stubs`-style bound: `<3.12,>=3.9`) — not fixed here since conformal
prediction is cut this session (ADR-021 below), but the next session that
picks it up will hit the same install failure `torch` did and needs its own
pin bump or a 3.11 environment.

---

## ADR-020 — Model Registry: `LocalModelRegistry`, not Hopsworks, not MLflow

**Status:** accepted · 2026-09-01 (session 5)

CLAUDE.md §11.2 names Hopsworks Model Registry primary, MLflow + Hugging Face
Hub fallback. `HOPSWORKS_API_KEY`/`HOPSWORKS_PROJECT` are still empty (the
same gap `HopsworksFeatureStore` has carried since session 3 — see
`docs/STATE.md`), and the session brief is explicit: register locally, do not
block on Hopsworks.

**Chosen:** `src/aqi/models/registry.py`'s `LocalModelRegistry` — one
directory per `(model_name, horizon_hours)` under `data/model_registry/`
(gitignored, like `data/feature_store/` — ADR-006/ADR-014's precedent:
regenerable via `python -m aqi.pipelines.training_pipeline`, not committed),
holding `metadata.json` (training window, feature-group version, git SHA,
feature columns actually used, per-horizon metrics, champion flag) plus a
`joblib`/`torch.save` artifact where one exists. `champion.json` records the
promotion decision.

**Rejected:** installing MLflow as the fallback the brief also offered
("Hopsworks if credentials exist, MLflow/local otherwise"). MLflow pulls in
a tracking-server dependency surface (Flask, SQLAlchemy, alembic, gunicorn)
this project has no other use for, and a plain local registry satisfies
D5's actual Evidence bar — "registered champion with metrics attached" — with
zero new dependencies and something that's already been exercised against
real data, not just imported.

**Consequence:** `HopsworksModelRegistry` doesn't exist yet, deliberately — an
unexercised stub against an account nobody has created is worse than an
honest gap (CLAUDE.md's prime directive, §1). D5 stays real and green on the
local backend; the Hopsworks half is the same class of outstanding item as
D3's, and needs the same thing: Aliza creates the project, then it gets
built and tested against something live.

---

## ADR-021 — Champion selection this session: mean RMSE, not median lead time

**Status:** accepted · 2026-09-01 (session 5)

CLAUDE.md §12.3 names **median lead time on AQI>200 episodes at D+1** as the
one primary metric that decides promotion. Computing lead time requires the
episode-detection machinery (`evaluation/episodes.py`) and a populated
forecast ledger — both differentiator #1/#3 work the session brief explicitly
cuts ("SKIP ENTIRELY: ... lead-time analysis").

**Chosen:** champion = the ladder entry (baseline or ML, per I6) with the
lowest **mean RMSE across h24/h48/h72** on the 2025-26 smog-season test
window. Recorded as `mean_rmse_across_horizons` and
`champion.selection_rule`/`selection_value` in `reports/metrics/ladder.json`,
and as the `selection_rule` string in `champion.json` — so nobody reading the
registry later mistakes this for CLAUDE.md's real primary metric.

**Rejected:** RMSE at a single horizon (e.g. D+1 only) — CLAUDE.md §12.4 is
explicit that collapsing per-horizon detail into one number is an
anti-pattern; a horizon-averaged RMSE at least uses all three rather than
privileging one arbitrarily.

**Consequence:** whichever session builds `evaluation/episodes.py` and wires
the ledger to ≥30 days (§11.2's ledger-gating threshold) should re-run
promotion under the real §12.3 rule and may get a different champion — this
session's registration is not assumed final. The result itself is worth
keeping regardless: **SARIMAX won at every horizon** (mean RMSE 19.50 vs.
LightGBM's 27.45 and the LSTM's 26.59), which is exactly the kind of
"the fancy model didn't win" finding CLAUDE.md §12.1 asks the report to state
plainly rather than bury.

---

## ADR-022 — Baseline definitions operationalized from the stored `target_daily_aqi_h24` column, not a raw CAMS refetch

**Status:** accepted · 2026-09-01 (session 5)

CLAUDE.md §12.1 defines the baselines in one line each ("persistence: today's
max"; "seasonal naive: same day last week"; "climatology: day-of-year
historical mean") without specifying how "today's max" is obtained at an
arbitrary intra-day issue time `T`, and D5's brief is explicit that training
reads *only* from the Feature Store, never a raw source.

**Chosen:** reconstruct a `date -> daily_aqi` series per zone
(`models.dataset.daily_aqi_by_date`) from the already-stored
`target_daily_aqi_h24` column — which *is* "the daily_aqi of `local_date + 1
day`," since `builder._add_targets` computes it once per calendar day. Then:
persistence looks up `daily_aqi(local_date(T) - 1 day)` — the most recently
*completed* day, since the in-progress day isn't finalized at an arbitrary
`T`; seasonal-naive looks up `daily_aqi(target_date - 7 days)`; climatology
fits a train-only day-of-year mean of the same series. All three read only
already-realized values strictly before `T` (never a peek at `T`'s own
in-progress day), so none of this violates I1 despite reusing a
horizon-24 target column as an input.

**Rejected:** an EPA-NowCast-based "current conditions" proxy
(`hourly_aqi_nowcast`, already a stored feature) as the persistence signal.
Simpler to wire (no day-shifting), but it's on the wrong scale — NowCast is a
trailing-12h *real-time* estimate, the targets are 24h-mean-based *daily*
AQI — and would have made persistence's baseline number a mix of forecast
skill and definition mismatch, exactly what CLAUDE.md I8 says never to
conflate.

**Consequence:** all three baselines are directly comparable to every ML
rung on identical (city_id, time_utc) test rows (`HorizonMatrix.test_*`) —
see ADR-023 below for why LSTM's own row count differs slightly.

---

## ADR-023 — SARIMAX runs at daily granularity with a positional index, not hourly with a DatetimeIndex

**Status:** accepted · 2026-09-01 (session 5)

Two implementation obstacles, both worth recording so the next session
doesn't waste time rediscovering them:

1. This environment's `scipy` (1.18.1) is newer than `statsmodels==0.14.4`
   was tested against — the default `lbfgs` optimizer path calls
   `scipy.optimize.fmin_l_bfgs_b(..., disp=...)` internally, and that
   parameter no longer exists in this scipy. **Chosen:** `model.fit(disp=False,
   method="powell")`, which avoids that code path entirely.
2. `SARIMAXResults.append(...)` requires its new data's index to literally
   extend the fitted model's inferred date frequency — and the real BLH /
   forecast-archive gaps (`docs/STATE.md`) make the daily series
   non-contiguous in calendar terms, so a `DatetimeIndex` triggers `"Given
   endog does not have an index that extends..."`. **Chosen:** a plain
   `RangeIndex` for both fit and append. With no `seasonal_order` component,
   the AR/MA terms already mean "the previous *available* day," not "the
   previous calendar day," so this changes nothing about what the model
   represents.

Also decided in the same pass: SARIMAX runs once per **zone**, at **daily**
granularity — endog = `target_daily_aqi_h{horizon}` (constant within a
calendar day, ADR-022), exog = the horizon's own admitted `fc_*_h{horizon}`
future covariates (ADR-011), averaged over the issuing day. Averaging within
a day is leakage-safe: every hourly value going into the mean was itself
issued at or before its own hour (I1); this rung just treats "issued that
day" as the coarser unit its daily granularity actually operates at.

**Rejected:** hourly SARIMAX. Same information (the target is constant
within a calendar day) at ~24x the compute for a state-space model with
weekly-seasonal terms — a clear case for CLAUDE.md §4's "keep it small."

**Consequence:** SARIMAX turned out to be the strongest rung at every horizon
(ADR-021) — worth investigating further in a later session: whether it's the
daily aggregation smoothing hourly noise out of the exogenous forecasts, or
whether the ML rungs are simply under-tuned at this session's fixed,
un-searched hyperparameters (CLAUDE.md §4 cuts hyperparameter search, so this
session can't distinguish the two).

---

## ADR-024 — LSTM sequence built from existing lag columns; target normalization added after a first failed run

**Status:** accepted · 2026-09-01 (session 5)

Rung 5 needed a genuinely different (sequential) input shape from the
flat-feature-vector rungs above it, without re-deriving a raw hourly window
(extra engineering surface, extra chance of a leakage bug).

**Chosen:** for 10 base variables (`conf/features.yaml`'s `lags.base_features`),
stack the already-computed `_lag_{168,48,24,12,6,3,1}h` columns plus the
current-hour value into an 8-step sequence per sample — every column used has
`min_lag_hours = None` (ADR-011: historical, safe at every horizon), so this
cannot leak regardless of the target horizon. A 1-layer LSTM (hidden size 16)
reads the sequence; the zone one-hot is concatenated to the final hidden
state before a linear head, matching the "give every pooled model a zone
signal" treatment `dataset._with_zone_dummies` gives Ridge/RF/LightGBM.

**A real bug caught before it shipped:** the first end-to-end run scored
RMSE 136 on a target whose std is 44 — worse than predicting the mean.
Root cause: raw AQI (0-500 scale) fed directly as the regression target, with
no output normalization, so a freshly-initialized net started too far from
the loss surface for Adam at a small fixed LR to recover in 8 epochs. Fixed
by fitting target mean/std on the **train split only** (the same rule
`evaluation/scaling.py` states and `RidgeModel`'s `StandardScaler` enforces)
and un-scaling at predict time. Post-fix: RMSE 21.4, R² 0.80 at h24 —
competitive with LightGBM. Caught by actually running the pipeline against
real data before considering the rung done, not by unit tests alone (the
unit-test surface for this rung is deliberately thin — CLAUDE.md "keep
models small" — so this class of numerical bug needed a real run to surface).

**Consequence:** `LSTMModel`'s train/test row counts differ slightly from
`HorizonMatrix`'s (the other pooled rungs) — it only requires 10 base
variables' lag columns to be non-null, not the full ~215-column admitted
feature set, so it retains more rows through `dropna`. Both counts are
reported per-rung in `ladder.json` (`n`/`n_train`), so the difference is
visible rather than hidden — read as "the split's temporal boundary is
identical, the row-level footprint isn't," not literally bit-identical row
sets across every rung.

---

## ADR-025 — Live serving uses LightGBM, not SARIMAX, even though SARIMAX is the metrics champion

**Status:** accepted · 2026-09-01 (session 6)

`reports/metrics/ladder.json`'s champion is SARIMAX (ADR-021: mean RMSE
19.50). But `SarimaxModel.fit_predict_daily` (`models/sarimax.py`, ADR-023)
was built to **evaluate a backtest**: it extends a fitted model's state with
`statsmodels`' `.append(actual_endog, ...)`, which requires already knowing
the true outcome for every day it "predicts." That is exactly right for
scoring a held-out test season and exactly wrong for a live `/forecast`
endpoint asking about a day that hasn't happened yet — there is no known
outcome to append.

**Chosen:** `serving/inference.py` calls the registered **LightGBM** model
instead. `.predict(row)` needs a feature vector, not a known answer, so it
serves genuinely future days with no rework. `explain/shap_explain.py`
explains the same LightGBM model — this session's brief already ruled out a
`KernelExplainer` on SARIMAX, so the serving model and the explained model
are now the same model, which is simpler than the alternative (two
different serving paths) and arguably more honest: the "why" page explains
the model that's actually answering the question on every other page.

**Rejected:** reworking `SarimaxModel` to call
`SARIMAXResults.get_forecast(steps=h, exog=...)` for genuine forward
prediction. This is the *correct* long-term fix — the exog/endog pairing in
`_daily_series` already means "exog issued on day D describes day D+h," so a
model fit through day D-1 and asked to forecast day D from its own exog
would give a real live SARIMAX forecast. Not done today: it needs care
(excluding the "current" row from training, handling the case where a
zone's most recent day is itself incomplete) that this deadline doesn't
allow, and getting it subtly wrong would be worse than the honest
LightGBM-serves substitution this ADR documents instead.

**Consequence:** `reports/metrics/ladder.json` and the Model card page both
name SARIMAX as champion; `/forecast`, `/explain`, the Now/3-day
forecast/Why pages and the alert rule all actually run on LightGBM. Every
place that could confuse the two states the distinction explicitly
(`serving/inference.py`'s module docstring, `ExplainResponse.explainer_note`,
the Why page's warning banner). Fixing SARIMAX for live forecasting is
the natural next differentiator-adjacent task, not a design gap.

---

## ADR-026 — Alert trigger: D+1 point forecast > 200, not `P(AQI>200) > 0.6`

**Status:** accepted · 2026-09-01 (session 6)

CLAUDE.md §14 specifies alerting on `P(AQI>200) > 0.6` — "the uncertainty
thesis applied to a real decision." That needs a probability head
(a classifier, or a conformal interval to derive an exceedance probability
from), and both are differentiator work cut from every session so far
(ADR-021; this session's brief states it outright: "P(AQI>200) is
unavailable since the classifier was cut").

**Chosen:** `alerts/rules.py` triggers on the **D+1 LightGBM point forecast**
(`horizon_hours == 24`, tomorrow — the most actionable one) crossing 200.
Deduplication is a state machine (`data/alerts_state.json`, gitignored,
regenerable) keyed on "is this zone currently in a hazard episode," firing
only on the transition in (`episode`) or out (`all_clear`) — CLAUDE.md's
"deduplicate within an episode... send an all-clear" read as a state
transition rule rather than a fixed time window.

**Rejected:** silently building this as if it were the real `P(AQI>200)`
rule. `alerts/rules.py`'s module docstring and this ADR both say plainly
that it's a substitute — an honest, cruder version of the rule CLAUDE.md
actually wants, not a relabelling of it. CLAUDE.md I5's "generated, never
typed" discipline applied to *behavior*, not just numbers: the code should
never claim a rule it doesn't implement.

**Consequence:** once a probability head exists, `alerts/rules.py` gets one
line changed (the trigger condition) and the state-machine/dedup/all-clear
machinery around it is unaffected — that part *is* built to the real spec
already.

---

## ADR-027 — Dashboard: four pages, no scorecard, ledger window shown instead

**Status:** accepted · 2026-09-01 (session 6)

CLAUDE.md §14 specifies five Streamlit pages including a live Scorecard
(us-vs-AQICN, wins and losses). The forecast ledger currently holds ~10
observed rows and ~4 AQICN rows spanning under a day (`data/ledger/`,
confirmed via `store.ledger.ledger_window` at session time) — nowhere near
enough to compute a trustworthy win/loss comparison, let alone the
interval-coverage plot the same page would need.

**Chosen:** ship four pages (Now / 3-day forecast / Why / Model card),
per this session's explicit brief. The Model card page states the ledger's
real window — start timestamp, end timestamp, row count, read live from
`store.ledger.ledger_window` (or from the static snapshot in `--static`
mode) — in the space the scorecard would occupy, with an explicit "why not
shown" explanation, rather than silently omitting the topic.

**Rejected:** building a scorecard anyway and letting a 10-row comparison
imply more than it can support. CLAUDE.md I4 ("never claim a benchmark you
didn't capture") and the prime directive (§1: evidence over quantity) both
argue against it — a five-page dashboard with one dishonest page is worse
than a four-page one that says exactly what it has and hasn't got yet.

**Consequence:** the real scorecard is a straightforward addition once the
now-live, now-fixed (ADR-012) hourly clock starter has accumulated weeks of
ledger history — the ledger-reading and coverage-reporting machinery
(`store/ledger.py`, session 4) already exists; only the comparison logic
and the page itself are missing.

---

## ADR-028 — `shap` pin bumped from 0.46.0 to 0.52.0 (no MSVC build tools in this environment)

**Status:** accepted · 2026-09-01 (session 6)

Same class of problem as ADR-019 (`torch`): `shap==0.46.0` (the original
pin) has no prebuilt wheel for this environment's Python 3.13 interpreter
and needs to compile a native C++ extension (`shap.cext`) from source to
install — which requires an MSVC toolchain this machine doesn't have
(`error: Microsoft Visual C++ 14.0 or greater is required`).

**Chosen:** bump to `shap==0.52.0`, which ships a `cp312-abi3` wheel —
Python's stable ABI, forward-compatible with 3.13, so no build step at all.

**Rejected:** installing the Visual C++ Build Tools. Possible, but a
multi-GB download and installer run against a same-day deadline for a
version bump that costs one pyproject line and has no known behavioral
difference for `TreeExplainer` (the only SHAP API this project uses).

**Consequence:** none expected — `shap_explain.py` only calls
`shap.TreeExplainer(...)(row)`, an API stable across this version range —
but flagged here per CLAUDE.md §19 so a future session isn't surprised by
the pin not matching what a tutorial or the CLAUDE.md excerpt implies.

---

## ADR-029 — Urdu strings shipped without native-speaker review

**Status:** accepted, flagged as outstanding · 2026-09-01 (session 6)

CLAUDE.md §14 requires the ~20 fixed Urdu strings (categories, health
guidance, alert templates) to be "hand-written once... and read by a native
speaker." `conf/i18n_ur.yaml`'s health-guidance and alert-template strings
were hand-written this session (never LLM-translated at request time, which
is the invariant that actually matters for I5/the accessibility claim) but
**not** reviewed by a native Urdu speaker — this session has no access to
one.

**Chosen:** ship them anyway, with the gap stated in the YAML file's own
header comment, in this ADR, and in `docs/STATE.md` — visible in three
places a reader might look, not buried once. Category names
(`aqi_scale.py::_CATEGORIES`) were already reviewed in an earlier session
and are unaffected.

**Rejected:** waiting to ship D13/D14 until a native speaker is available.
The session deadline doesn't allow it, and a demo-ready feature with a
flagged translation-quality caveat is worth more under the prime directive
(§1) than an unshipped one waiting on a reviewer nobody has scheduled yet.

**Consequence:** a native-speaker pass over `conf/i18n_ur.yaml` (health
guidance + alert templates, ~16 strings) is a named outstanding item, not a
silently-skipped step — `docs/STATE.md` carries it forward until done.
