"""Loader for `conf/i18n_ur.yaml` (CLAUDE.md §14, D13).

All strings here are hand-written, never LLM-translated — the loader is
deliberately dumb (a YAML read and three dict lookups), because the honesty
guarantee lives in the file, not in code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
I18N_YAML = REPO_ROOT / "conf" / "i18n_ur.yaml"


@lru_cache(maxsize=1)
def load_i18n(path: Path = I18N_YAML) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def health_guidance(category: str) -> tuple[str, str]:
    """(english, urdu) health guidance for an AQI category name — the exact
    strings `aqi_scale.category_for` returns."""
    entry = load_i18n()["health_guidance"].get(category)
    if entry is None:
        return "", ""
    return entry["en"], entry["ur"]


def feature_label(feature_name: str) -> tuple[str, str]:
    """(english, urdu) label for a feature base-name, falling back to the
    raw name in both slots when it isn't in the hand-written list — a
    visible degrade (CLAUDE.md §14), never a silent mistranslation."""
    entry = load_i18n()["feature_labels"].get(feature_name)
    if entry is None:
        return feature_name, feature_name
    return entry["en"], entry["ur"]


def alert_template(kind: str) -> tuple[str, str]:
    """(english, urdu) `str.format`-ready alert template, `kind` in
    {"episode", "all_clear"}."""
    entry = load_i18n()["alerts"][kind]
    return entry["en"], entry["ur"]
