# Pearls AQI Predictor

**The only AQI forecaster for Pakistan that publishes whether it beats the incumbent — including
the days it loses.** Three-day, uncertainty-aware air quality forecasts for Rawalpindi/Islamabad and
Lahore, explained in plain Urdu and English, running on $0 of serverless infrastructure.

> 🌐 **Live dashboard:** **[aqi-pearls-predictor.streamlit.app](https://aqi-pearls-predictor.streamlit.app/)**
>
> 📋 **Marking this?** [`docs/DELIVERABLES.md`](docs/DELIVERABLES.md) maps every requirement in the
> brief to where it lives and how to verify it.
>
> 🚧 **In build.** [`docs/STATE.md`](docs/STATE.md) has the current position.

---

## What makes it different

Most AQI projects report an average RMSE and stop. Average RMSE is dominated by ordinary days when
the AQI is 80 and the model guesses 82. Nobody in Pakistan is asking that question. They are
asking *"is Thursday a keep-the-kids-home day, and will I know in time?"*

So this one is built around four things instead:

1. **Episode detection over average error.** Precision, recall, false-alarm rate and — the headline —
   **lead time** on hazardous days. A forecast that flags Thursday's smog on Thursday morning has
   perfect recall and no value.
2. **Intervals and probabilities, never bare numbers.** Conformal prediction calibrated *per
   horizon* and *per regime*, so the report can say what the 90% band actually covered during smog
   season rather than on average.
3. **A public scorecard against AQICN's own forecast**, captured at issue time in an append-only
   ledger, showing wins and losses over the exact window it covers.
4. **Features engineered for Punjab smog physics** — inversion proxy, stagnation index,
   crop-burning window, festival calendar — not a generic global feature set.

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env          # add your free AQICN token
make test                     # full suite, incl. the EPA conversion + leakage tests
make clock-dry                # see what the ledger capture would write
make probe                    # answer the open data questions with data
make backfill                 # resumable, chunked pull to the CAMS floor
make features                 # one hourly feature-pipeline run, both zones
```

## Start the clock first

`scripts/clock_starter.py` runs hourly and captures AQICN's published forecast plus the current
station reading into `data/ledger/`. It depends on no model, no feature store and no pipeline, and
it ships before all of them — because AQICN offers no history endpoint, so any hour not captured is
gone permanently. Everything else in this repo can be rebuilt; the ledger cannot.

## Layout

| Path | What |
|---|---|
| `CLAUDE.md` | The operating manual — rules, contracts, invariants, build sequence |
| `docs/STATE.md` | Where the build actually is right now |
| `docs/DECISIONS.md` | ADR log: what was chosen, what was rejected, why |
| `src/aqi/` | Library code — sources, features, store, models, evaluation, serving |
| `scripts/` | Standalone Stage 0 tools that depend on nothing else |
| `data/ledger/` | Append-only forecast + observation ledger (committed on purpose) |

## Honest limitations

Kept here rather than buried, because they are load-bearing:

- Training labels are **CAMS reanalysis, not instrument measurements**. Ground stations serve as
  truth and benchmark; the disagreement between them is measured, not hidden.
- CAMS global resolution is ~0.4° (~45 km). `make probe` confirmed Islamabad and Rawalpindi return
  **byte-identical** CAMS series — one grid cell, reported as one modelled forecast zone
  (`capital`), not two independently-modelled cities.
- The scorecard covers exactly the days in the ledger. No backfilled benchmarks, ever.

## Licence

MIT.
