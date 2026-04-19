from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

from kalimati.config import DB_PATH


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


def ohlc_series_for_commodity(commodity: str, path: Path | None = None) -> list[dict]:
    """
    OHLC for charts: low=min, high=max, close=avg, open=previous day's close (avg).
    """
    pts = series_for_commodity(commodity, path)
    out: list[dict] = []
    prev_close: float | None = None
    for p in pts:
        lo, hi, cl = p.get("min_price"), p.get("max_price"), p.get("avg_price")
        if lo is None or hi is None or cl is None:
            continue
        lo_f, hi_f, cl_f = float(lo), float(hi), float(cl)
        op_f = float(prev_close) if prev_close is not None else cl_f
        out.append(
            {
                "day": p["day"],
                "open": op_f,
                "high": hi_f,
                "low": lo_f,
                "close": cl_f,
            }
        )
        prev_close = cl_f
    return out


def dashboard_summary(path: Path | None = None) -> dict:
    """Aggregates for the analytics dashboard (latest day vs prior day when available)."""
    with connect(path) as conn:
        agg = conn.execute(
            """
            SELECT COUNT(DISTINCT day), COUNT(DISTINCT commodity), MIN(day), MAX(day)
            FROM daily_prices
            """
        ).fetchone()
        if not agg or agg[0] == 0:
            return {"has_data": False}

        n_days, n_commodities, min_day, max_day = int(agg[0]), int(agg[1]), agg[2], agg[3]
        days_desc = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT day FROM daily_prices ORDER BY day DESC LIMIT 2"
            )
        ]
        latest = days_desc[0]
        prev = days_desc[1] if len(days_desc) > 1 else None

        cur_rows = conn.execute(
            "SELECT commodity, min_price, max_price, avg_price FROM daily_prices WHERE day=?",
            (latest,),
        ).fetchall()
        prev_map: dict[str, tuple[float | None, float | None, float | None]] = {}
        if prev:
            for r in conn.execute(
                "SELECT commodity, min_price, max_price, avg_price FROM daily_prices WHERE day=?",
                (prev,),
            ):
                prev_map[r["commodity"]] = (r["min_price"], r["max_price"], r["avg_price"])

    cheaper = higher = same = new_items = 0
    spreads: list[float] = []
    mins_latest: list[float] = []

    for r in cur_rows:
        mn = r["min_price"]
        if mn is not None:
            mins_latest.append(float(mn))
        mx, av = r["max_price"], r["avg_price"]
        if mn is not None and mx is not None:
            spreads.append(float(mx) - float(mn))

        key = r["commodity"]
        if key not in prev_map:
            new_items += 1
            continue
        pmn = prev_map[key][0]
        if pmn is None or mn is None:
            continue
        if float(mn) < float(pmn) - 1e-9:
            cheaper += 1
        elif float(mn) > float(pmn) + 1e-9:
            higher += 1
        else:
            same += 1

    mins_latest.sort()
    median_min = None
    if mins_latest:
        mid = len(mins_latest) // 2
        if len(mins_latest) % 2:
            median_min = mins_latest[mid]
        else:
            median_min = (mins_latest[mid - 1] + mins_latest[mid]) / 2

    mean_spread = sum(spreads) / len(spreads) if spreads else None

    return {
        "has_data": True,
        "distinct_days": n_days,
        "distinct_commodities": n_commodities,
        "first_day": min_day,
        "last_day": max_day,
        "latest_day": latest,
        "prior_day": prev,
        "min_cheaper_count": cheaper,
        "min_higher_count": higher,
        "min_same_count": same,
        "new_or_returning_count": new_items,
        "median_min_latest": median_min,
        "mean_min_max_spread_latest": mean_spread,
        "rows_latest_day": len(cur_rows),
    }
