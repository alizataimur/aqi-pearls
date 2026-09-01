"""D14 — alert rules (pure, no data dependency) and the Telegram sender
(mocked HTTP — this environment's TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are
both empty, the same credential gap as Hopsworks, see docs/STATE.md)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aqi.alerts import rules, telegram
from aqi.config import CityConfig, ZoneConfig
from aqi.serving.inference import HorizonForecast


def _forecast(aqi: float, *, date: str = "2026-01-15") -> HorizonForecast:
    category = "Very Unhealthy" if aqi > 200 else "Moderate"
    category_ur = "انتہائی مضر صحت" if aqi > 200 else "درمیانہ"
    return HorizonForecast(
        horizon_hours=24,
        target_local_date=date,
        predicted_aqi=aqi,
        category_en=category,
        category_ur=category_ur,
    )


class TestEvaluate:
    def test_crossing_above_200_from_clear_fires_episode(self) -> None:
        state: dict[str, dict[str, bool]] = {}
        decision = rules.evaluate("capital", _forecast(250.0), state)
        assert decision.kind == "episode"
        assert state["capital"]["in_episode"] is True

    def test_staying_above_200_does_not_refire(self) -> None:
        state = {"capital": {"in_episode": True}}
        decision = rules.evaluate("capital", _forecast(260.0), state)
        assert decision.kind is None

    def test_dropping_below_200_from_episode_fires_all_clear(self) -> None:
        state = {"capital": {"in_episode": True}}
        decision = rules.evaluate("capital", _forecast(150.0), state)
        assert decision.kind == "all_clear"
        assert state["capital"]["in_episode"] is False

    def test_staying_below_200_stays_silent(self) -> None:
        state = {"capital": {"in_episode": False}}
        decision = rules.evaluate("capital", _forecast(80.0), state)
        assert decision.kind is None

    def test_unknown_zone_defaults_to_not_in_episode(self) -> None:
        decision = rules.evaluate("lahore", _forecast(90.0), {})
        assert decision.kind is None


class TestStatePersistence:
    def test_save_then_load_round_trips(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "alerts_state.json"
        rules._save_state({"capital": {"in_episode": True}}, path)
        loaded = rules._load_state(path)
        assert loaded == {"capital": {"in_episode": True}}

    def test_missing_file_loads_empty(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert rules._load_state(tmp_path / "does_not_exist.json") == {}


class TestFormatMessage:
    def _zone(self) -> ZoneConfig:
        city = CityConfig(
            id="islamabad",
            name_en="Islamabad",
            name_ur="اسلام آباد",
            lat=33.7,
            lon=73.1,
            timezone="Asia/Karachi",
            cams_grid=(33.7, 73.0),
            zone="capital",
        )
        return ZoneConfig(
            zone_id="capital", representative_city=city, member_city_ids=("islamabad",)
        )

    def test_episode_message_has_both_languages_and_the_aqi_value(self) -> None:
        zone = self._zone()
        decision = rules.AlertDecision(
            zone_id="capital",
            kind="episode",
            predicted_aqi=250.0,
            target_local_date="2026-01-15",
            category_en="Very Unhealthy",
            category_ur="انتہائی مضر صحت",
        )
        message = rules.format_message(zone, decision)
        assert "250" in message
        assert "Islamabad" in message
        assert "اسلام آباد" in message

    def test_all_clear_message_has_both_languages(self) -> None:
        zone = self._zone()
        decision = rules.AlertDecision(
            zone_id="capital",
            kind="all_clear",
            predicted_aqi=120.0,
            target_local_date="2026-01-16",
            category_en="Moderate",
            category_ur="درمیانہ",
        )
        message = rules.format_message(zone, decision)
        assert "Islamabad" in message
        assert "اسلام آباد" in message

    def test_no_op_decision_raises(self) -> None:
        zone = self._zone()
        decision = rules.AlertDecision(
            zone_id="capital",
            kind=None,
            predicted_aqi=90.0,
            target_local_date="2026-01-16",
            category_en="Moderate",
            category_ur="درمیانہ",
        )
        with pytest.raises(ValueError, match="no-op"):
            rules.format_message(zone, decision)


class TestTelegramSender:
    def test_raises_when_not_configured(self) -> None:
        with patch("aqi.alerts.telegram.get_secrets") as mock_secrets:
            mock_secrets.return_value.telegram_bot_token = ""
            mock_secrets.return_value.telegram_chat_id = ""
            with pytest.raises(telegram.TelegramNotConfiguredError):
                telegram.send_message("hello")

    def test_posts_to_telegram_when_configured(self) -> None:
        with patch("aqi.alerts.telegram.get_secrets") as mock_secrets:
            mock_secrets.return_value.telegram_bot_token = "fake-token"
            mock_secrets.return_value.telegram_chat_id = "12345"
            with patch("aqi.alerts.telegram.requests.post") as mock_post:
                mock_post.return_value = MagicMock(raise_for_status=lambda: None)
                telegram.send_message("hello")
                mock_post.assert_called_once()
                called_url = mock_post.call_args.args[0]
                assert "fake-token" in called_url
                assert mock_post.call_args.kwargs["data"]["chat_id"] == "12345"
