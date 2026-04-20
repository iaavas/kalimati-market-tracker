from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from kalimati.config import DASHBOARD_HOST, DASHBOARD_PORT
from kalimati.db import (
    DB_PATH,
    list_commodities,
    series_for_commodity,
    snapshot_min_price_movements,
    snapshot_today_prices,
)

# --- Reusable analytics (usable from scripts/tests without Flask) -----------------

PERIOD_DAYS = {"30": 30, "90": 90, "365": 365}


def filter_points_by_period(points: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    """Keep points whose day is within ``period`` days of the newest row. ``period`` is 'all' or '30'|'90'|'365'."""
    if not points or period in ("", "all"):
        return list(points)
    n = PERIOD_DAYS.get(period)
    if n is None:
        return list(points)
    last = date.fromisoformat(str(points[-1]["day"]))
    start = last - timedelta(days=n)
    return [p for p in points if date.fromisoformat(str(p["day"])) >= start]


def compute_kpis(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Latest vs prior average on rows that have ``avg_price``, plus counts."""
    empty: dict[str, Any] = {
        "latest_day": None,
        "latest_avg": None,
        "prior_avg": None,
        "change_abs": None,
        "change_pct": None,
        "n_days": len(points),
    }
    if not points:
        return empty

    with_avg = [p for p in points if p.get("avg_price") is not None]
    if not with_avg:
        empty["latest_day"] = points[-1].get("day")
        empty["n_days"] = len(points)
        return empty

    last = with_avg[-1]
    empty["latest_day"] = last.get("day")
    empty["latest_avg"] = last.get("avg_price")

    if len(with_avg) >= 2:
        prev = with_avg[-2]
        empty["prior_avg"] = prev.get("avg_price")
        la, pa = empty["latest_avg"], empty["prior_avg"]
        if la is not None and pa is not None:
            empty["change_abs"] = la - pa
            if pa != 0:
                empty["change_pct"] = (la - pa) / pa * 100.0

    empty["n_days"] = len(points)
    return empty


# --- UI ---------------------------------------------------------------------------

SHARED_CSS = """
    :root {
      --bg: #0a0a0a;
      --surface: #111111;
      --surface-elev: #141414;
      --border: #2a2a2a;
      --border-subtle: #1a1a1a;
      --text: #d4d4d4;
      --muted: #737373;
      --accent: #6b9fff;
      --accent-dim: #4a7dcc;
      --positive: #6ee7a8;
      --negative: #f87171;
      --neutral-bar: #525252;
      --radius: 0;
      font-family: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.45;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
    }
    .app-header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
    }
    .app-header-inner {
      max-width: 1400px;
      margin: 0 auto;
      padding: 14px 24px 16px;
    }
    .header-row {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px 28px;
    }
    .brand { display: flex; align-items: flex-start; gap: 12px; }
    .brand-mark {
      width: 3px;
      min-height: 40px;
      background: var(--accent);
      margin-top: 2px;
      flex-shrink: 0;
    }
    .brand h1 {
      margin: 0;
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: 0.02em;
      text-transform: lowercase;
    }
    .brand-sub {
      margin: 6px 0 0;
      font-size: 11px;
      color: var(--muted);
      max-width: 520px;
      line-height: 1.45;
      font-weight: 400;
    }
    .site-nav {
      display: flex;
      gap: 0;
      align-items: center;
      flex-shrink: 0;
      font-size: 11px;
      text-transform: lowercase;
      letter-spacing: 0.06em;
    }
    .site-nav a {
      color: var(--muted);
      text-decoration: none;
      padding: 6px 12px;
      border: 1px solid transparent;
      border-right: none;
    }
    .site-nav a:last-child { border-right: 1px solid var(--border); }
    .site-nav a:first-child { border-left: 1px solid var(--border); }
    .site-nav a:hover { color: var(--text); background: var(--surface-elev); }
    .site-nav a.active {
      color: var(--accent);
      background: var(--bg);
      border-color: var(--border);
    }
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 14px 20px;
      align-items: flex-end;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border-subtle);
    }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field label {
      font-size: 10px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    select {
      min-width: 200px;
      height: 30px;
      padding: 0 10px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      font-size: 12px;
      font-family: inherit;
      cursor: pointer;
    }
    select:hover { border-color: var(--muted); }
    select:focus {
      outline: none;
      border-color: var(--accent);
    }
    .app-main {
      max-width: 1400px;
      margin: 0 auto;
      padding: 18px 24px 32px;
    }
    .content-grid { display: flex; flex-direction: column; gap: 14px; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
    }
    .card-pad { padding: 14px 18px 16px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .kpi {
      padding: 12px 14px;
      border-left: 2px solid var(--accent);
      background: var(--bg);
    }
    .kpi-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }
    .kpi-value {
      margin-top: 6px;
      font-size: 22px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.02em;
      line-height: 1.2;
    }
    .kpi-foot { margin-top: 6px; font-size: 11px; color: var(--muted); }
    .kpi-delta.positive { color: var(--positive); font-weight: 600; }
    .kpi-delta.negative { color: var(--negative); font-weight: 600; }
    .card-title-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 8px 16px;
      padding: 12px 18px 0;
      margin-bottom: 2px;
    }
    .card-title-row strong {
      font-size: 12px;
      font-weight: 600;
      color: var(--text);
      text-transform: lowercase;
      letter-spacing: 0.04em;
    }
    .card-title-row span { font-size: 11px; color: var(--muted); }
    .chart-card .card-title-row { margin-bottom: 0; padding-bottom: 6px; }
    .movements-card .movements-grid { padding: 6px 18px 14px; margin-top: 0; }
    .movements-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
    }
    @media (max-width: 900px) {
      .movements-grid { grid-template-columns: 1fr; }
    }
    .move-col {
      padding: 10px 10px 8px;
      border: 1px solid var(--border);
      background: var(--bg);
      min-height: 88px;
    }
    .move-col.move-down { border-top: 2px solid var(--positive); }
    .move-col.move-up { border-top: 2px solid var(--negative); }
    .move-col.move-neutral { border-top: 2px solid var(--neutral-bar); }
    .move-col h3 {
      margin: 0 0 6px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .move-col h3 .n {
      font-weight: 600;
      color: var(--text);
      font-size: 12px;
    }
    .move-col ul {
      list-style: none;
      margin: 0;
      padding: 0;
      max-height: 240px;
      overflow-y: auto;
    }
    .move-col li {
      padding: 6px 0;
      border-bottom: 1px solid var(--border-subtle);
      font-size: 11px;
    }
    .move-col li:last-child { border-bottom: none; }
    .move-name { display: block; font-weight: 600; color: var(--text); line-height: 1.35; }
    .move-detail { display: block; font-size: 10px; color: var(--muted); margin-top: 2px; }
    .movements-empty {
      padding: 20px 18px;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
    }
    #chart {
      height: 440px;
      position: relative;
      width: calc(100% - 24px);
      margin: 0 12px 12px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: var(--bg);
    }
    footer.muted {
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
      line-height: 1.55;
    }
    footer.muted a { color: var(--accent-dim); text-decoration: none; }
    footer.muted a:hover { color: var(--accent); text-decoration: underline; }
    code {
      font-size: 0.95em;
      background: var(--bg);
      padding: 1px 5px;
      border: 1px solid var(--border);
    }
    .calc-hint {
      margin: 0 0 10px;
      padding: 0 18px;
      font-size: 11px;
      color: var(--muted);
      line-height: 1.45;
    }
    .calc-table-wrap { overflow-x: auto; padding: 0 18px; }
    .calc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .calc-table th {
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .calc-table td {
      padding: 6px 8px;
      border-bottom: 1px solid var(--border-subtle);
      vertical-align: middle;
    }
    .calc-table select {
      min-width: 160px;
      max-width: min(400px, 100%);
      width: 100%;
      height: 30px;
      padding: 0 8px;
      border: 1px solid var(--border);
      font-size: 12px;
      font-family: inherit;
      background: var(--bg);
      color: var(--text);
    }
    .calc-table input[type="number"] {
      width: 96px;
      height: 30px;
      padding: 0 8px;
      border: 1px solid var(--border);
      font-size: 12px;
      font-family: inherit;
      background: var(--bg);
      color: var(--text);
    }
    .calc-unit { color: var(--text); font-weight: 600; white-space: nowrap; }
    .calc-price {
      color: var(--text);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .calc-remove {
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 4px 10px;
      cursor: pointer;
      font-size: 11px;
      color: var(--muted);
      font-family: inherit;
      text-transform: lowercase;
    }
    .calc-remove:hover { color: var(--text); border-color: var(--muted); }
    .calc-add-btn {
      margin: 2px 18px 14px;
      height: 30px;
      padding: 0 14px;
      border: 1px solid var(--accent);
      background: var(--bg);
      color: var(--accent);
      font-weight: 600;
      font-size: 11px;
      cursor: pointer;
      font-family: inherit;
      text-transform: lowercase;
      letter-spacing: 0.04em;
    }
    .calc-add-btn:hover { background: var(--surface-elev); }
    .calc-totals {
      margin: 6px 18px 14px;
      padding: 12px 14px;
      background: var(--bg);
      border: 1px solid var(--border);
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
    }
    .calc-total-block .calc-total-label {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }
    .calc-total-block .calc-total-val {
      font-size: 16px;
      font-weight: 600;
      color: var(--text);
      margin-top: 2px;
      font-variant-numeric: tabular-nums;
    }
    .empty-state {
      padding: 28px 18px;
      text-align: center;
      font-size: 12px;
      color: var(--muted);
      border-bottom: 1px solid var(--border-subtle);
    }
"""


DASHBOARD_HTML = (
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Kalimati · dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
  <style>
"""
    + SHARED_CSS
    + """
  </style>
</head>
<body>
  <header class="app-header">
    <div class="app-header-inner">
      <div class="header-row">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true"></span>
          <div>
            <h1>Kalimati market prices</h1>
            <p class="brand-sub">Candlestick (min–max wicks, day-over-day average in the body). Scope a commodity and window.</p>
          </div>
        </div>
        <nav class="site-nav" aria-label="Sections">
          <a href="/" class="site-nav-link active">dashboard</a>
          <a href="/calculator" class="site-nav-link">calculator</a>
        </nav>
      </div>
      <div class="filters">
        <div class="field">
          <label for="commodity">Commodity</label>
          <select id="commodity"></select>
        </div>
        <div class="field">
          <label for="period">Time window</label>
          <select id="period">
            <option value="all">All available</option>
            <option value="365">Last 365 days</option>
            <option value="90" selected>Last 90 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </div>
      </div>
    </div>
  </header>

  <main class="app-main">
    <div class="content-grid">
      <section class="card card-pad">
        <div class="kpis">
          <div class="kpi">
            <div class="kpi-label">Latest average</div>
            <div class="kpi-value" id="kpi-avg">—</div>
            <div class="kpi-foot" id="kpi-day">No day selected</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Vs prior snapshot</div>
            <div class="kpi-value" id="kpi-delta">—</div>
            <div class="kpi-foot" id="kpi-delta-note">Change in average price</div>
          </div>
        </div>
      </section>

      <section class="card movements-card" id="movements-card">
        <div class="card-title-row">
          <strong>Min wholesale price vs prior day</strong>
          <span id="movements-range">—</span>
        </div>
        <div id="movements-empty" class="movements-empty" hidden>No SQLite data yet. Run <code>scripts/daily_job.py</code>.</div>
        <div class="movements-grid" id="movements-grid">
          <div class="move-col move-down">
            <h3>Price down <span class="n" id="cnt-down">0</span></h3>
            <ul id="list-down" aria-label="Commodities with lower minimum price"></ul>
          </div>
          <div class="move-col move-up">
            <h3>Price up <span class="n" id="cnt-up">0</span></h3>
            <ul id="list-up" aria-label="Commodities with higher minimum price"></ul>
          </div>
          <div class="move-col move-neutral">
            <h3>Neutral <span class="n" id="cnt-neutral">0</span></h3>
            <ul id="list-neutral" aria-label="Unchanged or not comparable"></ul>
          </div>
        </div>
      </section>

      <section class="card chart-card">
        <div class="card-title-row">
          <strong id="chart-title">Price trend</strong>
          <span id="chart-range"></span>
        </div>
        <div id="chart" role="img" aria-label="Price candlestick chart"></div>
      </section>
    </div>

    <footer class="muted">
      Data source: local SQLite (<code>data/prices.db</code>). Refresh with <code>scripts/daily_job.py</code>.
      Website: <a href="https://kalimatimarket.gov.np/" target="_blank" rel="noreferrer">kalimatimarket.gov.np</a>
    </footer>
  </main>

  <script>
    let tvChart = null;
    let chartResizeObs = null;

    function fmtNpr(n) {
      if (n == null || Number.isNaN(n)) return '—';
      return new Intl.NumberFormat('en-NP', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
    }

    function fmtPct(n) {
      if (n == null || Number.isNaN(n)) return '—';
      const sign = n > 0 ? '+' : '';
      return sign + n.toFixed(2) + '%';
    }

    function renderKpis(kpis) {
      const avgEl = document.getElementById('kpi-avg');
      const dayEl = document.getElementById('kpi-day');
      const deltaEl = document.getElementById('kpi-delta');
      const noteEl = document.getElementById('kpi-delta-note');

      if (!kpis.latest_day) {
        avgEl.textContent = '—';
        dayEl.textContent = 'No data in this window';
        deltaEl.textContent = '—';
        noteEl.textContent = 'Need at least one day with average price';
        deltaEl.className = '';
        return;
      }

      avgEl.textContent = kpis.latest_avg != null ? 'NPR ' + fmtNpr(kpis.latest_avg) : '—';
      dayEl.textContent = 'As of ' + kpis.latest_day + ' · ' + kpis.n_days + ' day(s) in range';

      const pct = kpis.change_pct;
      const abs = kpis.change_abs;
      if (kpis.prior_avg == null || pct == null) {
        deltaEl.textContent = '—';
        noteEl.textContent = 'Only one price point in this window';
        deltaEl.className = '';
        return;
      }

      deltaEl.textContent = fmtPct(pct) + ' (' + (abs >= 0 ? '+' : '') + fmtNpr(abs) + ')';
      deltaEl.className = 'kpi-delta ' + (pct >= 0 ? 'positive' : 'negative');
      noteEl.textContent = 'Compared to prior day with data in this window';
    }

    function ensureVisibleCandleBody(open, close, high, low) {
      let o = open, c = close, h = high, l = low;
      const range = h - l;
      const body = Math.abs(c - o);
      if (!isFinite(range) || range < 0) return { open: o, close: c, high: h, low: l };

      if (range === 0) {
        const pad = 0.5;
        const m = h;
        return {
          open: m - pad * 0.35,
          close: m + pad * 0.35,
          high: m + pad,
          low: m - pad,
        };
      }

      const minBody = Math.max(range * 0.07, 0.4);
      if (body >= minBody) return { open: o, close: c, high: h, low: l };

      const bearish = c < o;
      const mid = (o + c) / 2;
      let half = Math.min(minBody / 2, range / 2 - 1e-6);
      if (half <= 0) {
        o = l;
        c = h;
        if (bearish) { o = h; c = l; }
        return { open: o, close: c, high: h, low: l };
      }
      if (!bearish) {
        o = mid - half;
        c = mid + half;
      } else {
        o = mid + half;
        c = mid - half;
      }
      let loB = Math.min(o, c);
      let hiB = Math.max(o, c);
      if (loB < l) {
        const d = l - loB;
        o += d;
        c += d;
      }
      loB = Math.min(o, c);
      hiB = Math.max(o, c);
      if (hiB > h) {
        const d = hiB - h;
        o -= d;
        c -= d;
      }
      return { open: o, close: c, high: h, low: l };
    }

    function buildCandleData(points) {
      const rows = [];
      for (let i = 0; i < points.length; i++) {
        const p = points[i];
        const prev = i > 0 ? points[i - 1] : null;
        const minV = p.min_price;
        const maxV = p.max_price;
        const avgV = p.avg_price;
        let c = avgV;
        if (c == null || Number.isNaN(c)) {
          if (minV != null && maxV != null) c = (minV + maxV) / 2;
          else c = minV ?? maxV;
        }
        if (c == null || Number.isNaN(c)) continue;
        let o;
        if (prev) {
          o = prev.avg_price;
          if (o == null || Number.isNaN(o)) {
            const pm = prev.min_price, px = prev.max_price;
            if (pm != null && px != null) o = (pm + px) / 2;
            else o = pm ?? px ?? c;
          }
        } else {
          o = c;
        }
        let h = maxV != null ? maxV : Math.max(o, c);
        let l = minV != null ? minV : Math.min(o, c);
        if (l > h) { const t = l; l = h; h = t; }
        const adj = ensureVisibleCandleBody(o, c, h, l);
        rows.push({
          time: String(p.day).slice(0, 10),
          open: adj.open,
          high: adj.high,
          low: adj.low,
          close: adj.close,
        });
      }
      return rows;
    }

    function renderChart(payload) {
      const el = document.getElementById('chart');
      const L = window.LightweightCharts;
      if (!L) {
        console.error('LightweightCharts failed to load');
        return;
      }

      if (tvChart) {
        tvChart.remove();
        tvChart = null;
      }
      if (chartResizeObs) {
        chartResizeObs.disconnect();
        chartResizeObs = null;
      }

      const candleData = buildCandleData(payload.points);
      const h = 440;

      document.getElementById('chart-title').textContent = (payload.commodity || 'Price') + ' · candlestick';
      const r = payload.period === 'all' ? 'Full history' : 'Last ' + payload.period + ' days';
      document.getElementById('chart-range').textContent = r + ' · ' + candleData.length + ' day(s)';

      const w = Math.max(200, Math.floor(el.clientWidth || el.parentElement.clientWidth || 600));

      tvChart = L.createChart(el, {
        width: w,
        height: h,
        layout: {
          background: { type: L.ColorType.Solid, color: '#0a0a0a' },
          textColor: '#737373',
          fontSize: 11,
          fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
        },
        localization: {
          priceFormatter: (price) => 'NPR ' + fmtNpr(price),
        },
        grid: {
          vertLines: { color: '#1a1a1a' },
          horzLines: { color: '#1a1a1a' },
        },
        crosshair: {
          mode: L.CrosshairMode.Normal,
          vertLine: { color: '#404040', labelBackgroundColor: '#1f1f1f' },
          horzLine: { color: '#404040', labelBackgroundColor: '#1f1f1f' },
        },
        rightPriceScale: {
          borderColor: '#2a2a2a',
          scaleMargins: { top: 0.06, bottom: 0.1 },
        },
        timeScale: {
          borderColor: '#2a2a2a',
          timeVisible: false,
          secondsVisible: false,
          fixLeftEdge: false,
          fixRightEdge: false,
        },
      });

      const series = tvChart.addCandlestickSeries({
        upColor: '#4ade80',
        downColor: '#f87171',
        borderVisible: true,
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#4ade80',
        wickDownColor: '#f87171',
        wickVisible: true,
        priceLineVisible: false,
      });
      series.setData(candleData);
      tvChart.timeScale().fitContent();

      chartResizeObs = new ResizeObserver(() => {
        if (!tvChart) return;
        const nw = Math.max(200, Math.floor(el.clientWidth));
        tvChart.resize(nw, h);
      });
      chartResizeObs.observe(el);

      renderKpis(payload.kpis);
    }

    async function loadSeries(commodity) {
      const period = document.getElementById('period').value;
      const q = new URLSearchParams({ commodity, period });
      const res = await fetch('/api/series?' + q.toString());
      const payload = await res.json();
      renderChart(payload);
    }

    async function loadCommodities() {
      const res = await fetch('/api/commodities');
      const data = await res.json();
      const sel = document.getElementById('commodity');
      sel.innerHTML = '';
      for (const name of data.items) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
      }
      if (data.items.length) await loadSeries(data.items[0]);
    }

    document.getElementById('commodity').addEventListener('change', (e) => loadSeries(e.target.value));
    document.getElementById('period').addEventListener('change', () => {
      const sel = document.getElementById('commodity');
      if (sel.value) loadSeries(sel.value);
    });

    function renderMovements(m) {
      const emptyEl = document.getElementById('movements-empty');
      const gridEl = document.getElementById('movements-grid');
      const rangeEl = document.getElementById('movements-range');
      const ul = (id) => document.getElementById(id);

      for (const id of ['list-down', 'list-up', 'list-neutral']) {
        ul(id).innerHTML = '';
      }

      if (!m || !m.has_data) {
        emptyEl.hidden = false;
        gridEl.hidden = true;
        rangeEl.textContent = '—';
        document.getElementById('cnt-down').textContent = '0';
        document.getElementById('cnt-up').textContent = '0';
        document.getElementById('cnt-neutral').textContent = '0';
        return;
      }

      emptyEl.hidden = true;
      gridEl.hidden = false;

      const ld = m.latest_day || '';
      const pd = m.prior_day;
      rangeEl.textContent = pd
        ? (ld + ' vs ' + pd + ' · min price')
        : (ld + ' · no prior day in DB (all neutral)');

      function fillList(listId, items) {
        const root = ul(listId);
        for (const it of items) {
          const li = document.createElement('li');
          const name = document.createElement('span');
          name.className = 'move-name';
          name.textContent = it.commodity;
          li.appendChild(name);
          const detail = document.createElement('span');
          detail.className = 'move-detail';
          if (it.delta != null && it.prior_min != null && it.min != null) {
            const sign = it.delta > 0 ? '+' : '';
            detail.textContent = 'NPR ' + fmtNpr(it.prior_min) + ' → ' + fmtNpr(it.min)
              + ' (' + sign + fmtNpr(it.delta) + ')';
          } else if (it.min != null && it.prior_min == null) {
            detail.textContent = 'NPR ' + fmtNpr(it.min) + ' · no prior row';
          } else if (it.min == null) {
            detail.textContent = 'No min price';
          } else {
            detail.textContent = 'NPR ' + fmtNpr(it.min) + ' · same vs prior';
          }
          li.appendChild(detail);
          root.appendChild(li);
        }
      }

      fillList('list-down', m.down || []);
      fillList('list-up', m.up || []);
      fillList('list-neutral', m.neutral || []);

      document.getElementById('cnt-down').textContent = String((m.down || []).length);
      document.getElementById('cnt-up').textContent = String((m.up || []).length);
      document.getElementById('cnt-neutral').textContent = String((m.neutral || []).length);
    }

    async function loadMovements() {
      try {
        const res = await fetch('/api/movements');
        const m = await res.json();
        renderMovements(m);
      } catch (e) {
        renderMovements({ has_data: false });
      }
    }

    loadCommodities();
    loadMovements();
  </script>
</body>
</html>
"""
)


CALCULATOR_HTML = (
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Kalimati · basket calculator</title>
  <style>
"""
    + SHARED_CSS
    + """
  </style>
</head>
<body>
  <header class="app-header">
    <div class="app-header-inner">
      <div class="header-row">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true"></span>
          <div>
            <h1>Basket calculator</h1>
            <p class="brand-sub">Latest snapshot wholesale rates. Quantity is in each line's unit (e.g. केजी). Line total uses min / avg / max for that item.</p>
          </div>
        </div>
        <nav class="site-nav" aria-label="Sections">
          <a href="/" class="site-nav-link">dashboard</a>
          <a href="/calculator" class="site-nav-link active">calculator</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="app-main">
    <div class="content-grid">
      <section class="card" id="calc-card">
        <div id="calc-empty" class="empty-state" hidden>
          No snapshot in SQLite. Run <code>scripts/daily_job.py</code> to ingest prices.
        </div>
        <div id="calc-body" hidden>
          <div class="card-title-row">
            <strong>Basket lines</strong>
            <span id="calc-day">—</span>
          </div>
          <p class="calc-hint">
            Each row shows wholesale <strong>avg</strong> per unit for the selected item (same day as totals).
            Totals multiply quantity by snapshot <strong>min</strong>, <strong>avg</strong>, or <strong>max</strong>.
          </p>
          <div class="calc-table-wrap">
            <table class="calc-table" aria-label="Basket calculator">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Qty</th>
                  <th>Unit</th>
                  <th>Avg / unit</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="calc-tbody"></tbody>
            </table>
          </div>
          <button type="button" class="calc-add-btn" id="calc-add">Add item</button>
          <div class="calc-totals">
            <div class="calc-total-block">
              <div class="calc-total-label">Total (min)</div>
              <div class="calc-total-val" id="calc-total-min">—</div>
            </div>
            <div class="calc-total-block">
              <div class="calc-total-label">Total (avg)</div>
              <div class="calc-total-val" id="calc-total-avg">—</div>
            </div>
            <div class="calc-total-block">
              <div class="calc-total-label">Total (max)</div>
              <div class="calc-total-val" id="calc-total-max">—</div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <footer class="muted">
      Data source: local SQLite (<code>data/prices.db</code>). Refresh with <code>scripts/daily_job.py</code>.
      Website: <a href="https://kalimatimarket.gov.np/" target="_blank" rel="noreferrer">kalimatimarket.gov.np</a>
    </footer>
  </main>

  <script>
    function fmtNpr(n) {
      if (n == null || Number.isNaN(n)) return '—';
      return new Intl.NumberFormat('en-NP', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
    }

    let priceCatalog = [];
    let priceMap = {};
    let calcLines = [{ id: 1, commodity: '', qty: 1 }];
    let calcNextId = 2;

    function recalcTotals() {
      let tMin = 0;
      let tAvg = 0;
      let tMax = 0;
      let anyMin = false;
      let anyAvg = false;
      let anyMax = false;
      for (const line of calcLines) {
        if (!line.commodity) continue;
        const p = priceMap[line.commodity];
        if (!p) continue;
        const q = Number(line.qty);
        if (!Number.isFinite(q) || q <= 0) continue;
        if (p.min_price != null) {
          tMin += q * p.min_price;
          anyMin = true;
        }
        if (p.avg_price != null) {
          tAvg += q * p.avg_price;
          anyAvg = true;
        }
        if (p.max_price != null) {
          tMax += q * p.max_price;
          anyMax = true;
        }
      }
      document.getElementById('calc-total-min').textContent = anyMin ? ('NPR ' + fmtNpr(tMin)) : '—';
      document.getElementById('calc-total-avg').textContent = anyAvg ? ('NPR ' + fmtNpr(tAvg)) : '—';
      document.getElementById('calc-total-max').textContent = anyMax ? ('NPR ' + fmtNpr(tMax)) : '—';
    }

    function renderCalcRows() {
      const tb = document.getElementById('calc-tbody');
      tb.innerHTML = '';
      for (const line of calcLines) {
        const tr = document.createElement('tr');

        const td1 = document.createElement('td');
        const sel = document.createElement('select');
        const opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = '— select —';
        sel.appendChild(opt0);
        for (const it of priceCatalog) {
          const o = document.createElement('option');
          o.value = it.commodity;
          o.textContent = it.commodity;
          if (it.commodity === line.commodity) o.selected = true;
          sel.appendChild(o);
        }
        sel.addEventListener('change', () => {
          line.commodity = sel.value;
          renderCalcRows();
          recalcTotals();
        });
        td1.appendChild(sel);

        const td2 = document.createElement('td');
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.min = '0';
        inp.step = 'any';
        inp.value = String(line.qty);
        inp.addEventListener('input', () => {
          const v = parseFloat(inp.value);
          line.qty = Number.isFinite(v) ? v : 0;
          recalcTotals();
        });
        td2.appendChild(inp);

        const td3 = document.createElement('td');
        const pr = line.commodity ? priceMap[line.commodity] : null;
        td3.textContent = pr ? pr.unit : '—';
        td3.className = 'calc-unit';

        const td4 = document.createElement('td');
        td4.className = 'calc-price';
        if (pr && pr.avg_price != null && !Number.isNaN(Number(pr.avg_price))) {
          td4.textContent = 'NPR ' + fmtNpr(pr.avg_price);
        } else {
          td4.textContent = '—';
        }

        const td5 = document.createElement('td');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'calc-remove';
        btn.textContent = 'remove';
        btn.addEventListener('click', () => {
          if (calcLines.length <= 1) {
            line.commodity = '';
            line.qty = 1;
            renderCalcRows();
            recalcTotals();
            return;
          }
          calcLines = calcLines.filter((l) => l.id !== line.id);
          renderCalcRows();
          recalcTotals();
        });
        td5.appendChild(btn);

        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        tr.appendChild(td4);
        tr.appendChild(td5);
        tb.appendChild(tr);
      }
    }

    async function loadTodayPrices() {
      const emptyEl = document.getElementById('calc-empty');
      const bodyEl = document.getElementById('calc-body');
      try {
        const res = await fetch('/api/today-prices');
        const data = await res.json();
        if (!data.has_data) {
          emptyEl.hidden = false;
          bodyEl.hidden = true;
          return;
        }
        emptyEl.hidden = true;
        bodyEl.hidden = false;
        document.getElementById('calc-day').textContent = data.day;
        priceCatalog = data.items || [];
        priceMap = {};
        for (const it of priceCatalog) {
          priceMap[it.commodity] = it;
        }
        if (calcLines.length === 1 && !calcLines[0].commodity && priceCatalog.length) {
          calcLines[0].commodity = priceCatalog[0].commodity;
        }
        renderCalcRows();
        recalcTotals();
      } catch (e) {
        emptyEl.hidden = false;
        bodyEl.hidden = true;
      }
    }

    document.getElementById('calc-add').addEventListener('click', () => {
      calcLines.push({ id: calcNextId++, commodity: '', qty: 1 });
      renderCalcRows();
      recalcTotals();
    });

    loadTodayPrices();
  </script>
</body>
</html>
"""
)


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    path = db_path or DB_PATH

    @app.get("/")
    def index() -> str:
        return render_template_string(DASHBOARD_HTML)

    @app.get("/calculator")
    def calculator() -> str:
        return render_template_string(CALCULATOR_HTML)

    @app.get("/api/commodities")
    def commodities() -> Response:
        return jsonify({"items": list_commodities(path)})

    @app.get("/api/series")
    def series() -> Response:
        name = request.args.get("commodity", "").strip()
        if not name:
            return jsonify({"error": "missing commodity"}), 400
        period = request.args.get("period", "all").strip().lower()
        raw = series_for_commodity(name, path)
        filtered = filter_points_by_period(raw, period)
        kpis = compute_kpis(filtered)
        return jsonify(
            {
                "commodity": name,
                "period": period,
                "points": filtered,
                "kpis": kpis,
            }
        )

    @app.get("/api/movements")
    def movements() -> Response:
        return jsonify(snapshot_min_price_movements(path))

    @app.get("/api/today-prices")
    def today_prices() -> Response:
        return jsonify(snapshot_today_prices(path))

    return app


def run(host: str | None = None, port: int | None = None, db_path: Path | None = None) -> None:
    app = create_app(db_path)
    app.run(host=host or DASHBOARD_HOST,
            port=port or DASHBOARD_PORT, debug=False)
