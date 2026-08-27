# RUNBOOK — executing this project with Claude Code

How to get from an empty repo to a submitted project. `CLAUDE.md` is the contract, `STATE.md` is the
position, and this file is the **procedure**.

---

## 0. The one thing to understand first

You asked not to run stage by stage. You can drop the *approval gates* — nothing here makes you sit
and confirm each step. What you cannot drop is **session boundaries**, and it is worth being clear
why, because it changes how you drive this.

Claude Code has a finite context window. A six-week build does not fit in one. When context fills,
the session compacts — it summarises older turns and keeps going — and each compaction loses detail.
A single marathon session ends up rebuilding its own understanding from a lossy summary of itself,
and that is where the silent mistakes come from.

So the unit of work is **one session per deliverable group**, and the mechanism that makes a fresh
session continue rather than restart is two files:

- **`CLAUDE.md`** is loaded automatically at the start of every session. Rules, invariants,
  contracts. It never changes during a session.
- **`docs/STATE.md`** is read first thing and written last thing. Position, blockers, next action.

That pair is the baton. Each session picks it up, runs, and puts it down. You are not approving
anything mid-session — you launch a session, walk away, and come back to a commit plus an updated
`STATE.md`.

Thirteen sessions, indexed by deliverable, below.

---

## 1. One-time setup — do all of this before session 1

### 1.1 Accounts and tokens (about an hour, and it is the real blocker)

Get all of them now. Discovering at week four that a free tier needs email verification is the kind
of delay that eats a weekend.

| Service | For | Where |
|---|---|---|
| **GitHub** — repo must be **public** | D8. Public = unlimited Actions minutes | github.com/new |
| **AQICN token** | D1, and the whole benchmark | aqicn.org/data-platform/token/ |
| **Hopsworks** | D3, the Feature Store row | app.hopsworks.ai |
| **Hugging Face** | Parquet fallback + FastAPI Space | huggingface.co/join |
| **Streamlit Community Cloud** | D9 public URL | share.streamlit.io |
| **Telegram bot** | D14. Message @BotFather, `/newbot` | telegram.org |
| **Groq or Gemini** | Briefing prose. Optional — must degrade | console.groq.com |

Put every one into GitHub → Settings → Secrets and variables → Actions, and into a local `.env`
copied from `.env.example`. Never anywhere else (I9).

### 1.2 Windows / PowerShell

PowerShell is fine for this entire project. WSL is not required — the workflows run on GitHub's
Linux runners regardless, and everything local is plain Python.

Three differences from the POSIX commands used elsewhere in this file:

| POSIX | PowerShell |
|---|---|
| `python3` | `python` |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `cmd1 && cmd2` | `cmd1; cmd2` (Windows PowerShell 5.1 rejects `&&`) |

**`make` does not exist on Windows.** Every `make` target in this runbook is a thin wrapper; run the
underlying command directly:

| Make target | Direct command |
|---|---|
| `make test` | `pytest` |
| `make lint` | `ruff check .` then `ruff format --check .` then `mypy src/` |
| `make clock-dry` | `python scripts/clock_starter.py --dry-run` |
| `make clock` | `python scripts/clock_starter.py` |
| `make probe` | `python scripts/probe_sources.py` |

The scripts read `.env` themselves rather than relying on `make` to export it, so they behave
identically on every platform.

