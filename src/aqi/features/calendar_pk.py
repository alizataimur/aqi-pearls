"""Punjab/Pakistan calendar flags — the non-physics half of §10's physics table.

Festival dates come from `conf/calendar_pk.yaml`, never a formula — Islamic
dates shift ~11 days a year and a sin/cos-of-day-of-year trick would silently
mis-date every one of them (CLAUDE.md §10).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_YAML = REPO_ROOT / "conf" / "calendar_pk.yaml"

# CLAUDE.md §10.
CROP_BURNING_START = (10, 15)  # Oct 15
CROP_BURNING_END = (11, 30)  # Nov 30
HEATING_START = (12, 1)  # Dec 1
HEATING_END_MONTH_DAY = (2, 15)  # Feb 15, following year


def load_festival_dates(path: Path = CALENDAR_YAML) -> set[date]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {date.fromisoformat(entry["date"]) for entry in raw["festivals"]}


def is_festival(day: date, festival_dates: set[date]) -> bool:
    return day in festival_dates


def crop_burning_window(day: date) -> tuple[bool, int]:
    """(in_window, day_count) — day_count is 1 on Oct 15, rising through Nov 30."""
    start = date(day.year, *CROP_BURNING_START)
    end = date(day.year, *CROP_BURNING_END)
    if start <= day <= end:
        return True, (day - start).days + 1
    return False, 0


def heating_season(day: date) -> bool:
    """Dec 1 - Feb 15, spanning the New Year."""
    start = date(day.year, *HEATING_START)
    end_this_year = date(day.year, *HEATING_END_MONTH_DAY)
    if day >= start:
        return True
    return day <= end_this_year
