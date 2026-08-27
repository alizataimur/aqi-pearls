"""Shared GET-with-retry helper for the Open-Meteo sources.

Stdlib only, matching `aqicn.py`'s approach: a dependency resolution problem
must never be able to block a capture (CLAUDE.md §6). Exponential backoff plus
jitter, per CLAUDE.md §8.3. `aqicn.py` keeps its own copy of this logic
because it shipped at Stage 0 before this module existed and its retry/verify
path is tested and load-bearing (ADR-007) — not worth touching to deduplicate
a dozen lines.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any


class SourceError(RuntimeError):
    """Raised when a source cannot be retrieved after retries."""


def get_json(
    url: str,
    *,
    attempts: int = 4,
    base_delay: float = 1.5,
    max_delay: float = 30.0,
    timeout: float = 30.0,
    user_agent: str = "pearls-aqi-predictor/0.1",
) -> dict[str, Any]:
    """GET `url` and decode JSON, retrying transient failures.

    Raises :class:`SourceError` after the final attempt — never crashes the
    caller's process (I10); the caller decides whether one failed request
    should end the run.
    """
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))  # type: ignore[no-any-return]
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

    raise SourceError(f"GET {url} failed after {attempts} attempts: {last_error}")
