#!/usr/bin/env bash
# macOS/Linux launcher: frees the UI port, keeps the Mac awake (caffeinate) and starts the app.
# ON_NO_KILL=1 skips the cleanup of an older instance (launchd sets it).
# Logs (also when launchd starts this script): what this script does goes to <logs>/run.log; once the app runs,
# everything it prints goes to <logs>/app.log, problems of the data collection to <logs>/collect.log.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1

# env_value NAME DEFAULT: real environment variable, else NAME from .env, else DEFAULT
env_value() {
    local v="${!1:-}"
    if [ -z "$v" ] && [ -f .env ]; then
        v=$(grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tr -d '\r' | tail -1 \
            | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'"'"']//; s/["'"'"']$//' || true)
    fi
    echo "${v:-$2}"
}

# the log folder, as the app sees it (ON_LOG_DIR: relative to the project folder, or absolute, or ~/...)
log_dir=$(env_value ON_LOG_DIR logs)
case "$log_dir" in "~"*) log_dir="$HOME${log_dir#"~"}" ;; esac
case "$log_dir" in /*) ;; *) log_dir="$PWD/$log_dir" ;; esac
mkdir -p "$log_dir" 2>/dev/null || true

# log MESSAGE: to the screen and, with the time, to run.log (a log that cannot be written is no reason to stop)
log() {
    echo "$*"
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$log_dir/run.log" 2>/dev/null || true
}

log "run.sh start (pid $$, ON_NO_KILL=${ON_NO_KILL:-0}, user $(id -un), dir $PWD)"

# Port: real ON_PORT env var, else ON_PORT from .env, else 8090.
port=$(env_value ON_PORT 8090)
case "$port" in ''|*[!0-9]*) port=8090 ;; esac

if [ "${ON_NO_KILL:-0}" != "1" ]; then
    pids=$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "Port $port held by PID(s) $pids - killing"
        kill $pids 2>/dev/null || true
        sleep 0.5
    fi
    pkill -f "python.*backend\.main" 2>/dev/null || true
fi

if [ ! -x .venv/bin/python ]; then
    log "ERROR: .venv not found - run ./bin/setup_mac.sh first"
    exit 1
fi

if command -v caffeinate >/dev/null 2>&1; then
    log "starting the app: caffeinate -i .venv/bin/python -m backend.main (app log: $log_dir/app.log)"
    exec caffeinate -i .venv/bin/python -m backend.main
fi
log "starting the app: .venv/bin/python -m backend.main (app log: $log_dir/app.log)"
exec .venv/bin/python -m backend.main
