# STATE — current build position

> Read CLAUDE.md first for rules and contracts. This file is *position only*.
> Update it at the end of every session (CLAUDE.md §19).

**Stage:** 0 — Start the clock
**Updated:** 2026-08-27
**Repo status:** scaffolded, not yet pushed to GitHub

---

## Stage 0 gate — one green daily run and dated files in `data/ledger/`

| Item | Status | Notes |
|---|---|---|
| Repo skeleton + config | done | `conf/cities.yaml`, `conf/config.yaml` |
| `aqi_scale.py` + tests | done | 44 tests green; EPA 2024 breakpoints verified against the AQS code table |
| `scripts/clock_starter.py` | done | untested against the live API — no outbound network in the authoring environment |
| `.github/workflows/clock-starter.yml` | done | hourly at :07 |
| AQICN token in GitHub Secrets | **BLOCKED — needs Aliza** | free token: https://aqicn.org/data-platform/token/ |
| Repo pushed, public | **BLOCKED — needs Aliza** | public = unlimited Actions minutes |
| First green workflow run | blocked | follows the two above |
| `make probe` findings recorded | blocked | needs network; see open questions |
| Rubric confirmed with instructor | **BLOCKED — needs Aliza** | decides how much of §3 survives |

---

## The single next action

Push to a public GitHub repo, add `AQICN_TOKEN` to Actions secrets, and run the
`clock-starter` workflow once by hand. Nothing else in the project should start
before the ledger has its first row — every hour before that is an hour of
benchmark history that cannot be recovered (CLAUDE.md I3).

---

## Open questions — answered by `make probe`, not by guessing

1. **Do Islamabad and Rawalpindi share one CAMS grid cell?** CLAUDE.md §8.2
   expects yes at ~0.4° resolution. If so they are one forecast zone with two
   labels and the report must say so. `conf/cities.yaml:cams_grid` stays `null`
   until the probe fills it.
2. **What is the true earliest CAMS date for these coordinates?** Documented as
   ~Aug 2022 for the global domain. `conf/config.yaml:sources.backfill_start`
   stays `null` until probed.
3. **What shape is the AQICN feed?** Undocumented. The probe snapshots it to
   `docs/schemas/aqicn_feed.json`; a contract test pins it at Stage 2.

---

## Known gaps carried forward

- `tests/test_no_leakage.py` does not exist yet — nothing to leak at Stage 0.
  **Stage 2 gate item:** write it and give it its own CI step (placeholder
  comment is in `.github/workflows/ci.yml`).
- `make backfill/features/train/...` are stubs that exit 1 by design.
- Deploy targets (Hopsworks, HF Spaces, Streamlit Cloud) not yet created.

---

## Log

**2026-08-27 — Stage 0 scaffolded.** Repo skeleton, config, `aqi_scale.py` with
44 passing tests, clock starter, probe script, two workflows. EPA 2024 PM2.5
breakpoints verified against the authoritative AQS code table; found and kept
the 501–999 band above 325.5 µg/m³ that most implementations clip away
(ADR-003). Clock starter changed from daily to hourly (ADR-002). Not yet run
against the live API — the authoring environment had no outbound access to
Open-Meteo or AQICN, so first execution is on Aliza's side.
