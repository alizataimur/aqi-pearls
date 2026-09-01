#!/usr/bin/env python3
"""Separate unattributable ledger rows from genuine ones, in both directions.

Background. The clock-starter ran once on 2026-08-27 before station pinning
landed (ADR-007) and captured a Delhi station, writing it to the ledger as
`islamabad`, `rawalpindi` and `lahore` — both the observation and, invisibly,
AQICN's forecast. `verify_station` stops new bad rows; it cannot remove old ones.

I3 says never rewrite ledger history. I4 says never claim a capture you did not
make. A mislabelled row is not a capture, so bad rows are *moved*, not deleted:
they stay under `_quarantine/` with a reason, and remain auditable.

**Classify on evidence, not on time.** An earlier version of this script used the
moment CI was fixed as the cutoff, which quarantined perfectly good captures made
locally before then — where PyYAML was installed and the stations always parsed
correctly. Capture time is a proxy; the station name and the payload hash are the
actual evidence. Time is now only a last resort for rows carrying neither.

    python scripts/quarantine_ledger.py            # report
    python scripts/quarantine_ledger.py --apply    # move bad rows out
    python scripts/quarantine_ledger.py --restore  # bring good rows back
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "data" / "ledger"
QUARANTINE = LEDGER / "_quarantine"

# Last-resort only, for rows with no station name and no payload hash.
CUTOFF = datetime(2026, 8, 31, 14, 40, tzinfo=UTC)

# Cities the clock starter must never write, having no pinned station.
UNPINNED = {"rawalpindi"}

COUNTRY = "Pakistan"


def city_of(path: Path) -> str:
    return path.parent.name


def kind_of(path: Path) -> str:
    """'observed' or 'aqicn' — the ledger stream this file belongs to."""
    parts = path.parts
    for marker in ("observed", "aqicn"):
        if marker in parts:
            return marker
    return "unknown"


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_unparseable": line})
    return rows


def build_hash_index() -> dict[str, set[str]]:
    """Map each AQICN payload hash to the set of cities it was filed under.

    A forecast served for one city and stored under three is the signature of
    the geo-lookup contamination: identical payload, different city labels.
    """
    index: dict[str, set[str]] = defaultdict(set)
    for path in LEDGER.rglob("*.jsonl"):
        if kind_of(path) != "aqicn":
            continue
        quarantined = "_quarantine" in path.parts
        for row in read_rows(path):
            source = row.get("original_row", row) if quarantined else row
            digest = source.get("payload_sha1")
            if digest:
                index[digest].add(city_of(path))
    return index


def classify(path: Path, row: dict[str, Any], hashes: dict[str, set[str]]) -> str | None:
    """Reason to quarantine, or None to keep. Evidence first, time last."""
    if row.get("_unparseable"):
        return "unparseable line"

    city = city_of(path)
    if city in UNPINNED:
        return f"{city} has no pinned station — this row cannot be attributed"

    # Observed rows carry the station they came from. That is decisive.
    station = row.get("station_name")
    if station:
        if COUNTRY in station:
            return None
        return f"station {station!r} is not in {COUNTRY}"

    # Forecast rows carry no station. A payload filed under more than one city
    # is the same forecast served for all of them — contamination.
    digest = row.get("payload_sha1")
    if digest:
        cities = hashes.get(digest, set())
        if len(cities) > 1:
            return (
                f"payload {digest[:8]} is filed under {len(cities)} cities "
                f"({', '.join(sorted(cities))}) — one forecast cannot describe all"
            )
        return None
    if "forecast" in row and row.get("forecast") is None:
        return None  # a recorded gap; legitimate

    # No station, no hash: fall back to provenance in time.
    raw = row.get("captured_at_utc")
    if not isinstance(raw, str):
        return "no station, no payload hash, no timestamp — provenance unknown"
    try:
        captured = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"unparseable captured_at_utc {raw!r}"
    if captured < CUTOFF:
        return f"no station or hash to check, and captured {raw} (pre-ADR-007)"
    return None


def sort_key(row: dict[str, Any]) -> str:
    return str(row.get("captured_at_utc", ""))


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True)
        for r in sorted(rows, key=sort_key)
    )
    path.write_text(body + "\n", encoding="utf-8")


def do_quarantine(apply: bool) -> tuple[int, int]:
    hashes = build_hash_index()
    kept_total = moved_total = 0

    for path in sorted(LEDGER.rglob("*.jsonl")):
        if "_quarantine" in path.parts:
            continue
        kept, moved = [], []
        for row in read_rows(path):
            reason = classify(path, row, hashes)
            (kept.append(row) if reason is None else moved.append((row, reason)))

        rel = path.relative_to(REPO_ROOT)
        kept_total += len(kept)
        moved_total += len(moved)
        if not moved:
            print(f"  ok         {rel}  ({len(kept)} rows)")
            continue

        print(f"  QUARANTINE {rel}  keep {len(kept)}, move {len(moved)}")
        for _, reason in moved:
            print(f"             └─ {reason}")
        if not apply:
            continue

        dest = QUARANTINE / path.relative_to(LEDGER)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as handle:
            for row, reason in moved:
                handle.write(
                    json.dumps(
                        {
                            "quarantined_at_utc": datetime.now(UTC).isoformat(),
                            "reason": reason,
                            "original_row": row,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if kept:
            write_rows(path, kept)
        else:
            path.unlink()
            print(f"             └─ removed {rel} (nothing left to keep)")
    return kept_total, moved_total


def do_restore(apply: bool) -> int:
    """Return rows the corrected classifier says should never have been moved."""
    hashes = build_hash_index()
    restored_total = 0

    for qpath in sorted(QUARANTINE.rglob("*.jsonl")):
        rel_within = qpath.relative_to(QUARANTINE)
        ledger_path = LEDGER / rel_within
        stay, restore = [], []

        for entry in read_rows(qpath):
            row = entry.get("original_row", entry)
            # Classify against the LEDGER path, so city/kind resolve correctly.
            reason = classify(ledger_path, row, hashes)
            (stay.append(entry) if reason is not None else restore.append(row))

        if not restore:
            continue

        restored_total += len(restore)
        print(f"  RESTORE    {ledger_path.relative_to(REPO_ROOT)}  {len(restore)} row(s)")
        for row in restore:
            label = row.get("station_name") or (row.get("payload_sha1") or "")[:8]
            print(f"             └─ {row.get('captured_at_utc')}  {label}")
        if not apply:
            continue

        existing = read_rows(ledger_path) if ledger_path.exists() else []
        write_rows(ledger_path, existing + restore)
        if stay:
            write_rows(qpath, stay)
        else:
            qpath.unlink()
    return restored_total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    parser.add_argument(
        "--restore", action="store_true", help="bring wrongly-quarantined rows back"
    )
    args = parser.parse_args()

    if args.restore:
        n = do_restore(args.apply)
        print(f"\n{n} row(s) to restore" if n else "\nnothing to restore")
    else:
        kept, moved = do_quarantine(args.apply)
        print(f"\n{kept} rows kept, {moved} quarantined")

    if not args.apply:
        print("dry run — nothing written. Add --apply to commit the change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