If `Activate.ps1` is blocked by execution policy:
`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

**Do not keep the repo inside OneDrive.** OneDrive sync and git are a bad combination — it locks
files mid-write, uploads the whole virtual environment, and produces sync-conflict copies that git
then sees as untracked junk. Move it somewhere plain, e.g. `C:\dev\aqi-pearls`.

### 1.3 Install and point Claude Code at the repo

```bash
npm install -g @anthropic-ai/claude-code     # verify the current install command in the docs
cd aqi-pearls
claude
```

`CLAUDE.md` at the repo root is picked up automatically. Do **not** run `/init` — it would offer to
generate a CLAUDE.md, and you already have one that is worth far more than a generated skeleton.

### 1.4 Stop the permission prompts

Unattended runs are the point, and a session that halts on the fourth `pytest` is not unattended.
`.claude/settings.json` is committed in this repo with an allowlist for the safe things (pytest,
ruff, mypy, git status/diff/add/commit, make, python) and a denylist for the dangerous ones (`rm
-rf`, writing `.env`, force pushes).

Check the exact pattern syntax with `/help` or `claude --help` before relying on it — the CLI moves
faster than any runbook. If prompts still interrupt, run `claude --permission-mode acceptEdits`.

Avoid `--dangerously-skip-permissions` on your own machine. It exists for throwaway sandboxes.

### 1.5 Know four commands

| Command | Use |
|---|---|
| `/clear` | Between sessions. Fresh context, same repo. |
| `/compact` | Mid-session when context fills. Give it an instruction: `/compact keep the feature spec and the leakage decisions, drop the API exploration` |
| `claude --continue` | Resume the last session — same day, same task |
| `/model` | Switch model. Heavier tier for architecture and evaluation design; lighter for mechanical work |

---

## 2. The session ledger

Every row is one session. Start each with `/clear`, then `/session <N>`. Walk away.

| # | Session | Closes | Rough agent time |
|---|---|---|---|
| 0 | **Start the clock** — token, push, first green run, `make probe` | prerequisite | 1h, mostly you |
| 1 | Sources: Open-Meteo air/ERA5/historical-forecast + schema capture | D1 | 2–3h |
| 2 | Feature + target builder, `features.yaml`, `min_lag`, **leakage test** | D2 | 3–4h |
| 3 | Feature store (Hopsworks + Parquet parity) and resumable backfill | D3, D4 | 3–4h + backfill wall time |
| 4 | EDA notebook + model-vs-station divergence | D11 | 2–3h |
| 5 | Baselines then ladder rungs 1–5, per-horizon RMSE/MAE/R² | D6, D7, D12 | 4–6h |
| 6 | Episode metrics + conformal intervals with per-group coverage | differentiators 1 & 2 | 3–4h |
| 7 | Training pipeline, model registry, gated promotion | D5 | 3–4h |
| 8 | All workflows live, failure notifications | D8 | 2–3h |
| 9 | SHAP + grounded briefing, English and Urdu | D13 | 2–3h |
| 10 | FastAPI + Streamlit + both deployments | D9, D10 | 4–6h |
| 11 | Telegram alerts on P(AQI>200) > 0.6 | D14 | 1–2h |
| 12 | Report, README, **audit sweep** | D15 | 4–6h |

**Order matters in three places only.** Session 0 before everything (its losses are permanent).
Session 2's leakage test before session 5 (otherwise you may spend a day on numbers that are
fiction). Session 8 as early as you can bear (uptime is wall-clock). Everything else can slide.

---

## 3. How to launch a session

The repo ships a `/session` command so you type one line. What it expands to, and what matters
about it:

```
Read CLAUDE.md and docs/STATE.md. You are running session <N>: <goal>.

Close these deliverable rows: <D-rows>.

Work autonomously to the end. Do not stop to ask me for approval or preference —
take the most reasonable reading, write the choice into docs/DECISIONS.md, and
continue. Stop only if a decision is irreversible and could reasonably go either
way, or if you are blocked on a credential I have not supplied.

Honour §4's cut list. Do not build anything outside §2 and §3.

