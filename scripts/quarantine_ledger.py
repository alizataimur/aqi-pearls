#!/usr/bin/env python3
"""Move pre-ADR-007 rows out of the ledger, without destroying them.

The clock-starter ran once successfully on 2026-08-27 *before* station pinning
landed. That run captured a Delhi station (Pooth Khurd, Bawana) and wrote it to
the ledger labelled `islamabad`, `rawalpindi` and `lahore` — both the station
observation and, invisibly, AQICN's forecast for the wrong city.

`verify_station` stops new bad rows. It cannot remove old ones.

I3 says never rewrite ledger history. I4 says never claim a capture you did not
make. A mislabelled row is not a capture, so the resolution is to move it out of
the ledger rather than delete it: quarantined rows stay on disk as evidence of
what happened and remain auditable, but nothing downstream reads them.

    python scripts/quarantine_ledger.py            # report only
    python scripts/quarantine_ledger.py --apply    # move them
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "data" / "ledger"
QUARANTINE = LEDGER / "_quarantine"

# The ADR-007 fix went green as workflow_dispatch run #18. Everything captured
# before this instant predates pinned stations and cannot be vouched for.
CUTOFF = datetime(2026, 8, 31, 14, 40, tzinfo=timezone.utc)

# Cities the clock starter must never write, because they have no pinned station.
UNPINNED = {"rawalpindi"}


def parse_captured(row: dict) -> datetime | None:
    raw = row.get("captured_at_utc")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(path: Path, row: dict) -> str | None:
    """Return a reason string if the row should be quarantined, else None."""
    city = path.parent.name
    if city in UNPINNED:
        return f"{city} has no pinned station — this row cannot be attributed"

    captured = parse_captured(row)
    if captured is None:
        return "no captured_at_utc — cannot establish provenance"
    if captured < CUTOFF:
        return f"captured {captured.isoformat()}, before the ADR-007 fix"

    station = row.get("station_name")
    if station and "Pakistan" not in station:
        return f"station {station!r} is not in Pakistan"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    files = sorted(p for p in LEDGER.rglob("*.jsonl") if "_quarantine" not in p.parts)
    if not files:
        print("no ledger files found")
        return 0

    total_kept = total_moved = 0

    for path in files:
        kept: list[str] = []
        moved: list[tuple[str, str]] = []

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                moved.append((line, "unparseable"))
                continue
            reason = classify(path, row)
            (kept.append(line) if reason is None else moved.append((line, reason)))

        rel = path.relative_to(REPO_ROOT)
        total_kept += len(kept)
        total_moved += len(moved)

        if not moved:
            print(f"  ok      {rel}  ({len(kept)} rows)")
            continue

        print(f"  QUARANTINE {rel}  keep {len(kept)}, move {len(moved)}")
        for _, reason in moved:
            print(f"            └─ {reason}")

        if not args.apply:
            continue

        dest = QUARANTINE / path.relative_to(LEDGER)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as handle:
            for line, reason in moved:
                handle.write(
                    json.dumps(
                        {
                            "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
                            "reason": reason,
                            "original_row": json.loads(line),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if kept:
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            # An empty ledger file is misleading — it looks like a captured gap.
            path.unlink()
            print(f"            └─ removed {rel} (nothing left to keep)")

    print(f"\n{total_kept} rows kept, {total_moved} quarantined")
    if not args.apply:
        print("dry run — nothing written. Re-run with --apply to move them.")
    else:
        print(f"moved rows are in {QUARANTINE.relative_to(REPO_ROOT)}, still auditable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
