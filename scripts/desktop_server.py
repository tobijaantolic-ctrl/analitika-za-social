#!/usr/bin/env python3
"""Run the local dashboard as a desktop-managed service."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app


def main():
    parser = argparse.ArgumentParser(description="Run Analytics Dashboard local server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5455)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
