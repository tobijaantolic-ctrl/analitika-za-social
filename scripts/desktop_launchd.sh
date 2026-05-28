#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-5455}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"
exec "${PROJECT_DIR}/venv/bin/python" "${PROJECT_DIR}/scripts/desktop_server.py" --host "127.0.0.1" --port "${PORT}"
