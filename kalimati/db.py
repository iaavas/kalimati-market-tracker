from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

from kalimati.config import DB_PATH

# Same basis as posters / dashboard: min wholesale vs prior day.
MIN_PRICE_MOVE_EPS = 0.01


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    commodity TEXT NOT NULL,
    min_price REAL,
    max_price REAL,
    avg_price REAL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(day, commodity)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_day ON daily_prices(day);
CREATE INDEX IF NOT EXISTS idx_daily_prices_commodity ON daily_prices(commodity);
"""


@dataclass(frozen=True)
class PriceRow:
    commodity: str
    min_price: float | None
    max_price: float | None
    avg_price: float | None


def ensure_db(path: Path | None = None) -> Path:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.executescript(SCHEMA)
    return p


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    ensure_db(path)
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_day(day: date, rows: Iterable[PriceRow], path: Path | None = None) -> int:
    d = day.isoformat()
    inserted = 0
    with connect(path) as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO daily_prices(day, commodity, min_price, max_price, avg_price)
                VALUES(?,?,?,?,?)
                ON CONFLICT(day, commodity) DO UPDATE SET
                    min_price=excluded.min_price,
                    max_price=excluded.max_price,
                    avg_price=excluded.avg_price
                """,
                (d, r.commodity, r.min_price, r.max_price, r.avg_price),
            )
            inserted += 1
    return inserted


def latest_two_days(path: Path | None = None) -> tuple[date | None, date | None]:
    with connect(path) as conn:
        cur = conn.execute(
            "SELECT DISTINCT day FROM daily_prices ORDER BY day DESC LIMIT 2"
        )
        days = [date.fromisoformat(r[0]) for r in cur.fetchall()]
    if not days:
        return None, None
    if len(days) == 1:
        return days[0], None
    return days[0], days[1]


def prices_for_day(day: date, path: Path | None = None) -> dict[str, PriceRow]:
    d = day.isoformat()
    out: dict[str, PriceRow] = {}
    with connect(path) as conn:
        cur = conn.execute(
            "SELECT commodity, min_price, max_price, avg_price FROM daily_prices WHERE day=?",
            (d,),
        )
        for row in cur.fetchall():
            out[row["commodity"]] = PriceRow(
                commodity=row["commodity"],
                min_price=row["min_price"],
                max_price=row["max_price"],
                avg_price=row["avg_price"],
            )
    return out


def list_commodities(path: Path | None = None) -> list[str]:
    with connect(path) as conn:
        cur = conn.execute(
            "SELECT DISTINCT commodity FROM daily_prices ORDER BY commodity COLLATE NOCASE"
        )
        return [r[0] for r in cur.fetchall()]


def commodity_unit_from_name(commodity: str) -> str:
    """Best-effort unit label from Kalimati commodity name (same rules as posters)."""
    parts = re.findall(r"\(([^)]*)\)", commodity)
    if not parts:
        return "केजी"

    def classify(inner: str) -> str | None:
        s = inner.strip()
        if "मुठा" in s:
            return "मुठा"
        if "दर्जन" in s:
            return "दर्जन"
        if "गोटा" in s:
            return "गोटा"
        if "प्रति" in s:
            return s.replace("  ", " ")
        compact = s.replace(" ", "").replace(".", "")
        if "केजी" in s or "केजी" in compact:
            return "केजी"
        return None

    for inner in reversed(parts):
        u = classify(inner)
        if u:
            return u
    return "केजी"


def snapshot_today_prices(path: Path | None = None) -> dict[str, Any]:
    """Latest day in DB with min / max / avg per commodity plus parsed ``unit`` for calculators."""
    latest, _ = latest_two_days(path)
    if latest is None:
        return {"has_data": False}
    rows = prices_for_day(latest, path)
    items: list[dict[str, Any]] = []
    for name in sorted(rows.keys(), key=str.casefold):
        row = rows[name]
        items.append(
            {
                "commodity": name,
                "min_price": row.min_price,
                "max_price": row.max_price,
                "avg_price": row.avg_price,
                "unit": commodity_unit_from_name(name),
            }
        )
    return {"has_data": True, "day": latest.isoformat(), "items": items}


