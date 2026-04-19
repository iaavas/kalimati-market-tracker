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
