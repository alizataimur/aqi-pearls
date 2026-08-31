---
description: Run one autonomous build session against a deliverable group
---

Read `CLAUDE.md` and `docs/STATE.md` before anything else.

You are running the session identified by: **$ARGUMENTS**

If that is a session number, look it up in the ledger in `docs/RUNBOOK.md` §2 to find the goal and
the deliverable rows it closes. If it is a description, work to that instead.

## How to run

Work autonomously to the end of the session. **Do not stop to ask me for approval or preference.**
Take the most reasonable reading of anything ambiguous, write the choice into `docs/DECISIONS.md`
as a new ADR (chosen / rejected / why), and continue.

Stop and ask only if:
- a decision is irreversible *and* could reasonably go either way, or
- you are blocked on a credential or an account I have not supplied.

Honour `CLAUDE.md` §4 — the cut list. Build nothing that is not in §2 (required) or §3
(differentiating). If you find yourself wanting to add something, add it to §4 instead and say so.

Respect the invariants in §5 without exception. I1 (no temporal leakage) and I3 (ledger written at
issue time) are build-breaking; a violation of either invalidates work downstream of it.

Build to `CLAUDE.md` §2.1, not just to §2. Every deliverable row has a named distinctive answer —
that column is how the row is graded, not a bonus. A row shipped plain is a cost to be bought back
with any slack, not a neutral outcome.

## Before you finish

1. `ruff check .`, `ruff format --check .`, `mypy src/` and `pytest` — all green. Invoke them as
   `python -m ruff` / `python -m mypy` so the pinned versions in `pyproject.toml` are the ones that
   actually run. Do not leave a failing test without a note.
2. Update `docs/DELIVERABLES.md` for every row this session touched: status marker, the Evidence
   cell, and anything now outstanding. A row goes ✅ only when someone else could run its Evidence
   command and have it pass — "the file exists" is not evidence.
3. Update `docs/STATE.md`: current stage, what moved, what broke, the single next action, and any
   blocker that needs me.
4. Commit with a conventional-commit message describing what changed and why.

**Write `STATE.md` as you finish each major piece, not once at the end.** Sessions get cut off by
usage limits mid-task, and a slightly stale handoff is worth far more than none — the next session
reads that file before anything else.

## Then report, in five lines or fewer

- what is done and how it can be verified
- what is not done
- what needs me (a credential, a deploy, a decision)
- any invariant you were unable to satisfy
- the single next action
