from __future__ import annotations

import json
from datetime import date
from typing import Iterable

import requests

from kalimati.config import NTFY_TOPIC, WEBHOOK_URL


def send_ntfy(title: str, message: str, tags: list[str] | None = None) -> None:
    if not NTFY_TOPIC:
        return
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    headers = {"Title": title[:200]}
    if tags:
        headers["Tags"] = ",".join(tags)
    r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=30)
    r.raise_for_status()


def send_webhook(payload: dict) -> None:
    if not WEBHOOK_URL:
        return
    r = requests.post(
        WEBHOOK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()


def notify_price_drops(day: date, drops: Iterable[tuple[str, float | None, float | None]]) -> None:
    drops = list(drops)
    if not drops:
        return
    title = f"Kalimati: {len(drops)} item(s) cheaper than last day ({day.isoformat()})"
    lines = [f"{name}: Rs {prev:g} → Rs {now:g}" for name, prev, now in drops]
    message = "\n".join(lines[:50])
    if len(lines) > 50:
        message += f"\n… and {len(lines) - 50} more"

    send_ntfy(title, message, tags=["moneybag", "chart_with_downwards_trend"])
    send_webhook({"type": "kalimati_price_drop", "day": day.isoformat(), "drops": drops})
