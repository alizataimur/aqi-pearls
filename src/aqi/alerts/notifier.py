"""Notifier Protocol (D14) — the alert *rules* (`alerts/rules.py::evaluate`,
`format_message`) are channel-agnostic; only the transport differs between
implementations. Telegram (`alerts/telegram.py`) was the original choice and
remains a working implementation, but it is blocked in Pakistan by the PTA —
the market this product is for — so it cannot be the default channel a
citizen actually receives (`docs/DECISIONS.md` ADR-032). Email
(`alerts/email_sender.py`) is now default; Telegram stays available and
untouched, e.g. for the maintainer's own monitoring from outside Pakistan,
where the PTA block doesn't apply.
"""

from __future__ import annotations

from typing import Protocol


class NotifierNotConfiguredError(RuntimeError):
    """Raised by a `Notifier` implementation when its required env config
    (CLAUDE.md I9) isn't set. `alerts/rules.py::run_alerts` catches this one
    base type regardless of which channel `ALERT_CHANNEL` selects — a new
    channel only needs to subclass this, not touch the dispatch code."""


class Notifier(Protocol):
    def send(self, text: str) -> None:
        """Send `text` to whatever destination this channel is configured
        for. Raises (`NotifierNotConfiguredError` for a missing config, the
        channel's own error type for a real send failure) rather than
        swallowing anything — the caller decides whether to degrade or
        propagate; this Protocol never decides that on its own."""
        ...
