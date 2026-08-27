"""Guards against ADR-007 — the wrong-station failure.

On 2026-08-27 the `geo:lat;lon` lookup returned a Delhi station (~690 km away)
for all three Pakistani cities, with `status: "ok"` and a plausible AQI. Nothing
downstream would have noticed. AQICN has no history endpoint, so every hour
captured that way would have been permanently wrong ground truth *and* a
benchmark against a different country's forecast.

These tests exist so that cannot happen again quietly.
"""

from __future__ import annotations

import re

import pytest

from aqi.sources.aqicn import (
    AQICNError,
    StationMismatchError,
    _haversine_km,
    verify_station,
)

ISLAMABAD_STATION = (33.7235, 73.11822)  # Islamabad US Embassy, idx 11739
LAHORE_STATION = (31.560078, 74.33589)  # Lahore US Embassy, idx 11765
DELHI_STATION = (28.7757959, 77.0462514)  # Pooth Khurd, Bawana — the wrong one


def feed(lat: float, lon: float, name: str = "Test Station") -> dict:
    return {"status": "ok", "data": {"city": {"name": name, "geo": [lat, lon]}}}


class TestGeoLookupIsForbidden:
    def test_fetch_feed_rejects_geo_station(self) -> None:
        """geo: silently ignores the coordinates — never allow it back in."""
        with pytest.raises(AQICNError, match="geo: lookups are forbidden"):
            from aqi.sources.aqicn import fetch_feed

            fetch_feed("geo:33.6844;73.0479", "sometoken")

    def test_error_names_the_remedy(self) -> None:
        from aqi.sources.aqicn import fetch_feed

        with pytest.raises(AQICNError, match=re.escape("conf/cities.yaml")):
            fetch_feed("geo:31.5;74.3", "sometoken")


class TestStationVerification:
    def test_correct_station_passes(self) -> None:
        distance = verify_station(feed(*ISLAMABAD_STATION), *ISLAMABAD_STATION)
        assert distance == pytest.approx(0.0, abs=0.01)

    def test_the_actual_delhi_regression(self) -> None:
        """The exact failure from 2026-08-27, as a test."""
        with pytest.raises(StationMismatchError) as exc:
            verify_station(feed(*DELHI_STATION), 33.7235, 73.11822)
        assert "refusing to write it to the ledger" in str(exc.value)

    def test_lahore_station_is_not_accepted_for_islamabad(self) -> None:
        """Neighbouring-city substitution is still wrong data."""
        with pytest.raises(StationMismatchError):
            verify_station(feed(*LAHORE_STATION), *ISLAMABAD_STATION)

    def test_a_station_across_town_is_fine(self) -> None:
        """Rawalpindi vs Islamabad is ~15 km — a legitimate metro-area station."""
        distance = verify_station(feed(33.5651, 73.0169), *ISLAMABAD_STATION)
        assert distance < 60.0

    def test_missing_coordinates_fail_closed(self) -> None:
        """No geo means no verification means no write."""
        with pytest.raises(StationMismatchError, match="no station coordinates"):
            verify_station({"status": "ok", "data": {"city": {"name": "X"}}}, 33.7, 73.1)

    def test_threshold_is_configurable_but_still_enforced(self) -> None:
        with pytest.raises(StationMismatchError):
            verify_station(feed(33.5651, 73.0169), *ISLAMABAD_STATION, max_km=5.0)


class TestHaversine:
    def test_known_distance_islamabad_to_lahore(self) -> None:
        # Great-circle Islamabad -> Lahore is roughly 270 km.
        distance = _haversine_km(*ISLAMABAD_STATION, *LAHORE_STATION)
        assert 250 < distance < 290

    def test_known_distance_islamabad_to_delhi(self) -> None:
        # The miss that started this: roughly 690 km.
        distance = _haversine_km(*ISLAMABAD_STATION, *DELHI_STATION)
        assert 650 < distance < 730

    def test_zero_distance(self) -> None:
        assert _haversine_km(33.7, 73.1, 33.7, 73.1) == pytest.approx(0.0)