Finish by: running make lint and make test until green; updating docs/STATE.md
with what moved, what broke, and the single next action; and committing with a
conventional-commit message. Then tell me in five lines what is done, what is
not, and what needs me.
```

Three things in there are load-bearing:

- **"Do not stop to ask"** is what converts gated stages into an unattended run. The escape hatch
  for irreversible decisions stays, because that is the one case where guessing is worse than
  waiting.
- **"Write the choice into DECISIONS.md"** is what stops autonomy becoming amnesia. Every guess it
  makes on your behalf is recoverable because it is written down.
- **"Tell me what needs me"** is your queue. Deploys, secrets, and the instructor email are the only
  things that ever land there.

For sessions 5, 6 and 7 — the ones where a design mistake costs days — enter **plan mode** first
(Shift+Tab cycles modes; it is read-only) and let it produce a plan before it writes anything.

---

## 4. Verifying each deliverable

The submission risk is not that the work is missing. It is that it exists and does not quite do what
the brief asked. Run these; each one either prints evidence or fails.

| D | Prove it with |
|---|---|
| D1 | A green `feature-pipeline` run in the Actions tab |
| D2 | `pytest tests/test_features.py` and `docs/feature_spec.md` listing every feature with its `min_lag` |
| D3 | Row count from the Hopsworks feature group, pasted into `STATE.md` |
| D4 | `reports/metrics/coverage.json` — first date, last date, gap list |
| D5 | The registered champion in the registry with metrics attached |
| D6 | The ladder table containing Ridge, Random Forest **and** the PyTorch model |
| D7 | `reports/metrics/ladder.json` with RMSE, MAE, R² **per horizon** |
| D8 | Actions history: hourly and daily workflows, ≥7 consecutive green days |
| D9 | Open the Streamlit URL in a private window |
| D10 | The dashboard's network tab showing calls to the FastAPI service |
| D11 | `notebooks/01_eda.ipynb` rendered, reading as a narrative |
| D12 | The ladder containing SARIMAX **and** the deep model |
| D13 | The Why page rendering real SHAP contributions |
| D14 | A Telegram message in your own chat |
| D15 | `reports/final_report.md`, every number traced to `reports/metrics/` |

### The audit sweep — session 12, and do not skip it

An agent that wrote the code is a poor judge of it. `.claude/agents/auditor.md` defines a reviewer
that starts with **fresh context** and has not watched anything being built. Run it before you
submit:

```
Ask the auditor agent to verify every row of CLAUDE.md §2 against the actual
repo, and to check invariants I1 through I10 one by one. For each, report
PASS or FAIL with the file and line that proves it. Assume nothing is done
because a commit message says so.
```

Then, separately:

```
Ask the auditor agent to try to find temporal leakage in the feature builder
and the training pipeline. Attack it: construct a case where a feature at
horizon h uses information from after the issue time. Report the specific
feature and the code path, or state that you could not construct one.
```

If the second one finds something, every number in the report is wrong and you fix it before you do
anything else. That is not pessimism — it is the single most common way projects like this fail, and
it fails silently.

---

## 5. What only you can do

Everything else is unattended. These are your queue:

| When | You |
|---|---|
| Setup | Create all seven accounts; put tokens in GitHub Secrets |
| Setup | Email your instructor about the rubric (§2). It decides how much of §3 survives |
| Session 0 | Push the repo public; run `clock-starter` once by hand |
| Session 3 | Create the Hopsworks project and the HF dataset repo |
| Session 8 | Confirm workflow secrets; watch the first 24h of runs |
| Session 10 | Connect Streamlit Cloud to the repo; create the HF Space |
| Session 11 | `/newbot` with @BotFather; get your chat id |
| Session 12 | Record the demo video. A week early, not the night before |
| Weekly | Glance at the Actions tab. A dead pipeline outranks whatever you planned |

---

## 6. Failure modes, and what to do

**"It rewrote something that was working."** Commit after every session, and read `git diff` before
you accept. Small commits are what make this cheap to undo.

**"It drifted off-spec and built something I did not ask for."** It lost the thread of §4. Start the
next session with `/clear` and name the cut list explicitly in the prompt.

**"Metrics suddenly look great."** Leakage until proven otherwise (§20). Run the audit sweep's
second prompt before you celebrate.

**"The session ran out of context halfway."** `/compact` with an instruction naming what to keep,
then continue. If it happens repeatedly in one session, that session is scoped too large — split it.

**"A free tier is down."** That is what the fallbacks are for (I10). Switch
`FEATURE_STORE_BACKEND=parquet` and carry on; note the outage in `STATE.md`.

**"The clock-starter workflow has been red for three days."** Stop everything else. This is the only
failure in the project that cannot be repaired later.

---

## 7. If you have less time than the plan assumes

Cut in this order, and write each cut into §4 with a paragraph for the report's future-work section:

1. Session 9's LLM briefing → keep the deterministic template only
2. Session 6's Mondrian grouping → keep marginal conformal coverage
3. Ladder rungs 3 and 5 → but **only** if the rubric does not name statistical or deep models,
   because D6 and D12 require them
4. The second forecast zone → one zone, honestly reported
5. Session 4's divergence notebook → fold two paragraphs into the report

Never cut: the ledger, the baseline table, the leakage test, or the report.
