"""Read-only access to the forecast ledger (CLAUDE.md §11.3, I3).

Append-only JSONL under `data/ledger/{observed,aqicn}/<city>/<YYYY-MM>.jsonl`.
`_quarantine/` holds rows `scripts/quarantine_ledger.py` classified as
unattributable (ADR-007) — this reader always excludes it, so "read the
ledger" never silently includes a mislabelled row back into an analysis.

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

LedgerKind = Literal["observed", "aqicn"]

CAPTURED_AT_COLUMN = "captured_at_utc"


def _iter_files(kind: LedgerKind, root: Path) -> list[Path]:
    kind_root = root / kind
    if not kind_root.exists():
        return []
    return sorted(p for p in kind_root.rglob("*.jsonl") if "_quarantine" not in p.parts)


def read_ledger(kind: LedgerKind, root: Path = LEDGER_ROOT) -> pd.DataFrame:
    """Every non-quarantined row of one ledger stream, as a flat DataFrame.

    One row per JSON line. Nested fields (`iaqi`, `forecast`) are left as
    dict/list objects in their own column rather than pre-flattened — the two
    streams have different nested shapes, and a caller reaching for a specific
    nested value is better served doing that explicitly than trusting a
    guessed flattening here.
    """
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
    frame[CAPTURED_AT_COLUMN] = pd.to_datetime(frame[CAPTURED_AT_COLUMN], utc=True)
    return frame.sort_values(CAPTURED_AT_COLUMN).reset_index(drop=True)


def ledger_window(
    kind: LedgerKind, root: Path = LEDGER_ROOT
) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    """`(earliest, latest, row_count)` over `captured_at_utc` for one stream.

    Every ledger analysis must print this before drawing any conclusion
    (CLAUDE.md I4) — the window is the honest scope of the claim, and a small
    or short window is a reason to state a limitation, not to round it away.
    """
    frame = read_ledger(kind, root)
    if frame.empty:
        return None, None, 0
    return (
        frame[CAPTURED_AT_COLUMN].min(),
        frame[CAPTURED_AT_COLUMN].max(),
        len(frame),
    )
