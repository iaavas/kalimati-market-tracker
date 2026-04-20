from __future__ import annotations

import json
from datetime import date
from typing import Any

import requests

from kalimati.config import NTFY_TOPIC, WEBHOOK_URL
from kalimati.system_notify import send_system_notification


def _format_drop_line(commodity: str, prev: float | None, today: float | None) -> str:
    def _rs(x: float | None) -> str:
        if x is None:
            return "—"
        return f"Rs.{x:g}"

    return f"• {commodity}: {_rs(prev)} → {_rs(today)}"


def _title_and_body(
    day: date,
    drops: list[tuple[str, float | None, float | None]],
    *,
    max_lines: int = 12,
) -> tuple[str, str]:
    title = f"Kalimati · price drops · {day.isoformat()}"
    lines = [_format_drop_line(c, p, t) for c, p, t in drops]
    if len(lines) > max_lines:
        rest = len(lines) - max_lines
        lines = lines[:max_lines] + [f"… and {rest} more"]
    body = "\n".join(lines)
    return title, body


def notify_price_drops(day: date, drops: list[tuple[str, float | None, float | None]]) -> None:
    """
    Notify about day-over-day min-price drops: system toast (optional), ntfy, and/or webhook.
    """
    if not drops:
        return

    title, body = _title_and_body(day, drops)
    send_system_notification(title, body)

    if NTFY_TOPIC:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        try:
            requests.post(
                url,
                data=body.encode("utf-8"),
                headers={"Title": title},
                timeout=30,
            )
        except (OSError, requests.RequestException):
            pass

    if WEBHOOK_URL:
        payload: dict[str, Any] = {
            "source": "kalimati",
            "kind": "price_drops",
            "date": day.isoformat(),
            "title": title,
            "body": body,
            "drops": [
                {"commodity": c, "previous_min": p, "today_min": t}
                for c, p, t in drops
            ],
        }
        try:
            requests.post(
                WEBHOOK_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
        except (OSError, requests.RequestException):
            pass
