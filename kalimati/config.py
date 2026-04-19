from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    return p


DB_PATH = _path("KALIMATI_DB_PATH", "data/prices.db")
OUTPUT_DIR = _path("KALIMATI_OUTPUT_DIR", "output")

NTFY_TOPIC = os.environ.get("KALIMATI_NTFY_TOPIC", "").strip()
WEBHOOK_URL = os.environ.get("KALIMATI_WEBHOOK_URL", "").strip()

FACEBOOK_PAGE_ID = os.environ.get("KALIMATI_FACEBOOK_PAGE_ID", "").strip()
FACEBOOK_ACCESS_TOKEN = os.environ.get("KALIMATI_FACEBOOK_ACCESS_TOKEN", "").strip()

DASHBOARD_HOST = os.environ.get("KALIMATI_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("KALIMATI_DASHBOARD_PORT", "8765"))

USER_AGENT = os.environ.get(
    "KALIMATI_USER_AGENT",
    "Mozilla/5.0 (compatible; KalimatiPriceBot/0.1; +https://example.local)",
)

HOME_URL = os.environ.get("KALIMATI_HOME_URL", "https://kalimatimarket.gov.np/")
