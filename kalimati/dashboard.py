from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from kalimati.config import DASHBOARD_HOST, DASHBOARD_PORT
from kalimati.db import DB_PATH, list_commodities, series_for_commodity, snapshot_min_price_movements

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

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Kalimati prices</title>
  <script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    :root {
      --pbi-page: #f3f2f1;
      --pbi-card: #ffffff;
      --pbi-border: #edebe9;
      --pbi-text: #323130;
      --pbi-muted: #605e5c;
      --pbi-accent: #0078d4;
      --pbi-accent-hover: #106ebe;
      --positive: #107c10;
      --negative: #a4262c;
      --shadow-card: 0 0.6px 1.8px rgba(0, 0, 0, 0.08), 0 3.2px 7.2px rgba(0, 0, 0, 0.12);
      --radius: 4px;
      font-family: "Segoe UI", "Segoe UI Web (West European)", system-ui, -apple-system, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--pbi-page);
      color: var(--pbi-text);
      font-size: 14px;
      line-height: 1.45;
    }
    .app-header {
      background: var(--pbi-card);
      border-bottom: 1px solid var(--pbi-border);
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.04);
    }
    .app-header-inner {
      max-width: 1400px;
      margin: 0 auto;
      padding: 16px 28px 18px;
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px 32px;
    }
    .brand { display: flex; align-items: flex-start; gap: 14px; }
    .brand-mark {
      width: 4px;
      min-height: 44px;
      background: var(--pbi-accent);
      border-radius: 2px;
      margin-top: 2px;
      flex-shrink: 0;
    }
    .brand h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      color: var(--pbi-text);
      letter-spacing: -0.01em;
    }
    .brand-sub {
      margin: 4px 0 0;
      font-size: 12px;
      color: var(--pbi-muted);
      max-width: 420px;
      line-height: 1.4;
    }
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 16px 20px;
      align-items: flex-end;
    }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field label {
      font-size: 11px;
      font-weight: 600;
      color: var(--pbi-muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    select {
      min-width: 220px;
      height: 32px;
      padding: 0 28px 0 10px;
      border-radius: 2px;
      border: 1px solid #8a8886;
      background: var(--pbi-card);
      color: var(--pbi-text);
      font-size: 14px;
      font-family: inherit;
      cursor: pointer;
    }
    select:hover { border-color: var(--pbi-text); }
    select:focus {
      outline: none;
      border-color: var(--pbi-accent);
      box-shadow: 0 0 0 1px var(--pbi-accent);
    }
    .app-main {
      max-width: 1400px;
      margin: 0 auto;
      padding: 20px 28px 36px;
    }
    .content-grid { display: flex; flex-direction: column; gap: 16px; }
    .card {
      background: var(--pbi-card);
      border: 1px solid var(--pbi-border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-card);
    }
    .card-pad { padding: 16px 20px 18px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }
    .kpi {
      padding: 16px 18px;
      border-left: 3px solid var(--pbi-accent);
      background: var(--pbi-card);
    }
    .kpi-label {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--pbi-muted);
    }
    .kpi-value {
      margin-top: 6px;
      font-size: 28px;
      font-weight: 600;
      color: var(--pbi-text);
      letter-spacing: -0.02em;
      line-height: 1.2;
    }
    .kpi-foot { margin-top: 8px; font-size: 12px; color: var(--pbi-muted); }
    .kpi-delta.positive { color: var(--positive); font-weight: 600; }
    .kpi-delta.negative { color: var(--negative); font-weight: 600; }
    .card-title-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 8px 16px;
      padding: 14px 20px 0;
      margin-bottom: 4px;
    }
    .card-title-row strong {
      font-size: 14px;
      font-weight: 600;
      color: var(--pbi-text);
    }
    .card-title-row span { font-size: 12px; color: var(--pbi-muted); }
    .chart-card .card-title-row { margin-bottom: 0; padding-bottom: 8px; }
    .movements-card .movements-grid { padding: 8px 20px 18px; margin-top: 0; }
    .movements-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    @media (max-width: 900px) {
      .movements-grid { grid-template-columns: 1fr; }
    }
    .move-col {
      border-radius: 2px;
      padding: 12px 12px 10px;
      border: 1px solid var(--pbi-border);
      background: #faf9f8;
      min-height: 100px;
    }
    .move-col.move-down { border-top: 2px solid var(--positive); }
    .move-col.move-up { border-top: 2px solid var(--negative); }
    .move-col.move-neutral { border-top: 2px solid #a19f9d; }
    .move-col h3 {
      margin: 0 0 8px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--pbi-muted);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .move-col h3 .n {
      font-weight: 600;
      color: var(--pbi-text);
      font-size: 13px;
    }
    .move-col ul {
      list-style: none;
      margin: 0;
      padding: 0;
      max-height: 260px;
      overflow-y: auto;
    }
    .move-col li {
      padding: 8px 0;
      border-bottom: 1px solid var(--pbi-border);
      font-size: 12px;
    }
    .move-col li:last-child { border-bottom: none; }
    .move-name { display: block; font-weight: 600; color: var(--pbi-text); line-height: 1.35; }
    .move-detail { display: block; font-size: 11px; color: var(--pbi-muted); margin-top: 2px; }
    .movements-empty {
      padding: 24px 20px;
      text-align: center;
      color: var(--pbi-muted);
      font-size: 13px;
    }
    #chart {
      height: 440px;
      position: relative;
      width: calc(100% - 24px);
      margin: 0 12px 14px;
      border-radius: 2px;
      overflow: hidden;
      border: 1px solid var(--pbi-border);
      background: #fff;
    }
    footer.muted {
      margin-top: 20px;
      padding-top: 16px;
      border-top: 1px solid var(--pbi-border);
      font-size: 12px;
      color: var(--pbi-muted);
      line-height: 1.55;
    }
    footer.muted a { color: var(--pbi-accent); text-decoration: none; }
    footer.muted a:hover { color: var(--pbi-accent-hover); text-decoration: underline; }
    code { font-size: 0.92em; background: #f3f2f1; padding: 2px 6px; border-radius: 2px; border: 1px solid var(--pbi-border); }
  </style>
</head>
<body>
  <header class="app-header">
    <div class="app-header-inner">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true"></span>
        <div>
          <h1>Kalimati market prices</h1>
          <p class="brand-sub">Candlestick series (min–max wicks, prior vs current average in the body). Pick a commodity and range.</p>
        </div>
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

    /**
     * Real OHLC uses open/close = day-over-day average move; when averages barely change,
     * the body collapses to a hairline while wicks still show min–max (looks like a thin T).
     * Expand the body slightly (within the same low–high) so candles stay readable.
     */
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
          background: { type: L.ColorType.Solid, color: '#ffffff' },
          textColor: '#605e5c',
          fontSize: 12,
          fontFamily: '"Segoe UI", "Segoe UI Web (West European)", system-ui, sans-serif',
        },
        localization: {
          priceFormatter: (price) => 'NPR ' + fmtNpr(price),
        },
        grid: {
          vertLines: { color: '#edebe9' },
          horzLines: { color: '#edebe9' },
        },
        crosshair: {
          mode: L.CrosshairMode.Normal,
          vertLine: { color: '#c8c6c4', labelBackgroundColor: '#0078d4' },
          horzLine: { color: '#c8c6c4', labelBackgroundColor: '#0078d4' },
        },
        rightPriceScale: {
          borderColor: '#edebe9',
          scaleMargins: { top: 0.06, bottom: 0.1 },
        },
        timeScale: {
          borderColor: '#edebe9',
          timeVisible: false,
          secondsVisible: false,
          fixLeftEdge: false,
          fixRightEdge: false,
        },
      });

      const series = tvChart.addCandlestickSeries({
        upColor: '#1b7f3b',
        downColor: '#c0352b',
        borderVisible: true,
        borderUpColor: '#146b32',
        borderDownColor: '#a82d24',
        wickUpColor: '#1b7f3b',
        wickDownColor: '#c0352b',
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


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    path = db_path or DB_PATH

    @app.get("/")
    def index() -> str:
        return render_template_string(PAGE)

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

    return app


def run(host: str | None = None, port: int | None = None, db_path: Path | None = None) -> None:
    app = create_app(db_path)
    app.run(host=host or DASHBOARD_HOST, port=port or DASHBOARD_PORT, debug=False)
