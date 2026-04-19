from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string

from kalimati.config import DASHBOARD_HOST, DASHBOARD_PORT
from kalimati.db import DB_PATH, list_commodities, series_for_commodity

INDEX = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Kalimati price trends</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root { color-scheme: light; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background:#0b1220; color:#e5e7eb; }
    header { padding: 18px 22px; border-bottom: 1px solid #1f2937; display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    h1 { font-size: 18px; margin: 0; font-weight: 650; letter-spacing: .2px; }
    main { padding: 18px 22px; max-width: 1100px; margin: 0 auto; }
    label { font-size: 13px; color:#cbd5e1; }
    select { margin-left: 8px; padding: 8px 10px; border-radius: 10px; border: 1px solid #334155; background:#0f172a; color:#e5e7eb; min-width: 320px; }
    .card { margin-top: 16px; background:#0f172a; border:1px solid #1f2937; border-radius: 14px; padding: 14px; }
    canvas { width: 100% !important; height: 420px !important; }
    .muted { color:#94a3b8; font-size: 13px; margin-top: 10px; line-height: 1.45; }
    a { color:#5eead4; }
  </style>
</head>
<body>
  <header>
    <h1>Kalimati price trends</h1>
    <div>
      <label for="commodity">Commodity</label>
      <select id="commodity"></select>
    </div>
  </header>
  <main>
    <div class="card">
      <canvas id="chart"></canvas>
      <div class="muted">
        Data is read from your local SQLite file. Run <code>scripts/daily_job.py</code> daily to append snapshots.
        Source site: <a href="https://kalimatimarket.gov.np/" target="_blank" rel="noreferrer">kalimatimarket.gov.np</a>
      </div>
    </div>
  </main>
  <script>
    let chart;
    async function loadCommodities() {
      const res = await fetch('/api/commodities');
      const data = await res.json();
      const sel = document.getElementById('commodity');
      sel.innerHTML = '';
      for (const name of data.items) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      }
      if (data.items.length) await loadSeries(data.items[0]);
    }
    async function loadSeries(name) {
      const res = await fetch('/api/series?commodity=' + encodeURIComponent(name));
      const payload = await res.json();
      const labels = payload.points.map(p => p.day);
      const mins = payload.points.map(p => p.min_price);
      const maxs = payload.points.map(p => p.max_price);
      const avgs = payload.points.map(p => p.avg_price);
      const cfg = {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Min', data: mins, borderColor: '#34d399', tension: 0.15, spanGaps: true },
            { label: 'Avg', data: avgs, borderColor: '#60a5fa', tension: 0.15, spanGaps: true },
            { label: 'Max', data: maxs, borderColor: '#fb7185', tension: 0.15, spanGaps: true },
          ]
        },
        options: {
          responsive: true,
          plugins: { legend: { labels: { color: '#e5e7eb' } } },
          scales: {
            x: { ticks: { color: '#94a3b8', maxRotation: 0, autoSkip: true } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#1f2937' } }
          }
        }
      };
      if (chart) chart.destroy();
      chart = new Chart(document.getElementById('chart'), cfg);
    }
    document.getElementById('commodity').addEventListener('change', (e) => {
      loadSeries(e.target.value);
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
        return render_template_string(INDEX)

    @app.get("/api/commodities")
    def commodities() -> Response:
        return jsonify({"items": list_commodities(path)})

    @app.get("/api/series")
    def series() -> Response:
        from flask import request

        name = request.args.get("commodity", "")
        if not name:
            return jsonify({"error": "missing commodity"}), 400
        return jsonify({"commodity": name, "points": series_for_commodity(name, path)})

    return app


def run(host: str | None = None, port: int | None = None, db_path: Path | None = None) -> None:
    app = create_app(db_path)
    app.run(host=host or DASHBOARD_HOST, port=port or DASHBOARD_PORT, debug=False)
