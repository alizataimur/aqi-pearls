#!/usr/bin/env python3
"""Stage 0 probes — answer the open questions in CLAUDE.md §8.2 with data.

Three questions the build must not guess at:

  1. Which CAMS grid cell does each city snap to? Open-Meteo returns the cell
     centre it actually served. If Islamabad and Rawalpindi come back with
     identical coordinates they are ONE forecast zone with two labels, and the
     report must say so instead of presenting them as separate cities.

  2. What is the true earliest CAMS date for these coordinates? Documented as
     "~August 2022" for the global domain, but BACKFILL_START must be set from
     a probe, not from a docs page.

  3. What shape is the AQICN feed? Undocumented and liable to drift, so the
     observed payload is snapshotted to docs/schemas/ and pinned by a contract
     test.

Also snapshots the three Open-Meteo response shapes (air quality, ERA5
archive, historical forecast) to docs/schemas/ — those endpoints are
versioned and stable, but "stable" is an assumption worth pinning too.

Writes its findings to docs/schemas/ and prints a summary you paste into
docs/STATE.md and docs/DECISIONS.md.

    export AQICN_TOKEN=...          # question 3 only
    python scripts/probe_sources.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clock_starter import load_cities, load_dotenv  # noqa: E402  (same directory)

SCHEMAS = REPO_ROOT / "docs" / "schemas"
AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality"


def get_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "pearls-aqi-predictor/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def probe_grid_cells(cities: list[dict[str, Any]]) -> dict[str, Any]:
    """Question 1 — which cell does each city actually resolve to?"""
    results: dict[str, Any] = {}
    for city in cities:
        city_id = str(city["id"])
        url = (
            f"{AIR_QUALITY}?latitude={city['lat']}&longitude={city['lon']}"
            "&hourly=pm2_5&domains=cams_global&forecast_days=1"
        )
        try:
            payload = get_json(url)
            results[city_id] = {
                "requested": [city["lat"], city["lon"]],
                "served_grid": [payload.get("latitude"), payload.get("longitude")],
            }
        except Exception as exc:
            results[city_id] = {"error": str(exc)}

    # The finding that actually matters.
    cells: dict[tuple[Any, Any], list[str]] = {}
    for city_id, entry in results.items():
        grid = entry.get("served_grid")
        if grid:
            cells.setdefault(tuple(grid), []).append(city_id)
    results["_collisions"] = {
        str(list(cell)): ids for cell, ids in cells.items() if len(ids) > 1
    }
    return results


def probe_cams_floor(city: dict[str, Any]) -> dict[str, Any]:
    """Question 2 — binary-search the earliest date that returns real data."""
    lo, hi = date(2020, 1, 1), date.today()

    def has_data(day: date) -> bool:
        url = (
            f"{AIR_QUALITY}?latitude={city['lat']}&longitude={city['lon']}"
            f"&hourly=pm2_5&domains=cams_global"
            f"&start_date={day.isoformat()}&end_date={day.isoformat()}"
        )
        try:
            payload = get_json(url)
        except Exception:
            return False
        values = (payload.get("hourly") or {}).get("pm2_5") or []
        return any(v is not None for v in values)

    if not has_data(hi.replace(day=1)):
        return {"error": "no data even for the current month — check the endpoint"}

    probes = 0
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        probes += 1
        if has_data(mid):
            hi = mid
        else:
            lo = mid

    return {
        "city": city["id"],
        "earliest_date_with_data": hi.isoformat(),
        "probes_used": probes,
        "note": "set sources.backfill_start in conf/config.yaml from this",
    }


def shape(value: Any, depth: int = 0) -> Any:
    """Type skeleton, not values — this is a contract, not a fixture."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def probe_aqicn_schema(cities: list[dict[str, Any]], token: str) -> dict[str, Any]:
    """Question 3 — snapshot the observed payload shape."""
    from aqi.sources.aqicn import fetch_feed

    city = cities[0]
    payload = fetch_feed(float(city["lat"]), float(city["lon"]), token)
    return {
        "probed_city": city["id"],
        "has_forecast_block": bool(
            (payload.get("data", {}) or {}).get("forecast", {}) or {}
        ),
        "forecast_pollutants": sorted(
            ((payload.get("data", {}) or {}).get("forecast", {}) or {})
            .get("daily", {})
            .keys()
        ),
        "schema": shape(payload),
    }


def probe_open_meteo_schemas(city: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the three Open-Meteo response shapes for one city.

    Small requests only (one day) — this is a shape check, not a data pull.
    """
    from aqi.sources.open_meteo_air import fetch_air_quality
    from aqi.sources.open_meteo_hist_forecast import fetch_historical_forecast
    from aqi.sources.open_meteo_weather import fetch_weather_archive

    yesterday = (date.today() - timedelta(days=2)).isoformat()
    today = (date.today() - timedelta(days=1)).isoformat()
    lat, lon = float(city["lat"]), float(city["lon"])

    results: dict[str, Any] = {}
    probes = {
        "air_quality": lambda: fetch_air_quality(lat, lon, past_days=1),
        "weather_archive": lambda: fetch_weather_archive(lat, lon, yesterday, today),
        "historical_forecast": lambda: fetch_historical_forecast(
            lat, lon, yesterday, today
        ),
    }
    for name, probe in probes.items():
        try:
            payload = probe()
            results[name] = shape(payload)
            (SCHEMAS / f"open_meteo_{name}.json").write_text(
                json.dumps(results[name], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            results[name] = {"error": str(exc)}
    return results


def main() -> int:
    load_dotenv()
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    cities = load_cities()
    report: dict[str, Any] = {"probed_on": date.today().isoformat()}

    print("[1/3] probing CAMS grid cells ...")
    report["grid_cells"] = probe_grid_cells(cities)

    print("[2/4] probing CAMS history floor (binary search, ~10 requests) ...")
    report["cams_floor"] = probe_cams_floor(cities[0])

    print("[3/4] probing Open-Meteo response shapes (air, ERA5, hist forecast) ...")
    report["open_meteo_schemas"] = probe_open_meteo_schemas(cities[0])

    token = os.environ.get("AQICN_TOKEN", "").strip()
    if token:
        print("[4/4] probing AQICN feed schema ...")
        try:
            aqicn = probe_aqicn_schema(cities, token)
            report["aqicn"] = aqicn
            (SCHEMAS / "aqicn_feed.json").write_text(
                json.dumps(aqicn["schema"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            report["aqicn"] = {"error": str(exc)}
    else:
        print("[4/4] skipped — AQICN_TOKEN not set")
        report["aqicn"] = {"skipped": "AQICN_TOKEN not set"}

    (SCHEMAS / "probe_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 68)
    collisions = report["grid_cells"].get("_collisions") or {}
    if collisions:
        print("FINDING: cities sharing one CAMS grid cell —")
        for cell, ids in collisions.items():
            print(f"  {cell}: {', '.join(ids)}")
        print("  -> these are ONE forecast zone. Say so in the report (§8.2),")
        print("     and lean on the model-vs-station divergence analysis.")
    else:
        print("FINDING: every city resolved to a distinct CAMS grid cell.")
    floor = report["cams_floor"].get("earliest_date_with_data")
    if floor:
        print(f"\nCAMS history floor: {floor}")
        print("  -> set sources.backfill_start in conf/config.yaml to this date.")
    print("=" * 68)
    print(f"\nFull report: {SCHEMAS / 'probe_report.json'}")
    print("Paste the findings into docs/STATE.md and docs/DECISIONS.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
