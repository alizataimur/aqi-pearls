"""D14 — alert rules (pure, no data dependency) and the two `Notifier`
implementations (mocked I/O — this environment has none of
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID/ALERT_EMAIL_* set, the same credential
gap as Hopsworks, see docs/STATE.md)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aqi.alerts import email_sender, rules, telegram
from aqi.alerts.notifier import NotifierNotConfiguredError
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
                telegram.TelegramNotifier().send("hello")

    def test_not_configured_error_is_a_notifier_not_configured_error(self) -> None:
        assert issubclass(telegram.TelegramNotConfiguredError, NotifierNotConfiguredError)

    def test_posts_to_telegram_when_configured(self) -> None:
        with patch("aqi.alerts.telegram.get_secrets") as mock_secrets:
            mock_secrets.return_value.telegram_bot_token = "fake-token"
            mock_secrets.return_value.telegram_chat_id = "12345"
            with patch("aqi.alerts.telegram.requests.post") as mock_post:
                mock_post.return_value = MagicMock(raise_for_status=lambda: None)
                telegram.TelegramNotifier().send("hello")
                mock_post.assert_called_once()
                called_url = mock_post.call_args.args[0]
                assert "fake-token" in called_url
                assert mock_post.call_args.kwargs["data"]["chat_id"] == "12345"


class TestEmailSender:
    def _configured_secrets(self) -> MagicMock:
        secrets = MagicMock()
        secrets.alert_email_host = "smtp.gmail.com"
        secrets.alert_email_port = 587
        secrets.alert_email_user = "sender@example.com"
        secrets.alert_email_password = "app-password"
        secrets.alert_email_to = "recipient@example.com"
        return secrets

    def test_raises_when_not_configured(self) -> None:
        with patch("aqi.alerts.email_sender.get_secrets") as mock_secrets:
            mock_secrets.return_value.alert_email_host = ""
            mock_secrets.return_value.alert_email_user = ""
            mock_secrets.return_value.alert_email_password = ""
            mock_secrets.return_value.alert_email_to = ""
            with pytest.raises(email_sender.EmailNotConfiguredError):
                email_sender.EmailNotifier().send("hello")

    def test_not_configured_error_is_a_notifier_not_configured_error(self) -> None:
        assert issubclass(
            email_sender.EmailNotConfiguredError, NotifierNotConfiguredError
        )

    def test_sends_via_smtp_with_starttls_and_login_when_configured(self) -> None:
        with patch("aqi.alerts.email_sender.get_secrets") as mock_secrets:
            mock_secrets.return_value = self._configured_secrets()
            with patch("aqi.alerts.email_sender.smtplib.SMTP") as mock_smtp_cls:
                mock_smtp = MagicMock()
                mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
                email_sender.EmailNotifier().send("hello")

                mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587)
                mock_smtp.starttls.assert_called_once()
                mock_smtp.login.assert_called_once_with(
                    "sender@example.com", "app-password"
                )
                mock_smtp.send_message.assert_called_once()
                sent_message = mock_smtp.send_message.call_args.args[0]
                assert sent_message["To"] == "recipient@example.com"
                assert sent_message.get_content().strip() == "hello"

    def test_password_never_appears_in_repr_of_a_failure(self) -> None:
        # A cheap guard against accidentally logging the secrets object
        # itself (I9) — the password must never be in any string a caller
        # could plausibly print.
        with patch("aqi.alerts.email_sender.get_secrets") as mock_secrets:
            mock_secrets.return_value = self._configured_secrets()
            with patch("aqi.alerts.email_sender.smtplib.SMTP") as mock_smtp_cls:
                mock_smtp_cls.side_effect = OSError("connection refused")
                try:
                    email_sender.EmailNotifier().send("hello")
                except OSError as exc:
                    assert "app-password" not in str(exc)


class TestNotifierSelection:
    def test_email_is_the_default_channel(self) -> None:
        with patch("aqi.alerts.rules.get_secrets") as mock_secrets:
            mock_secrets.return_value.alert_channel = "email"
            notifier = rules._build_notifier()
            assert isinstance(notifier, email_sender.EmailNotifier)

    def test_explicit_channel_argument_overrides_the_secret(self) -> None:
        notifier = rules._build_notifier("telegram")
        assert isinstance(notifier, telegram.TelegramNotifier)

    def test_unknown_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="ALERT_CHANNEL"):
            rules._build_notifier("carrier_pigeon")
