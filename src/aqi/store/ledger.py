"""Read-only access to the forecast ledger (CLAUDE.md §11.3, I3).

Append-only JSONL under `data/ledger/{observed,aqicn,predicted}/<city>/
<YYYY-MM>.jsonl`. `observed`/`aqicn` are `scripts/clock_starter.py`'s
captures (the incumbent side, §6); `predicted` is
`aqi.pipelines.inference_pipeline`'s own issued forecasts (§11.3's other
half — this project's own side of the benchmark). `_quarantine/` holds rows
`scripts/quarantine_ledger.py` classified as unattributable (ADR-007) — this
reader always excludes it, so "read the ledger" never silently includes a
mislabelled row back into an analysis.

This module only reads. Nothing here may write, reorder or delete a ledger
row — that would violate I3's append-only contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd

from aqi.config import REPO_ROOT

LEDGER_ROOT = REPO_ROOT / "data" / "ledger"

LedgerKind = Literal["observed", "aqicn", "predicted"]

# CLAUDE.md's own schema names this differently per stream — §6's captures
# are "captured_at_utc" (the moment we polled AQICN), §11.3's forecast rows
# are "issued_at_utc" (the moment our model produced a prediction). Same
# role (the row's own timestamp, what every sort/window is keyed on),
# different name by design — not an inconsistency to paper over.
_TIMESTAMP_COLUMN_BY_KIND: dict[LedgerKind, str] = {
    "observed": "captured_at_utc",
    "aqicn": "captured_at_utc",
    "predicted": "issued_at_utc",
}


def _iter_files(kind: LedgerKind, root: Path) -> list[Path]:
    kind_root = root / kind
    if not kind_root.exists():
        return []
    return sorted(p for p in kind_root.rglob("*.jsonl") if "_quarantine" not in p.parts)


def read_ledger(kind: LedgerKind, root: Path = LEDGER_ROOT) -> pd.DataFrame:
    """Every non-quarantined row of one ledger stream, as a flat DataFrame.

    One row per JSON line. Nested fields (`iaqi`, `forecast`) are left as
    dict/list objects in their own column rather than pre-flattened — the
    streams have different nested shapes, and a caller reaching for a
    specific nested value is better served doing that explicitly than
    trusting a guessed flattening here.
    """
    timestamp_column = _TIMESTAMP_COLUMN_BY_KIND[kind]
    rows: list[dict[str, object]] = []
    for path in _iter_files(kind, root):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], utc=True)
    return frame.sort_values(timestamp_column).reset_index(drop=True)


def ledger_window(
    kind: LedgerKind, root: Path = LEDGER_ROOT
) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    """`(earliest, latest, row_count)` over this stream's own timestamp
    column (`captured_at_utc` for observed/aqicn, `issued_at_utc` for
    predicted — see `_TIMESTAMP_COLUMN_BY_KIND`).

    Every ledger analysis must print this before drawing any conclusion
    (CLAUDE.md I4) — the window is the honest scope of the claim, and a small
    or short window is a reason to state a limitation, not to round it away.
    """
    frame = read_ledger(kind, root)
    if frame.empty:
        return None, None, 0
    timestamp_column = _TIMESTAMP_COLUMN_BY_KIND[kind]
    return (
        frame[timestamp_column].min(),
        frame[timestamp_column].max(),
        len(frame),
    )