def series_for_commodity(commodity: str, path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        cur = conn.execute(
            """
            SELECT day, min_price, max_price, avg_price
            FROM daily_prices
            WHERE commodity=?
            ORDER BY day ASC
            """,
            (commodity,),
        )
        return [dict(row) for row in cur.fetchall()]


def snapshot_min_price_movements(path: Path | None = None) -> dict[str, Any]:
    """
    Classify commodities on the latest day vs the prior snapshot using minimum price.

    Returns ``has_data``, ``latest_day``, ``prior_day``, and three lists:
    ``down``, ``up``, ``neutral``. Each item has ``commodity``, ``min``, ``prior_min`` (nullable),
    and ``delta`` (nullable when not comparable).
    """
    latest, prior = latest_two_days(path)
    if latest is None:
        return {"has_data": False}

    cur = prices_for_day(latest, path)
    prev_map = prices_for_day(prior, path) if prior else {}

    down: list[dict[str, Any]] = []
    up: list[dict[str, Any]] = []
    neutral: list[dict[str, Any]] = []

    for name in sorted(cur.keys(), key=str.casefold):
        row = cur[name]
        cm = row.min_price
        pr = prev_map.get(name)
        pm = pr.min_price if pr else None
        if cm is None:
            neutral.append(
                {
                    "commodity": name,
                    "min": None,
                    "prior_min": float(pm) if pm is not None else None,
                    "delta": None,
                }
            )
            continue
        if pm is None:
            neutral.append(
                {
                    "commodity": name,
                    "min": float(cm),
                    "prior_min": None,
                    "delta": None,
                }
            )
            continue
        a, b = float(pm), float(cm)
        d = b - a
        item = {"commodity": name, "min": b, "prior_min": a, "delta": d}
        if d < -MIN_PRICE_MOVE_EPS:
            down.append(item)
        elif d > MIN_PRICE_MOVE_EPS:
            up.append(item)
        else:
            neutral.append(item)

    return {
        "has_data": True,
        "latest_day": latest.isoformat(),
        "prior_day": prior.isoformat() if prior else None,
        "down": down,
        "up": up,
        "neutral": neutral,
    }


def digest_stats(path: Path | None = None) -> dict:
    """Summary for scheduled AM/PM system notifications (latest day vs prior)."""
    with connect(path) as conn:
        max_day = conn.execute("SELECT MAX(day) FROM daily_prices").fetchone()[0]
        if not max_day:
            return {"has_data": False}

        n_rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE day=?",
                (max_day,),
            ).fetchone()[0]
        )
        prev = conn.execute(
            "SELECT MAX(day) FROM daily_prices WHERE day < ?",
            (max_day,),
        ).fetchone()[0]

        cheaper = higher = same = 0
        cur_rows = conn.execute(
            "SELECT commodity, min_price FROM daily_prices WHERE day=?",
            (max_day,),
        ).fetchall()
        prev_map: dict[str, float | None] = {}
        if prev:
            for r in conn.execute(
                "SELECT commodity, min_price FROM daily_prices WHERE day=?",
                (prev,),
            ):
                prev_map[r["commodity"]] = r["min_price"]
        for r in cur_rows:
            mn = r["min_price"]
            key = r["commodity"]
            if key not in prev_map or prev_map[key] is None or mn is None:
                continue
            a, b = float(prev_map[key]), float(mn)
            if b < a - 1e-9:
                cheaper += 1
            elif b > a + 1e-9:
                higher += 1
            else:
                same += 1

    return {
        "has_data": True,
        "latest_day": max_day,
        "prior_day": prev,
        "rows_latest": n_rows,
        "min_cheaper": cheaper,
        "min_higher": higher,
        "min_same": same,
    }
