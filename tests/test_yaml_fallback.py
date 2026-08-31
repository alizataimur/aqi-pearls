"""The stdlib config parser must agree with PyYAML, exactly.

`scripts/clock_starter.py` reads `conf/cities.yaml` without requiring PyYAML so
that a dependency problem can never block a capture (CLAUDE.md §6). The CI
runner installs nothing, so it takes the fallback path while a developer's
laptop takes the PyYAML path — two different code paths reading the same file.

That divergence cost 16 consecutive scheduled runs and ~90 hours of permanent
ledger gaps: quoted station ids came back as `'"@11739"'` with the quote
characters attached, every city failed, and the workflow was red while
everything passed locally (ADR-012).

An earlier parity check existed and passed. It compared only `lat`. These tests
compare the whole structure, which is the only version that means anything.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import clock_starter  # noqa: E402


def load_without_pyyaml() -> list[dict[str, Any]]:
    """Load cities the way the CI runner does — no PyYAML available."""
    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("PyYAML unavailable (simulating the CI runner)")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = blocked
    try:
        return clock_starter.load_cities()
    finally:
        builtins.__import__ = real_import


class TestParserParity:
    def test_full_structure_matches_pyyaml(self) -> None:
        """Not just lat. Every key, every value, every type."""
        yaml = pytest.importorskip("yaml")
        expected = yaml.safe_load(
            (REPO_ROOT / "conf" / "cities.yaml").read_text(encoding="utf-8")
        )["cities"]
        assert load_without_pyyaml() == expected

    def test_station_ids_carry_no_quote_characters(self) -> None:
        """The exact regression: '\"@11739\"' instead of '@11739'."""
        for city in load_without_pyyaml():
            station = city.get("aqicn_station")
            if station is None:
                continue
            assert '"' not in station and "'" not in station, (
                f"{city['id']}: station {station!r} still carries quote "
                "characters — this produces a nonsense URL and a red workflow"
            )
            assert station.startswith("@") or station.isalpha()

    def test_every_configured_city_survives_the_fallback(self) -> None:
        cities = load_without_pyyaml()
        assert [c["id"] for c in cities] == ["islamabad", "rawalpindi", "lahore"]
        for city in cities:
            assert isinstance(city["lat"], float)
            assert isinstance(city["lon"], float)


class TestScalarParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('"@11739"', "@11739"),
            ("'@11765'", "@11765"),
            ("null", None),
            ("~", None),
            ("", None),
            ("33.6844", 33.6844),
            ("11739", 11739),
            ("true", True),
            ("false", False),
            ("Asia/Karachi", "Asia/Karachi"),
            ("[33.7, 73.0]", [33.7, 73.0]),
            ("[]", []),
        ],
    )
    def test_scalars(self, raw: str, expected: Any) -> None:
        assert clock_starter._parse_scalar(raw) == expected

    def test_unicode_survives(self) -> None:
        assert clock_starter._parse_scalar("اسلام آباد") == "اسلام آباد"

    def test_int_and_float_are_distinguished(self) -> None:
        assert isinstance(clock_starter._parse_scalar("5"), int)
        assert isinstance(clock_starter._parse_scalar("5.0"), float)


class TestMalformedStationIsRejectedLoudly:
    def test_quoted_station_raises_a_useful_error(self) -> None:
        """Defence in depth: even if a parser regresses, the fetch refuses."""
        from aqi.sources.aqicn import AQICNError, fetch_feed

        with pytest.raises(AQICNError, match="malformed station identifier"):
            fetch_feed('"@11739"', "sometoken")

    def test_whitespace_in_station_is_rejected(self) -> None:
        from aqi.sources.aqicn import AQICNError, fetch_feed

        with pytest.raises(AQICNError, match="malformed station identifier"):
            fetch_feed("@11739 ", "sometoken")

    def test_empty_station_is_rejected(self) -> None:
        from aqi.sources.aqicn import AQICNError, fetch_feed

        with pytest.raises(AQICNError, match="malformed station identifier"):
            fetch_feed("", "sometoken")
