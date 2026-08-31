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
