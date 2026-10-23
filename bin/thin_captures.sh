#!/usr/bin/env bash
# Keep one picture per camera of every shoe pass of a day (the days saved with three cuts per camera), delete the other two.
#   ./bin/thin_captures.sh --date 2026-10-17 --dry-run     what would happen (nothing is changed)
#   ./bin/thin_captures.sh --date 2026-10-17               do it (asks first; --yes skips the question)
#   options: --mode best|center  --captures DIR  --no-zip-check  --backup-dir DIR
# Details: the top of backend/cli/thin_captures.py.
# Make the backup first: ./bin/capture_backup.sh --date 2026-10-17 --keep  (this script wants that zip).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Run ./bin/setup_mac.sh first" >&2; exit 1; }

log_dir="$(.venv/bin/python -c 'from backend.core import config as C; print(C.LOG_DIR)')"
mkdir -p "$log_dir"
.venv/bin/python -u -m backend.cli.thin_captures "$@" 2>&1 | tee -a "$log_dir/backup.log"
exit "${PIPESTATUS[0]}"
