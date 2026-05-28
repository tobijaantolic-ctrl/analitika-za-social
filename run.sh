#!/bin/bash
set -e
cd "$(dirname "$0")"

# Use Python 3.12 if available; greenlet/Playwright can lag newest Python releases.
PYTHON="${ANALYTICS_PYTHON:-python3}"

# Create venv if missing
if [ ! -d "venv" ]; then
  echo "Ustvarjam virtualno okolje..."
  "$PYTHON" -m venv venv
fi

source venv/bin/activate

# Install deps
pip install -q -r requirements.txt

# Install Playwright browsers (only if not already installed)
python -m playwright install chromium --quiet 2>/dev/null || true

echo ""
echo "  Social Analytics Dashboard"
echo "  → http://localhost:5455"
echo ""

python app.py
