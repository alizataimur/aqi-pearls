"""US EPA Air Quality Index conversion.

Two named entry points, never one ambiguous ``aqi()`` — see CLAUDE.md I8:

    aqi_from_24h_mean(...)   daily AQI, from a 24-hour average concentration
    aqi_nowcast(...)         real-time AQI, from the EPA NowCast weighting of
                             the last 12 hourly concentrations

Breakpoints are the **EPA 2024 revision**, transcribed from the authoritative
AQS code table on 2026-08-27:
https://aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.html

Record that revision in the report. Providers do not all use the same one, and
the difference appears as a systematic offset between our AQI, Open-Meteo's
``us_aqi`` and AQICN's value. When comparing, always separate a *forecast*
difference from a *conversion* difference (CLAUDE.md I8).

Note the 501-999 band. Most implementations clip at AQI 500; Punjab smog
episodes genuinely exceed it, and clipping destroys signal in precisely the
regime this project exists to forecast.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "AQIResult",
    "Pollutant",
    "aqi_from_24h_mean",
    "aqi_nowcast",
    "category_for",
    "overall_aqi",
    "ugm3_to_epa_units",
]


# --------------------------------------------------------------------------
# Breakpoints
# --------------------------------------------------------------------------
# Each row: (C_low, C_high, I_low, I_high)
# Concentration units per pollutant are given in _UNITS below. EPA defines the
# gas indices in ppm/ppb, NOT ug/m3 — see ugm3_to_epa_units().

_BREAKPOINTS: dict[str, tuple[tuple[float, float, int, int], ...]] = {
    # PM2.5, 24-hour average, ug/m3 (2024 revision)
    "pm2_5": (
        (0.0, 9.0, 0, 50),
        (9.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150),
        (55.5, 125.4, 151, 200),
        (125.5, 225.4, 201, 300),
        (225.5, 325.4, 301, 500),
        (325.5, 99999.9, 501, 999),
    ),
    # PM10, 24-hour average, ug/m3
    "pm10": (
        (0.0, 54.0, 0, 50),
        (55.0, 154.0, 51, 100),
        (155.0, 254.0, 101, 150),
        (255.0, 354.0, 151, 200),
        (355.0, 424.0, 201, 300),
        (425.0, 604.0, 301, 500),
        (605.0, 99999.9, 501, 999),
    ),
    # Ozone, 8-hour average, ppm
    "o3": (
        (0.000, 0.054, 0, 50),
        (0.055, 0.070, 51, 100),
        (0.071, 0.085, 101, 150),
        (0.086, 0.105, 151, 200),
        (0.106, 0.200, 201, 300),
    ),
    # NO2, 1-hour, ppb
    "no2": (
        (0, 53, 0, 50),
        (54, 100, 51, 100),
        (101, 360, 101, 150),
        (361, 649, 151, 200),
        (650, 1249, 201, 300),
        (1250, 2049, 301, 500),
    ),
    # SO2, 1-hour, ppb
    "so2": (
        (0, 35, 0, 50),
        (36, 75, 51, 100),
        (76, 185, 101, 150),
        (186, 304, 151, 200),
        (305, 604, 201, 300),
        (605, 1004, 301, 500),
    ),
    # CO, 8-hour, ppm
    "co": (
        (0.0, 4.4, 0, 50),
        (4.5, 9.4, 51, 100),
        (9.5, 12.4, 101, 150),
        (12.5, 15.4, 151, 200),
        (15.5, 30.4, 201, 300),
        (30.5, 50.4, 301, 500),
    ),
}

Pollutant = str

_UNITS: dict[str, str] = {
    "pm2_5": "ug/m3",
    "pm10": "ug/m3",
    "o3": "ppm",
    "no2": "ppb",
    "so2": "ppb",
    "co": "ppm",
}

# EPA truncation: concentrations are truncated (not rounded) to this many
# decimal places before the index is computed. TAD Table 4.
_TRUNCATION: dict[str, int] = {
    "pm2_5": 1,
    "pm10": 0,
    "o3": 3,
    "no2": 0,
    "so2": 0,
    "co": 1,
}

_CATEGORIES: tuple[tuple[int, str, str], ...] = (
    (50, "Good", "بہتر"),
    (100, "Moderate", "درمیانہ"),
    (150, "Unhealthy for Sensitive Groups", "حساس افراد کے لیے مضر"),
    (200, "Unhealthy", "مضر صحت"),
    (300, "Very Unhealthy", "انتہائی مضر صحت"),
    (10_000, "Hazardous", "خطرناک"),
)

# Molecular weights (g/mol) for the ug/m3 -> ppm/ppb conversion.
_MOLECULAR_WEIGHT: dict[str, float] = {
    "o3": 48.00,
    "no2": 46.0055,
    "so2": 64.066,
    "co": 28.010,
}

# Molar volume of an ideal gas at EPA reference conditions (25 C, 1 atm), L/mol.
_MOLAR_VOLUME_L = 24.45


@dataclass(frozen=True)
class AQIResult:
    """An AQI value plus everything needed to explain or audit it."""

    aqi: int
    pollutant: Pollutant
    concentration: float
    """The (truncated) concentration the index was computed from, in EPA units."""
    category: str
    category_ur: str
    method: str
    """Either ``24h_mean`` or ``nowcast`` — never leave this ambiguous."""

    @property
    def exceeds_scale(self) -> bool:
        """True when AQI > 500, i.e. beyond the conventional published scale."""
        return self.aqi > 500


def ugm3_to_epa_units(value: float, pollutant: Pollutant) -> float:
    """Convert a CAMS concentration in ug/m3 to the units EPA's index expects.

    PM2.5 and PM10 are already in ug/m3 and pass through unchanged. Gases are
    converted to ppm (o3, co) or ppb (no2, so2) at 25 C and 1 atm.

    This conversion is a documented source of divergence from providers who
    apply the index directly to ug/m3. Getting it wrong silently biases every
    gas sub-index, so it lives here and nowhere else.
    """
    if pollutant in ("pm2_5", "pm10"):
        return value
    if pollutant not in _MOLECULAR_WEIGHT:
        raise ValueError(f"unknown pollutant: {pollutant!r}")

    ppb = value * _MOLAR_VOLUME_L / _MOLECULAR_WEIGHT[pollutant]
    if _UNITS[pollutant] == "ppm":
        return ppb / 1000.0
    return ppb


def _truncate(value: float, places: int) -> float:
    factor = 10**places
    return math.floor(value * factor) / factor


def _index_from_concentration(conc: float, pollutant: Pollutant) -> int:
    try:
        table = _BREAKPOINTS[pollutant]
    except KeyError as exc:  # pragma: no cover - guarded by callers
        raise ValueError(f"no AQI breakpoints for {pollutant!r}") from exc

    if conc < 0:
        raise ValueError(f"negative concentration for {pollutant}: {conc}")

    c = _truncate(conc, _TRUNCATION[pollutant])

    for c_low, c_high, i_low, i_high in table:
        if c <= c_high:
            c_low = max(c_low, 0.0)
            if c_high == c_low:  # pragma: no cover - defensive
                return i_low
            index = (i_high - i_low) / (c_high - c_low) * (c - c_low) + i_low
            return round(index)

    # Above the highest defined breakpoint: report the top of the scale rather
    # than raising. The caller can see exceeds_scale on the result.
    return table[-1][3]


def category_for(aqi: int) -> tuple[str, str]:
    """Return (english, urdu) category names for an AQI value."""
    for upper, name, name_ur in _CATEGORIES:
        if aqi <= upper:
            return name, name_ur
    return _CATEGORIES[-1][1], _CATEGORIES[-1][2]  # pragma: no cover


def aqi_from_24h_mean(
    concentration: float,
    pollutant: Pollutant = "pm2_5",
    *,
    units: str = "ug/m3",
) -> AQIResult:
    """Daily AQI from a 24-hour average concentration.

    This is the definition behind the ``daily_episode`` target family and the
    only correct one for a *daily* value. Do not feed it a single hourly
    reading — use :func:`aqi_nowcast` for that.

    Args:
        concentration: 24-hour mean concentration.
        pollutant: one of pm2_5, pm10, o3, no2, so2, co.
        units: ``ug/m3`` (CAMS native, converted here) or ``epa`` if the value
            is already in the pollutant's EPA unit.
    """
    conc = (
        ugm3_to_epa_units(concentration, pollutant) if units == "ug/m3" else concentration
    )
    index = _index_from_concentration(conc, pollutant)
    name, name_ur = category_for(index)
    return AQIResult(
        aqi=index,
        pollutant=pollutant,
        concentration=_truncate(conc, _TRUNCATION[pollutant]),
        category=name,
        category_ur=name_ur,
        method="24h_mean",
    )


def aqi_nowcast(
    hourly: Sequence[float | None],
    pollutant: Pollutant = "pm2_5",
    *,
    units: str = "ug/m3",
) -> AQIResult | None:
    """Real-time AQI via the EPA NowCast weighting.

    ``hourly`` is ordered **most recent first** and may contain ``None`` for
    missing hours. At most the latest 12 values are used.

    The NowCast weights recent hours more heavily when concentrations are
    changing fast, which is why a raw hourly reading is not a valid "current
    AQI". Returns ``None`` when EPA's data-sufficiency rule fails: at least two
    of the three most recent hours must be present.
    """
    window = list(hourly[:12])
    if len(window) < 3:
        return None
    if sum(1 for v in window[:3] if v is not None) < 2:
        return None

    values = [
        (ugm3_to_epa_units(v, pollutant) if units == "ug/m3" else v)
        for v in window
        if v is not None
    ]
    if not values:
        return None

    c_min, c_max = min(values), max(values)
    weight = 1.0 if c_max <= 0 else 1.0 - (c_max - c_min) / c_max
    weight = max(weight, 0.5)  # EPA floors the weight at 1/2

    numerator = 0.0
    denominator = 0.0
    for i, value in enumerate(window):
        if value is None:
            continue
        v = ugm3_to_epa_units(value, pollutant) if units == "ug/m3" else value
        numerator += (weight**i) * v
        denominator += weight**i

    nowcast_conc = numerator / denominator
    index = _index_from_concentration(nowcast_conc, pollutant)
    name, name_ur = category_for(index)
    return AQIResult(
        aqi=index,
        pollutant=pollutant,
        concentration=_truncate(nowcast_conc, _TRUNCATION[pollutant]),
        category=name,
        category_ur=name_ur,
        method="nowcast",
    )


def overall_aqi(results: Iterable[AQIResult | None]) -> AQIResult | None:
    """Overall AQI is the maximum sub-index, and it reports which pollutant won.

    The driving pollutant is worth carrying through to the dashboard: "AQI 210,
    driven by PM2.5" is actionable in a way that a bare number is not.
    """
    present = [r for r in results if r is not None]
    if not present:
        return None
    return max(present, key=lambda r: r.aqi)
