#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore[assignment]


def _load_env() -> None:
    env_path = ROOT / ".env"
    if load_dotenv is not None and env_path.exists():
        load_dotenv(env_path)
    else:
        # Minimal fallback: export vars in shell before running.
        _ = env_path


def main() -> int:
    _load_env()

    from kalimati import db, scrape
    from kalimati.config import DB_PATH, OUTPUT_DIR
    from kalimati.facebook import upload_page_photo
    from kalimati.image_gen import render_price_grid_png
    from kalimati.notify import notify_price_drops

    parser = argparse.ArgumentParser(description="Fetch Kalimati prices, store SQLite, notify, render PNG.")
    parser.add_argument(
        "--date",
        help="Override snapshot date (YYYY-MM-DD) in Asia/Kathmandu unless --local-date is set.",
    )
    parser.add_argument(
        "--local-date",
        action="store_true",
        help="Use machine local timezone for the snapshot date instead of Asia/Kathmandu.",
    )
    args = parser.parse_args()

    if args.local_date:
        today = date.fromisoformat(args.date) if args.date else date.today()
    else:
        tz = ZoneInfo("Asia/Kathmandu")
        if args.date:
            today = date.fromisoformat(args.date)
        else:
            today = datetime.now(tz).date()

    db.ensure_db(DB_PATH)
    rows = scrape.fetch_today_rows()

    with db.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT MAX(day) FROM daily_prices WHERE day < ?",
            (today.isoformat(),),
        )
        prev_day_raw = cur.fetchone()[0]

    prev_map: dict[str, db.PriceRow] = {}
    if prev_day_raw:
        prev_map = db.prices_for_day(date.fromisoformat(prev_day_raw), DB_PATH)

    drops = scrape.compare_to_previous(rows, prev_map) if prev_map else []

    db.upsert_day(today, rows, DB_PATH)

    if drops:
        notify_price_drops(today, drops)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = render_price_grid_png(today, rows, OUTPUT_DIR, previous_by_commodity=prev_map)
    caption = (
        f"Kalimati daily vegetable prices — {today.isoformat()}\n"
        f"Source: https://kalimatimarket.gov.np/"
    )
    for p in paths:
        try:
            upload_page_photo(p, caption=caption)
        except Exception as exc:  # pragma: no cover - network integration
            print(f"[warn] Facebook upload skipped/failed for {p}: {exc}", file=sys.stderr)

    print(f"OK day={today.isoformat()} rows={len(rows)} pngs={len(paths)} drops={len(drops)} db={DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
