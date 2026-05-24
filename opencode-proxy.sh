#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PID_FILE="${ROOT_DIR}/.opencode-proxy.pid"
LOG_FILE="${ROOT_DIR}/.opencode-proxy.log"
CONFIG_FILE="${ROOT_DIR}/opp.toml"
VERBOSE=0

usage() {
  cat <<'EOF'
Usage:
  ./opencode-proxy.sh start [--config PATH] [--verbose]
  ./opencode-proxy.sh stop [--verbose]
  ./opencode-proxy.sh restart [--config PATH] [--verbose]
  ./opencode-proxy.sh status [--verbose]

Behavior:
  - Creates .venv if it does not exist.
  - Installs this package into .venv if needed.
  - Starts the proxy in the background and writes .opencode-proxy.pid.
  - Writes process output to .opencode-proxy.log.
EOF
}

log() {
  if [[ "${VERBOSE}" == "1" ]]; then
    printf '[opencode-proxy] %s\n' "$*"
  fi
}

die() {
  printf '[opencode-proxy] %s\n' "$*" >&2
  exit 1
}

is_running() {
  [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null
}

ensure_venv() {
  if [[ ! -d "${VENV_DIR}" ]]; then
    log "creating virtualenv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
  fi

  log "installing/updating opencode-proxy in virtualenv"
  if [[ "${VERBOSE}" == "1" ]]; then
    "${VENV_DIR}/bin/python" -m pip install "${ROOT_DIR}"
  else
    "${VENV_DIR}/bin/python" -m pip install -q "${ROOT_DIR}"
  fi
}

server_url() {
  "${VENV_DIR}/bin/python" - "${CONFIG_FILE}" <<'PY'
import sys
import tomllib
from pathlib import Path

config = tomllib.loads(Path(sys.argv[1]).read_text())
host = config.get("server", {}).get("host", "127.0.0.1")
port = config.get("server", {}).get("port", 11435)
if host == "0.0.0.0":
    host = "127.0.0.1"
print(f"http://{host}:{port}")
PY
}

wait_until_ready() {
  local url
  url="$(server_url)"
  for _ in {1..50}; do
    if ! is_running; then
      tail -40 "${LOG_FILE}" >&2 || true
      die "proxy process exited before becoming ready"
    fi
    if curl -fsS "${url}/health" >/dev/null 2>&1; then
      log "ready at ${url}"
      return
    fi
    sleep 0.1
  done

  tail -40 "${LOG_FILE}" >&2 || true
  die "proxy did not become ready at ${url}"
}

start_proxy() {
  if is_running; then
    die "already running with pid $(cat "${PID_FILE}")"
  fi
  [[ -f "${CONFIG_FILE}" ]] || die "missing config: ${CONFIG_FILE}"

  ensure_venv
  log "starting with config ${CONFIG_FILE}"
  log "logs: ${LOG_FILE}"

  local args=("--config" "${CONFIG_FILE}")
  if [[ "${VERBOSE}" == "1" ]]; then
    args+=("--verbose")
  fi

  nohup "${VENV_DIR}/bin/opencode-proxy" "${args[@]}" >>"${LOG_FILE}" 2>&1 &
  echo "$!" >"${PID_FILE}"
  log "started pid $(cat "${PID_FILE}")"
  wait_until_ready
}

stop_proxy() {
  if ! is_running; then
    rm -f "${PID_FILE}"
    log "not running"
    return
  fi

  log "stopping pid $(cat "${PID_FILE}")"
  kill "$(cat "${PID_FILE}")"
  for _ in {1..30}; do
    if ! is_running; then
      rm -f "${PID_FILE}"
      log "stopped"
      return
    fi
    sleep 0.2
  done

  log "process did not exit after SIGTERM; sending SIGKILL"
  kill -9 "$(cat "${PID_FILE}")" 2>/dev/null || true
  rm -f "${PID_FILE}"
}

status_proxy() {
  if is_running; then
    printf 'running pid=%s log=%s\n' "$(cat "${PID_FILE}")" "${LOG_FILE}"
    return
  fi
  printf 'stopped\n'
}

COMMAND="${1:-}"
[[ -n "${COMMAND}" ]] || {
  usage
  exit 1
}
shift || true

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --config)
      [[ "$#" -ge 2 ]] || die "--config requires a path"
      CONFIG_FILE="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "${COMMAND}" in
  start)
    start_proxy
    ;;
  stop)
    stop_proxy
    ;;
  restart)
    stop_proxy
    start_proxy
    ;;
  status)
    status_proxy
    ;;
  *)
    usage
    exit 1
    ;;
esac
