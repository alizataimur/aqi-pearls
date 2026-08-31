"""AQICN / WAQI station feed.

Two jobs in this project, and they have very different urgency:

1. **Ground truth** — real instrument readings, the benchmark's `y_true`.
2. **The incumbent forecast** — AQICN publishes its own multi-day forecast, and
   that is the thing this project is measured against.

Neither is retrievable historically. AQICN offers no free history endpoint, so
whatever is not captured at the time is gone permanently (CLAUDE.md I3). That
is why this module has no dependency on the feature store, the model registry
or any pipeline: it must be able to run on day one, before any of them exist.

The response shape is **not reliably documented**. Nothing here indexes into
the payload beyond the two fields the API contract guarantees (`status`,
`data`); everything else is carried through raw and parsed downstream, so a
schema change degrades one field rather than crashing the capture.
Run `scripts/probe_sources.py` to snapshot the observed shape into
`docs/schemas/aqicn_feed.json`, and let `tests/test_schemas.py` fail loudly
when it drifts.
"""

from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE = "https://api.waqi.info/feed"

# How far a returned station may sit from its configured city before the
# capture is rejected. Generous enough for a metro-area station, far tighter
# than the ~690 km miss that ADR-007 was written about.
MAX_STATION_DRIFT_KM = 60.0


class AQICNError(RuntimeError):
    """Raised when the feed cannot be retrieved or reports a non-ok status."""


class StationMismatchError(AQICNError):
    """The feed returned a station that is not where the config says it is."""


def fetch_feed(
    station: str,
    token: str,
    *,
    base_url: str = DEFAULT_BASE,
    attempts: int = 4,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch a **pinned** station feed.

    ``station`` is an AQICN identifier: ``"@11739"`` for a station index, or a
    city slug such as ``"islamabad"``. Never a ``geo:`` lookup — see ADR-007.
    Nearest-station lookup returned a Delhi station for all three Pakistani
    cities (AQICN appears to fall back to IP geolocation and ignore the
    coordinates), and even when it works it is unstable: a station added
    nearby silently switches the instrument mid-series, which quietly
    invalidates every comparison built on the ledger.

    Retries with exponential backoff plus jitter. Raises :class:`AQICNError`
    after the final attempt — the caller decides whether one city failing
    should end the run (it should not; see CLAUDE.md §8.3).
    """
    if not token:
        raise AQICNError("AQICN_TOKEN is empty — set it in the environment")
    if station.startswith("geo:"):
        raise AQICNError(
            f"geo: lookups are forbidden (ADR-007), got {station!r}. "
            "Pin a station id such as '@11739' in conf/cities.yaml."
        )
    # A malformed identifier produces a nonsense URL and a confusing 404 rather
    # than a clear failure. This turned 16 consecutive red runs into a token
    # hunt when the real cause was a config parser leaving quote characters on
    # the value (ADR-012).
    if any(ch in station for ch in "\"' \t") or not station:
        raise AQICNError(
            f"malformed station identifier {station!r} — expected something like "
            "'@11739' or 'islamabad', with no quotes or whitespace. Check how "
            "conf/cities.yaml was parsed."
        )

    url = f"{base_url}/{station}/?token={token}"
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "pearls-aqi-predictor/0.1"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                # json.loads is typed Any; the annotation is what lets this
                # function honestly claim a dict return under mypy --strict.
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))

            status = payload.get("status")
            if status != "ok":
                # A bad token or an unknown station is not worth retrying.
                raise AQICNError(f"feed returned status={status!r}: {payload}")
            if "data" not in payload:
                raise AQICNError("feed response has no 'data' key")
            return payload

        except AQICNError:
            raise
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            delay = min(base_delay * (2**attempt), max_delay)
            time.sleep(delay + random.uniform(0, delay * 0.3))

    raise AQICNError(f"failed after {attempts} attempts: {last_error}")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def verify_station(
    payload: dict[str, Any],
    expected_lat: float,
    expected_lon: float,
    *,
    max_km: float = MAX_STATION_DRIFT_KM,
) -> float:
    """Assert the station we received is the station we asked for.

    Every capture is checked, because the failure this guards against is
    silent and unrecoverable: a feed that returns the wrong station still
    returns `status: "ok"` and a plausible AQI, and by the time anyone notices,
    weeks of ledger rows are labelled with a city they never described. AQICN
    has no history endpoint, so those hours cannot be re-fetched (I3).

    Returns the distance in km so the caller can log it. Raises
    :class:`StationMismatchError` past ``max_km``.
    """
    city = (payload.get("data", {}) or {}).get("city", {}) or {}
    geo = city.get("geo")
    if not (isinstance(geo, list) and len(geo) >= 2):
        raise StationMismatchError(
            f"feed carries no station coordinates to verify: {city.get('name')!r}"
        )

    distance = _haversine_km(expected_lat, expected_lon, float(geo[0]), float(geo[1]))
    if distance > max_km:
        raise StationMismatchError(
            f"station {city.get('name')!r} at {geo} is {distance:.0f} km from the "
            f"configured location ({expected_lat}, {expected_lon}) — refusing to "
            "write it to the ledger"
        )
    return distance


def extract_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the current reading out of a feed payload, defensively.

    Returns whatever is present. Missing fields become ``None`` rather than
    raising: a partial observation is still worth recording, and a hard failure
    here would cost a permanent gap in the ledger.
    """
    data = payload.get("data", {}) or {}
    iaqi = data.get("iaqi", {}) or {}

    def value(key: str) -> float | None:
        entry = iaqi.get(key)
        if isinstance(entry, dict):
            v = entry.get("v")
            return float(v) if isinstance(v, int | float) else None
        return None

    time_block = data.get("time", {}) or {}
    city_block = data.get("city", {}) or {}
    station_aqi = data.get("aqi")

    return {
        "station_aqi": station_aqi if isinstance(station_aqi, int | float) else None,
        "dominant_pollutant": data.get("dominentpol"),
        "station_name": city_block.get("name"),
        "station_geo": city_block.get("geo"),
        "observed_at_iso": time_block.get("iso"),
        "iaqi": {k: value(k) for k in ("pm25", "pm10", "no2", "so2", "o3", "co")},
    }


def extract_forecast(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the published daily forecast block, defensively.

    This is the incumbent benchmark (CLAUDE.md §12.1 rung 0d). Returns ``None``
    when the feed carries no forecast for this station — which happens, and is
    itself worth recording as a gap rather than treated as an error.

    NOTE: AQICN's forecast values are AQI already, not concentrations, and are
    computed with AQICN's own conversion. Comparing them to our AQI mixes a
    forecast difference with a conversion difference — I8 requires the report
    to separate the two.
    """
    data = payload.get("data", {}) or {}
    forecast = (data.get("forecast", {}) or {}).get("daily")
    if not isinstance(forecast, dict) or not forecast:
        return None
    return forecast
