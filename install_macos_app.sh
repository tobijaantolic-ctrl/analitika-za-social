#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Analytics Dashboard.app"
SOURCE_APP="${PROJECT_DIR}/macos/${APP_NAME}"
TARGET_DIR="${HOME}/Applications"
TARGET_APP="${TARGET_DIR}/${APP_NAME}"
APP_SUPPORT="${HOME}/Library/Application Support/Analytics Dashboard"
RUNTIME_DIR="${APP_SUPPORT}/runtime"

mkdir -p "${TARGET_DIR}" "${RUNTIME_DIR}" "${RUNTIME_DIR}/uploads/tiktok"

if [[ ! -f "${RUNTIME_DIR}/analytics.db" && -f "${PROJECT_DIR}/analytics.db" ]]; then
  cp "${PROJECT_DIR}/analytics.db" "${RUNTIME_DIR}/analytics.db"
fi

rsync -a --delete \
  --exclude ".git/" \
  --exclude "venv/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude "analytics.db" \
  --exclude "artifacts/" \
  --exclude "macos/" \
  --exclude "uploads/tiktok/*.mp4" \
  --exclude "uploads/tiktok/*.mov" \
  --exclude "uploads/tiktok/*.m4v" \
  --exclude "uploads/tiktok/*.webm" \
  "${PROJECT_DIR}/" "${RUNTIME_DIR}/"

chmod +x "${PROJECT_DIR}/scripts/desktop_control.sh"
chmod +x "${PROJECT_DIR}/scripts/desktop_launchd.sh"
chmod +x "${PROJECT_DIR}/scripts/desktop_server.py"
chmod +x "${SOURCE_APP}/Contents/MacOS/Analytics Dashboard"
chmod +x "${RUNTIME_DIR}/scripts/desktop_control.sh"
chmod +x "${RUNTIME_DIR}/scripts/desktop_launchd.sh"
chmod +x "${RUNTIME_DIR}/scripts/desktop_server.py"

rm -rf "${TARGET_APP}"
cp -R "${SOURCE_APP}" "${TARGET_APP}"
chmod +x "${TARGET_APP}/Contents/MacOS/Analytics Dashboard"
xattr -dr com.apple.quarantine "${TARGET_APP}" 2>/dev/null || true
xattr -dr com.apple.quarantine "${RUNTIME_DIR}" 2>/dev/null || true

echo "Nameščeno: ${TARGET_APP}"
echo "Runtime: ${RUNTIME_DIR}"
echo "Odpri aplikacijo iz mape Applications ali zaženi:"
echo "open '${TARGET_APP}'"
