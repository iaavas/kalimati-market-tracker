#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
LOG="${ROOT}/logs/dashboard.log"
mkdir -p "$(dirname "$LOG")"
nohup "$PY" -m kalimati dashboard >>"$LOG" 2>&1 &
echo "Started dashboard PID=$! log=$LOG"
echo "Open http://127.0.0.1:${KALIMATI_DASHBOARD_PORT:-8765}/"
