"""Telegram sender (D14). Token and chat id from env only (CLAUDE.md I9) —
never hardcoded, never in a notebook output.

`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are both empty in this environment
(the same credential-gap pattern as Hopsworks — `docs/STATE.md`). This
module is written and exercised against a mocked HTTP call
(`tests/test_alerts.py`); `TelegramNotConfiguredError` is what a real run raises
until RUNBOOK §5's `/newbot` step happens — needs Aliza, not more code.
"""

from __future__ import annotations

import requests

from aqi.config import get_secrets

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT_SECONDS = 10


class TelegramNotConfiguredError(RuntimeError):
    pass


def send_message(text: str) -> None:
    """Raises `TelegramNotConfiguredError` if the secrets aren't set, or
    `requests.HTTPError` if Telegram itself rejects the request — both are
    real failures the caller (`alerts/rules.py`'s CLI) should see, not
    swallow (an alert that silently fails to send is worse than a crash)."""
    secrets = get_secrets()
    if not secrets.telegram_bot_token or not secrets.telegram_chat_id:
        raise TelegramNotConfiguredError(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set (CLAUDE.md I9) — "
            "see docs/RUNBOOK.md §5: /newbot with @BotFather, then get the chat id"
        )
    url = TELEGRAM_API_URL.format(token=secrets.telegram_bot_token)
    response = requests.post(
        url,
        data={"chat_id": secrets.telegram_chat_id, "text": text},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def send_test_message() -> None:
    send_message(
        "Pearls AQI Predictor: test alert. If you can read this, Telegram "
        "delivery works."
    )


if __name__ == "__main__":
    send_test_message()
    print("test message sent")
