.PHONY: setup test lint clock clock-dry probe backfill features train predict benchmark api app report

# Load .env if present so `make clock` works from a checkout without exporting
# anything by hand. .env is gitignored (I9); CI uses GitHub Secrets instead and
# has no .env, which is why this is conditional.
ifneq (,$(wildcard .env))
include .env
export
endif

setup:                ## install deps + pre-commit
	pip install -e ".[dev]"

test:                 ## pytest incl. leakage test
	pytest

lint:                 ## ruff + mypy
	ruff check . && ruff format --check . && mypy src/

# ---- Stage 0 : start the clock (CLAUDE.md §6) --------------------------------
clock:                ## capture AQICN observation + published forecast, now
	python scripts/clock_starter.py

clock-dry:            ## same, but print instead of writing
	python scripts/clock_starter.py --dry-run

probe:                ## answer the §8.2 open questions with data
	python scripts/probe_sources.py

# ---- Stage 2+ : not yet implemented ------------------------------------------
backfill features train predict benchmark api app report:
	@echo "'$@' is not implemented yet — see docs/STATE.md for the current stage."
	@exit 1
