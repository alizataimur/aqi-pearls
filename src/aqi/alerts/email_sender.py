"""SMTP email sender (D14) — the **default** alert channel
(`docs/DECISIONS.md` ADR-032): Telegram is blocked in Pakistan by the PTA,
so it cannot be the default channel for a product built for Pakistani
citizens. stdlib only (`smtplib`, `email.message`) — no new dependency.

Config from env only (CLAUDE.md I9): `ALERT_EMAIL_HOST`, `ALERT_EMAIL_PORT`,
`ALERT_EMAIL_USER`, `ALERT_EMAIL_PASSWORD`, `ALERT_EMAIL_TO`. For Gmail:
`smtp.gmail.com` / `587` with STARTTLS, and `ALERT_EMAIL_PASSWORD` must be a
Gmail **App Password** — Gmail has rejected plain account passwords for SMTP
since 2022. The password is never logged: it's read from `Secrets` and
handed straight to `smtplib.SMTP.login`, and no `print`/logging statement in
this module ever references it.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from aqi.alerts.notifier import NotifierNotConfiguredError
from aqi.config import get_secrets

DEFAULT_SUBJECT = "Pearls AQI Predictor alert"


class EmailNotConfiguredError(NotifierNotConfiguredError):
    pass


class EmailNotifier:
    """`Notifier` implementation: SMTP with STARTTLS."""

    def send(self, text: str) -> None:
        """Raises `EmailNotConfiguredError` if the secrets aren't fully
        set, or `smtplib.SMTPException` if the SMTP server itself rejects
        the message — same "never swallow a send failure" contract as
        `TelegramNotifier.send`."""
        secrets = get_secrets()
        if not (
            secrets.alert_email_host
            and secrets.alert_email_user
            and secrets.alert_email_password
            and secrets.alert_email_to
        ):
            raise EmailNotConfiguredError(
                "ALERT_EMAIL_HOST/ALERT_EMAIL_USER/ALERT_EMAIL_PASSWORD/"
                "ALERT_EMAIL_TO not fully set (CLAUDE.md I9) — see .env.example"
            )

        message = EmailMessage()
        message["Subject"] = DEFAULT_SUBJECT
        message["From"] = secrets.alert_email_user
        message["To"] = secrets.alert_email_to
        message.set_content(text)

        with smtplib.SMTP(secrets.alert_email_host, secrets.alert_email_port) as smtp:
            smtp.starttls()
            smtp.login(secrets.alert_email_user, secrets.alert_email_password)
            smtp.send_message(message)


def send_test_message(notifier: EmailNotifier | None = None) -> None:
    notifier = notifier or EmailNotifier()
    notifier.send(
        "Pearls AQI Predictor: test alert. If you can read this, email " "delivery works."
    )


if __name__ == "__main__":
    send_test_message()
    print("test message sent")
