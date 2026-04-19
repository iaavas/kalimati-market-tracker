from __future__ import annotations

import re
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from kalimati.config import HOME_URL, USER_AGENT
from kalimati.db import PriceRow

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _clean_name(cell_html: str) -> str:
    soup = BeautifulSoup(cell_html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_money(cell: str) -> float | None:
    s = cell.translate(_DEV_DIGITS)
    s = s.replace("Rs.", "").replace("रू", "").replace("रु", "").strip()
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_homepage_html(url: str | None = None) -> str:
    u = url or HOME_URL
    r = requests.get(
        u,
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    return r.text


def parse_commodity_table(html: str) -> list[PriceRow]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="commodityDailyPrice")
    if table is None:
        raise RuntimeError("Could not find table#commodityDailyPrice in HTML")

    rows: list[PriceRow] = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        name = _clean_name(str(tds[0]))
        if not name:
            continue
        mn = _parse_money(tds[1].get_text(" ", strip=True))
        mx = _parse_money(tds[2].get_text(" ", strip=True))
        av = _parse_money(tds[3].get_text(" ", strip=True))
        rows.append(PriceRow(commodity=name, min_price=mn, max_price=mx, avg_price=av))
    if not rows:
        raise RuntimeError("Parsed zero commodity rows")
    return rows


def fetch_today_rows(url: str | None = None) -> list[PriceRow]:
    return parse_commodity_table(fetch_homepage_html(url))


def compare_to_previous(
    today_rows: Iterable[PriceRow],
    yesterday_map: dict[str, PriceRow],
) -> list[tuple[str, float | None, float | None]]:
    """Return list of (commodity, prev_min, today_min) where today is strictly cheaper on min price."""
    cheaper: list[tuple[str, float | None, float | None]] = []
    for row in today_rows:
        prev = yesterday_map.get(row.commodity)
        if prev is None or prev.min_price is None or row.min_price is None:
            continue
        if row.min_price < prev.min_price:
            cheaper.append((row.commodity, prev.min_price, row.min_price))
    return cheaper
