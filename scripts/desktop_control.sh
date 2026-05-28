#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ANALYTICS_DASHBOARD_PORT:-5455}"
HOST="127.0.0.1"
BASE_URL="http://${HOST}:${PORT}"
APP_SUPPORT="${HOME}/Library/Application Support/Analytics Dashboard"
LOG_DIR="${HOME}/Library/Logs/Analytics Dashboard"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
LABEL="local.analytics.dashboard"
PLIST_FILE="${AGENTS_DIR}/${LABEL}.plist"
LOG_FILE="${LOG_DIR}/server.log"
ERR_FILE="${LOG_DIR}/server.err.log"
VENV_PYTHON="${ROOT_DIR}/venv/bin/python"
BOOTSTRAP_PYTHON="${ANALYTICS_BOOTSTRAP_PYTHON:-python3}"

mkdir -p "${APP_SUPPORT}" "${LOG_DIR}" "${AGENTS_DIR}"

is_pid_alive() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

server_pid() {
  lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

health_ok() {
  curl -fsS "${BASE_URL}/health" >/dev/null 2>&1
}

ensure_venv() {
  if [[ ! -x "${VENV_PYTHON}" ]]; then
    "${BOOTSTRAP_PYTHON}" -m venv "${ROOT_DIR}/venv"
  fi
  "${VENV_PYTHON}" -m pip install -q -r "${ROOT_DIR}/requirements.txt"
}

write_launch_agent() {
  cat >"${PLIST_FILE}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${ROOT_DIR}/scripts/desktop_launchd.sh</string>
    <string>${PORT}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>RunAtLoad</key>
  <false/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${ERR_FILE}</string>
</dict>
</plist>
PLIST
}

launch_agent_loaded() {
  launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1
}

start_server() {
  if health_ok; then
    return 0
  fi

  ensure_venv
  write_launch_agent
  if ! launch_agent_loaded; then
    launchctl bootstrap "gui/$(id -u)" "${PLIST_FILE}" 2>/dev/null || true
  fi
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

  for _ in {1..40}; do
    if health_ok; then
      return 0
    fi
    sleep 0.25
  done

  echo "Streznik se ni zagnal. Poglej log: ${LOG_FILE}" >&2
  return 1
}

stop_server() {
  local pid
  pid="$(server_pid)"
  if is_pid_alive "${pid}"; then
    kill "${pid}" || true
    for _ in {1..20}; do
      if ! is_pid_alive "${pid}"; then
        break
      fi
      sleep 0.2
    done
  fi
  launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
}

status_server() {
  local pid
  pid="$(server_pid)"
  if health_ok; then
    echo "Analytics Dashboard tece: ${BASE_URL}"
    [[ -n "${pid}" ]] && echo "PID: ${pid}"
  else
    echo "Analytics Dashboard ne tece."
    return 1
  fi
}

open_app() {
  start_server
  open "${BASE_URL}/"
}

case "${1:-open}" in
  open) open_app ;;
  start) start_server ;;
  stop) stop_server ;;
  restart) stop_server; start_server ;;
  status) status_server ;;
  logs) open "${LOG_FILE}" ;;
  url) echo "${BASE_URL}/" ;;
  *) echo "Uporaba: $0 {open|start|stop|restart|status|logs|url}" >&2; exit 2 ;;
esac
