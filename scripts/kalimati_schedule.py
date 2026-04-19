#!/usr/bin/env python3
"""
Scheduled entry points for macOS / Linux / Windows task runners.

  sync       — same as ``daily_job.py`` (fetch site, SQLite, PNGs, drop alerts).
  digest-am  — always send a **system** notification (7:30 AM style summary).
  digest-pm  — always send a **system** notification (7:30 PM style summary).

Typical wall-clock schedule (adjust for your timezone, see INSTALL.txt):

  07:00  sync
  07:30  digest-am
  19:30  digest-pm
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_env() -> None:
    env_path = ROOT / ".env"
    if load_dotenv is not None and env_path.exists():
        load_dotenv(env_path)


def _digest_body(slot: str, s: dict) -> tuple[str, str]:
    if not s.get("has_data"):
        return (
            f"Kalimati · {slot}",
            "SQLite has no rows yet. The 7:00 sync job will fill data after the first run.",
        )
    ld = s["latest_day"]
    n = s["rows_latest"]
    ch, hi, sm = s["min_cheaper"], s["min_higher"], s["min_same"]
    pv = s.get("prior_day")
    if pv:
        cmp_line = f"Cheaper: {ch} · Higher: {hi} · Same min: {sm} (vs {pv})."
    else:
        cmp_line = "Only one day stored — no day-over-day comparison yet."
    body = f"Latest snapshot: {ld} ({n} rows). {cmp_line}"
    title = f"Kalimati · {slot} · {ld}"
    return title, body


def cmd_digest(slot: str) -> int:
    _load_env()
    from kalimati.config import DB_PATH
    from kalimati.db import digest_stats
    from kalimati.system_notify import send_system_notification

    stats = digest_stats(DB_PATH)
    label = "Morning summary" if slot == "am" else "Evening summary"
    title, body = _digest_body(label, stats)
    send_system_notification(title, body, force=True)
    print(f"digest-{slot}: {title}")
    return 0


def cmd_sync() -> int:
    _load_env()
    try:
        runpy.run_path(str(ROOT / "scripts" / "daily_job.py"), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Kalimati scheduled tasks (sync + digests).")
    p.add_argument("command", choices=("sync", "digest-am", "digest-pm"))
    args = p.parse_args()
    if args.command == "sync":
        return cmd_sync()
    if args.command == "digest-am":
        return cmd_digest("am")
    if args.command == "digest-pm":
        return cmd_digest("pm")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
