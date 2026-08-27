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
