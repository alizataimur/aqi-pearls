"""Alert rules (D14) — trigger on the D+1 daily-max forecast crossing 200.

CLAUDE.md §14 specifies triggering on `P(AQI>200) > 0.6` rather than a point
forecast crossing 200 — "the uncertainty thesis applied to a real decision."
That needs a probability head (a classifier or conformal interval), and both
are cut this session (`docs/DECISIONS.md`, session 5's ADR-021 and this
session's brief: "P(AQI>200) is unavailable since the classifier was cut").
Until one exists, this rule uses the **D+1 point forecast** (`horizon_hours
== 24` — tomorrow, the most actionable one) crossing 200 as an honest,
documented substitute for the real rule, not a silently-relabelled one.

Deduplication is state-based, not time-based: `data/alerts_state.json`
(gitignored, regenerable — same class of artifact as `data/model_registry/`)
tracks whether each zone is currently "in an episode." A message fires only
on the transition into hazard (`episode`) or back out of it (`all_clear`) —
CLAUDE.md's "deduplicate within an episode... send an all-clear," read
literally as a state machine rather than a fixed time window.

`evaluate()` and `format_message()` below are the whole rule: what triggers
an alert and what it says. Neither knows or cares how the message is
delivered — `run_alerts()` picks a `Notifier` (`alerts/notifier.py`) by
`ALERT_CHANNEL`, and the channel refactor (ADR-032: Telegram is blocked in
Pakistan by the PTA, so email is now default) touched only that dispatch
code, not this rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from aqi.alerts.notifier import Notifier, NotifierNotConfiguredError
from aqi.config import REPO_ROOT, ZoneConfig, get_secrets
from aqi.explain.i18n import alert_template, health_guidance
from aqi.serving.inference import HorizonForecast, forecast_zone, load_frame_cached, zones

ALERT_HORIZON_HOURS = 24
HAZARD_THRESHOLD = 200
STATE_PATH = REPO_ROOT / "data" / "alerts_state.json"

AlertKind = Literal["episode", "all_clear"]


@dataclass(frozen=True)
class AlertDecision:
    zone_id: str
    kind: AlertKind | None
    predicted_aqi: float
    target_local_date: str
    category_en: str
    category_ur: str


def _load_state(path: Path = STATE_PATH) -> dict[str, dict[str, bool]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _save_state(state: dict[str, dict[str, bool]], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def evaluate(
    zone_id: str, horizon: HorizonForecast, state: dict[str, dict[str, bool]]
) -> AlertDecision:
    """Pure function — no I/O, so `tests/test_alerts.py` can drive the state
    machine directly without a feature store or a registered model."""
    triggered = horizon.predicted_aqi > HAZARD_THRESHOLD
    was_in_episode = bool(state.get(zone_id, {}).get("in_episode", False))

    kind: AlertKind | None
    if triggered and not was_in_episode:
        kind = "episode"
    elif not triggered and was_in_episode:
        kind = "all_clear"
    else:
        kind = None

    state[zone_id] = {"in_episode": triggered}
    return AlertDecision(
        zone_id=zone_id,
        kind=kind,
        predicted_aqi=horizon.predicted_aqi,
        target_local_date=horizon.target_local_date,
        category_en=horizon.category_en,
        category_ur=horizon.category_ur,
    )


def format_message(zone: ZoneConfig, decision: AlertDecision) -> str:
    if decision.kind is None:
        raise ValueError("format_message called on a no-op decision")
    city = zone.representative_city
    if decision.kind == "episode":
        template_en, template_ur = alert_template("episode")
        guidance_en, guidance_ur = health_guidance(decision.category_en)
        line_en = template_en.format(
            city_en=city.name_en,
            aqi=round(decision.predicted_aqi),
            category_en=decision.category_en,
            date=decision.target_local_date,
            guidance_en=guidance_en,
        )
        line_ur = template_ur.format(
            city_ur=city.name_ur,
            aqi=round(decision.predicted_aqi),
            category_ur=decision.category_ur,
            date=decision.target_local_date,
            guidance_ur=guidance_ur,
        )
    else:
        template_en, template_ur = alert_template("all_clear")
        line_en = template_en.format(city_en=city.name_en)
        line_ur = template_ur.format(city_ur=city.name_ur)
    return f"{line_en}\n\n{line_ur}"


def _build_notifier(channel: str | None = None) -> Notifier:
    """`ALERT_CHANNEL` picks the transport (email default, telegram
    supported — ADR-032); the rule logic above never sees this choice.
    Imports are local to avoid `smtplib`/`requests` import cost for callers
    (e.g. `evaluate`/`format_message` unit tests) that never send anything."""
    from aqi.alerts.email_sender import EmailNotifier
    from aqi.alerts.telegram import TelegramNotifier

    chosen = channel or get_secrets().alert_channel
    if chosen == "email":
        return EmailNotifier()
    if chosen == "telegram":
        return TelegramNotifier()
    raise ValueError(f"unknown ALERT_CHANNEL {chosen!r} — expected 'email' or 'telegram'")


def run_alerts(
    zone_ids: set[str] | None = None,
    *,
    dry_run: bool = False,
    channel: str | None = None,
) -> list[AlertDecision]:
    """Evaluate every zone's D+1 forecast and send a message on any state
    transition, via whichever `Notifier` `ALERT_CHANNEL` (or `channel`)
    selects. `dry_run=True` evaluates and prints without sending — used by
    CI and by anyone without the selected channel's credentials yet."""
    notifier = _build_notifier(channel)
    frame = load_frame_cached()
    state = _load_state()
    decisions = []

    for zone in zones():
        if zone_ids is not None and zone.zone_id not in zone_ids:
            continue
        horizons = forecast_zone(frame, zone.zone_id, zone.timezone)
        h24 = next(h for h in horizons if h.horizon_hours == ALERT_HORIZON_HOURS)
        decision = evaluate(zone.zone_id, h24, state)
        decisions.append(decision)

        if decision.kind is None:
            continue
        message = format_message(zone, decision)
        if dry_run:
            print(f"[alerts] (dry run) {zone.zone_id}: {decision.kind}\n{message}")
            continue
        try:
            notifier.send(message)
            channel_name = type(notifier).__name__
            print(f"[alerts] sent via {channel_name} {zone.zone_id}: {decision.kind}")
        except NotifierNotConfiguredError as exc:
            print(f"[alerts] not configured, not sent — {exc}\n{message}")

    _save_state(state)
    return decisions


if __name__ == "__main__":
    import sys

    run_alerts(dry_run="--dry-run" in sys.argv)
