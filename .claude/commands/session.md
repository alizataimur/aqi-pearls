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

## Before you finish

1. `make lint` and `make test` until both are green. Do not leave a failing test without a note.
2. Update `docs/STATE.md`: current stage, what moved, what broke, the single next action, and any
   blocker that needs me.
3. Commit with a conventional-commit message describing what changed and why.

## Then report, in five lines or fewer

- what is done and how it can be verified
- what is not done
- what needs me (a credential, a deploy, a decision)
- any invariant you were unable to satisfy
- the single next action
