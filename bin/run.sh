#!/usr/bin/env bash
# macOS/Linux launcher: frees the UI port, keeps the Mac awake (caffeinate) and starts the app.
# ON_NO_KILL=1 skips the cleanup of an older instance (launchd sets it).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1

# Port: real ON_PORT env var, else ON_PORT from .env, else 8090.
port="${ON_PORT:-}"
if [ -z "$port" ] && [ -f .env ]; then
    port=$(grep -E '^[[:space:]]*ON_PORT[[:space:]]*=' .env | tr -d '\r' | tail -1 \
        | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'"'"']//; s/["'"'"']$//' || true)
fi
case "$port" in ''|*[!0-9]*) port=8090 ;; esac

if [ "${ON_NO_KILL:-0}" != "1" ]; then
    pids=$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "Port $port held by PID(s) $pids - killing"
        kill $pids 2>/dev/null || true
        sleep 0.5
    fi
    pkill -f "python.*backend\.main" 2>/dev/null || true
fi

if [ ! -x .venv/bin/python ]; then
    echo ".venv not found - run ./bin/setup_mac.sh first" >&2
    exit 1
fi

if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i .venv/bin/python -m backend.main
fi
exec .venv/bin/python -m backend.main
