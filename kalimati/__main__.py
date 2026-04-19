from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m kalimati")
    sub = parser.add_subparsers(dest="cmd", required=True)

    dash = sub.add_parser("dashboard", help="Run local trend dashboard (Flask).")
    dash.add_argument("--host", default=None)
    dash.add_argument("--port", type=int, default=None)
    dash.add_argument("--db", type=Path, default=None)

    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if load_dotenv is not None and env_path.exists():
        load_dotenv(env_path)

    if args.cmd == "dashboard":
        from kalimati.dashboard import run

        run(host=args.host, port=args.port, db_path=args.db)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
