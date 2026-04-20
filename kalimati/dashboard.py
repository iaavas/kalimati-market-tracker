from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from kalimati.config import DASHBOARD_HOST, DASHBOARD_PORT
from kalimati.db import DB_PATH, list_commodities, series_for_commodity

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
      --bg: #eceef2;
      --surface: #ffffff;
      --border: #d4d8e0;
      --text: #1c1f26;
      --muted: #5c6370;
      --accent: #1f6feb;
      --accent-soft: #e8f1ff;
      --positive: #1b7f3b;
      --negative: #c0352b;
      --shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 4px 16px rgba(15, 23, 42, 0.06);
      --radius: 12px;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(180deg, #e4e7ee 0%, var(--bg) 32%);
      color: var(--text);
    }
    .shell { max-width: 1180px; margin: 0 auto; padding: 28px 22px 40px; }
    .title-row {
      display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
      gap: 16px 24px; margin-bottom: 20px;
    }
    h1 {
      margin: 0;
      font-size: 1.35rem;
      font-weight: 650;
      letter-spacing: -0.02em;
    }
    .subtitle { margin: 6px 0 0; font-size: 0.875rem; color: var(--muted); max-width: 520px; line-height: 1.45; }
    .filters {
      display: flex; flex-wrap: wrap; gap: 14px 18px; align-items: flex-end;
      background: var(--surface);
      padding: 14px 16px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    select {
      min-width: 240px;
      padding: 9px 11px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-size: 0.9rem;
    }
    select:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
    .kpis {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }
    .kpi {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 18px 16px;
      box-shadow: var(--shadow);
      border-top: 3px solid var(--accent);
    }
    .kpi-label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    .kpi-value { margin-top: 8px; font-size: 1.75rem; font-weight: 700; letter-spacing: -0.03em; }
    .kpi-foot { margin-top: 10px; font-size: 0.8125rem; color: var(--muted); }
    .kpi-delta.positive { color: var(--positive); font-weight: 600; }
    .kpi-delta.negative { color: var(--negative); font-weight: 600; }
    .chart-card {
      margin-top: 18px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px 14px 8px;
      box-shadow: var(--shadow);
    }
    .chart-head {
      display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap;
      gap: 8px; padding: 0 6px 12px;
    }
    .chart-head span { font-size: 0.875rem; color: var(--muted); }
    #chart {
      width: 100%;
      height: 420px;
      position: relative;
      border-radius: 10px;
      overflow: hidden;
    }
    footer.muted {
      margin-top: 18px; font-size: 0.8125rem; color: var(--muted); line-height: 1.5;
    }
    footer.muted a { color: var(--accent); text-decoration: none; }
    footer.muted a:hover { text-decoration: underline; }
    code { font-size: 0.85em; background: #f0f2f7; padding: 2px 6px; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="shell">
    <div class="title-row">
      <div>
        <h1>Kalimati market prices</h1>
        <p class="subtitle">Candlesticks (TradingView Lightweight Charts): wicks = min–max range; body = prior day’s average → today’s average (green when the average drops). Change commodity and window below.</p>
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

    <div class="chart-card">
      <div class="chart-head">
        <strong id="chart-title">Price trend</strong>
        <span id="chart-range"></span>
      </div>
      <div id="chart" role="img" aria-label="Price candlestick chart"></div>
    </div>

    <footer class="muted">
      Data source: local SQLite (<code>data/prices.db</code>). Refresh with <code>scripts/daily_job.py</code>.
      Website: <a href="https://kalimatimarket.gov.np/" target="_blank" rel="noreferrer">kalimatimarket.gov.np</a>
    </footer>
  </div>

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
      const h = 420;

      document.getElementById('chart-title').textContent = (payload.commodity || 'Price') + ' · candlestick';
      const r = payload.period === 'all' ? 'Full history' : 'Last ' + payload.period + ' days';
      document.getElementById('chart-range').textContent = r + ' · ' + candleData.length + ' day(s)';

      const w = Math.max(200, Math.floor(el.clientWidth || el.parentElement.clientWidth || 600));

      tvChart = L.createChart(el, {
        width: w,
        height: h,
        layout: {
          background: { type: L.ColorType.Solid, color: '#ffffff' },
          textColor: '#5c6370',
          fontSize: 12,
          fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif',
        },
        localization: {
          priceFormatter: (price) => 'NPR ' + fmtNpr(price),
        },
        grid: {
          vertLines: { color: '#eceef2' },
          horzLines: { color: '#eceef2' },
        },
        crosshair: {
          mode: L.CrosshairMode.Normal,
          vertLine: { color: '#c5cad6', labelBackgroundColor: '#1f6feb' },
          horzLine: { color: '#c5cad6', labelBackgroundColor: '#1f6feb' },
        },
        rightPriceScale: {
          borderColor: '#d4d8e0',
          scaleMargins: { top: 0.06, bottom: 0.1 },
        },
        timeScale: {
          borderColor: '#d4d8e0',
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

    loadCommodities();
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

    return app


def run(host: str | None = None, port: int | None = None, db_path: Path | None = None) -> None:
    app = create_app(db_path)
    app.run(host=host or DASHBOARD_HOST, port=port or DASHBOARD_PORT, debug=False)
