#!/usr/bin/env python3
"""Day 0 — start the clock. See CLAUDE.md §6.

Three things in this project accrue in wall-clock time and cannot be bought
back at any price:

  * AQICN's published forecasts   — no free history endpoint
  * this project's own forecasts  — same
  * pipeline uptime               — D8 needs >= 7 consecutive green days

Everything else can be crashed with effort. These cannot. So this script exists
before the feature store, before any model, before the dashboard, and depends
on none of them. Its only inputs are conf/cities.yaml and AQICN_TOKEN.

Runs HOURLY, not daily. AQICN publishes no observation history, so a once-a-day
snapshot cannot reconstruct a daily maximum — and daily max is the primary
target (CLAUDE.md §9.1). The forecast block is captured on the same schedule
and deduplicated by content hash, so hourly costs ~24 small appends a day.

    python scripts/clock_starter.py            # capture now
    python scripts/clock_starter.py --dry-run  # show what would be written

Exit codes: 0 if at least one city was captured, 1 if all cities failed.
A partial capture is a success — a permanent gap is the only real failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqi.sources.aqicn import (  # noqa: E402
    AQICNError,
    extract_forecast,
    extract_observation,
    fetch_feed,
    verify_station,
)

LEDGER = REPO_ROOT / "data" / "ledger"
RAW = REPO_ROOT / "data" / "raw" / "aqicn"


def load_dotenv() -> None:
    """Load `.env` into the environment if present, with no dependency.

    Day 0 must run on a bare Python on any platform. `make` handles this on
    Linux and macOS but does not exist on Windows, and the capture must not
    depend on which shell someone happens to use.

    Variables already set in the environment win, so CI — which supplies
    secrets through GitHub Secrets and ships no `.env` — is unaffected.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def _parse_scalar(raw: str) -> Any:
    """Parse one YAML scalar the way `yaml.safe_load` would.

    The fallback parser and PyYAML MUST agree exactly. When they did not, CI ran
    a different code path from the developer's laptop and the clock starter
    failed for 16 consecutive scheduled runs while passing locally — quoted
    station ids came back as `'"@11739"'`, quote characters included, producing
    a nonsense URL (ADR-012). `tests/test_yaml_fallback.py` now pins the
    agreement.
    """
    value = raw.strip()
    if value in ("", "null", "~"):
        return None
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    # Quoted scalar: strip the quotes, exactly as a YAML loader does.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_parse_scalar(part) for part in inner.split(",")] if inner else []
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_cities() -> list[dict[str, Any]]:
    """Read conf/cities.yaml without requiring PyYAML at Day 0.

    Day 0 must run on a bare Python: a dependency resolution problem must never
    be able to block a capture whose losses are permanent (CLAUDE.md §6). If
    PyYAML is present we use it; otherwise the minimal parser below handles this
    file's flat list-of-mappings shape and must produce an identical result.
    """
    path = REPO_ROOT / "conf" / "cities.yaml"
    text = path.read_text(encoding="utf-8")

    try:
        import yaml  # type: ignore[import-untyped]

        return list(yaml.safe_load(text)["cities"])
    except ImportError:
        pass

    cities: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        # Strip comments, but only a '#' at line start or after whitespace, so a
        # '#' inside a quoted value survives.
        line = re.split(r"(?:^|\s)#", raw_line, maxsplit=1)[0].rstrip()
        if not line.strip() or line.strip() == "cities:":
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                cities.append(current)
            current = {}
            stripped = stripped[2:]
        if current is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        current[key.strip()] = _parse_scalar(value)
    if current:
        cities.append(current)
    return cities


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def already_recorded(path: Path, digest: str) -> bool:
    """True if this exact forecast payload was already captured today."""
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                if json.loads(line).get("payload_sha1") == digest:
                    return True
            except json.JSONDecodeError:
                continue
    return False


def capture_city(city: dict[str, Any], token: str, now: datetime, dry_run: bool) -> bool:
    city_id = str(city.get("id", "unknown"))
    month = now.strftime("%Y-%m")

    station = city.get("aqicn_station")
    if not station:
        raise ValueError(
            f"{city_id} has no aqicn_station pinned in conf/cities.yaml. "
            "Run scripts/diagnose_aqicn.py to find one — geo: lookup is "
            "forbidden (ADR-007)."
        )

    payload = fetch_feed(str(station), token)
    # Refuse to write a station that is not where the config says it is. The
    # failure this catches returns status "ok" and a plausible AQI, so nothing
    # downstream would notice (ADR-007).
    distance_km = verify_station(payload, float(city["lat"]), float(city["lon"]))

    # Keep the untouched response. A transform bug must never mean a lost
    # capture, and the schema is not reliably documented (CLAUDE.md §8.2).
    if not dry_run:
        raw_path = RAW / city_id / f"{now.strftime('%Y-%m-%d')}.jsonl"
        append_jsonl(raw_path, {"captured_at_utc": now.isoformat(), "payload": payload})

    observation = extract_observation(payload)
    observation_record = {
        "captured_at_utc": now.isoformat(),
        "city_id": city_id,
        "station_id": str(station),
        "station_distance_km": round(distance_km, 2),
        **observation,
    }

    forecast = extract_forecast(payload)
    digest = (
        hashlib.sha1(json.dumps(forecast, sort_keys=True).encode("utf-8")).hexdigest()
        if forecast is not None
        else None
    )

    if dry_run:
        print(
            json.dumps(
                {"observation": observation_record, "forecast_sha1": digest},
                ensure_ascii=False,
                indent=2,
            )
        )
        return True

    append_jsonl(LEDGER / "observed" / city_id / f"{month}.jsonl", observation_record)

    forecast_path = LEDGER / "aqicn" / city_id / f"{month}.jsonl"
    if forecast is None:
        # Record the gap explicitly rather than letting silence look like data.
        append_jsonl(
            forecast_path,
            {
                "captured_at_utc": now.isoformat(),
                "city_id": city_id,
                "payload_sha1": None,
                "forecast": None,
                "note": "no forecast block in feed",
            },
        )
    elif not already_recorded(forecast_path, str(digest)):
        append_jsonl(
            forecast_path,
            {
                "captured_at_utc": now.isoformat(),
                "city_id": city_id,
                "payload_sha1": digest,
                "forecast": forecast,
            },
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("AQICN_TOKEN", "").strip()
    if not token:
        print(
            "AQICN_TOKEN is not set. Get a free token at "
            "https://aqicn.org/data-platform/token/ and export it.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(UTC).replace(microsecond=0)
    cities = load_cities()
    if not cities:
        print("no cities configured in conf/cities.yaml", file=sys.stderr)
        return 1

    captured, failed, skipped = [], [], []
    for city in cities:
        city_id = str(city.get("id", "unknown"))
        if not city.get("aqicn_station"):
            # Deliberate, not a failure: a city with no pinned station is
            # skipped rather than handed a neighbour's instrument. Kept out of
            # `failed` so it cannot mask a real outage.
            skipped.append(city_id)
            continue
        try:
            capture_city(city, token, now, args.dry_run)
            captured.append(city_id)
        except (AQICNError, KeyError, ValueError, OSError) as exc:
            # One city failing must never abort the others (CLAUDE.md §8.3).
            failed.append(city_id)
            print(f"[warn] {city_id}: {exc}", file=sys.stderr)

    print(
        json.dumps(
            {
                "captured_at_utc": now.isoformat(),
                "captured": captured,
                "failed": failed,
                "skipped": skipped,
                "dry_run": args.dry_run,
            }
        )
    )

    if not captured:
        print(
            "[error] every city failed — the ledger has a permanent gap for this hour",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
