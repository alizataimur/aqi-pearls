"""Load `conf/features.yaml` and expand it into one FeatureSpec per column.

The YAML declares *generators* (a lag base list x a set of lag hours, a
future-covariate variable list x a set of horizons) rather than every
generated column name, so it stays maintainable. This module is the single
place that expansion happens — `builder.py`'s actual pandas output and this
module's expansion are compared column-for-column in
`tests/test_features.py`, so the two cannot silently drift apart.

`min_lag_hours` semantics are ADR-011 (docs/DECISIONS.md): `None` means "built
only from data timestamped <= the issue time, safe at every horizon"; an
integer `h` means "a future-dated covariate for target time T+h, admitted
only when the target horizon equals h exactly."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURES_YAML = REPO_ROOT / "conf" / "features.yaml"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    category: str
    min_lag_hours: int | None = None

    def admitted_at(self, horizon_hours: int) -> bool:
        """ADR-011: historical features always pass; future covariates only
        at their exact horizon."""
        if self.min_lag_hours is None:
            return True
        return self.min_lag_hours == horizon_hours


def load_raw_spec(path: Path = FEATURES_YAML) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def expand_feature_specs(raw: dict[str, Any] | None = None) -> list[FeatureSpec]:
    """Expand the declarative YAML into one FeatureSpec per output column."""
    raw = raw if raw is not None else load_raw_spec()
    specs: list[FeatureSpec] = []

    for entry in raw["pollutants"]:
        specs.append(FeatureSpec(entry["name"], "pollutant"))
    for entry in raw["weather"]:
        specs.append(FeatureSpec(entry["name"], "weather"))
    for name in raw["time"]:
        specs.append(FeatureSpec(name, "time"))

    lags = raw["lags"]
    for base in lags["base_features"]:
        for h in lags["lag_hours"]:
            specs.append(FeatureSpec(f"{base}_lag_{h}h", "lag"))

    rolling = raw["rolling"]
    for base in rolling["base_features"]:
        for window in rolling["windows_hours"]:
            for stat in rolling["stats"]:
                specs.append(FeatureSpec(f"{base}_roll_{stat}_{window}h", "rolling"))

    for name in raw["derived"]:
        specs.append(FeatureSpec(name, "derived"))
    for name in raw["physics"]:
        specs.append(FeatureSpec(name, "physics"))

    future = raw["future_covariates"]
    for variable in future["variables"]:
        for horizon in future["horizons_hours"]:
            specs.append(
                FeatureSpec(f"fc_{variable}_h{horizon}", "future_covariate", horizon)
            )

    return specs


def target_column_names(raw: dict[str, Any] | None = None) -> list[str]:
    raw = raw if raw is not None else load_raw_spec()
    horizons = raw["targets"]["horizons_hours"]
    names = []
    for h in horizons:
        names.append(f"target_daily_aqi_h{h}")
        names.append(f"target_exceeds_200_h{h}")
    return names
