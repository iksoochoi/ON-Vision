#!/usr/bin/env bash
# Back up one day of the captures folder: statistics (captures/readme.md), capture_<date>.zip, then the backed-up
# files are deleted from captures/. The real work starts at 23:59 of the day (the script waits until then).
#   ./bin/capture_backup.sh                     today's sets, started at 23:59 (leave it running; Ctrl+C cancels)
#   ./bin/capture_backup.sh --now               today's sets, started now
#   ./bin/capture_backup.sh --date 2026-10-15   that day's sets (a day that is over starts at once)
#   ./bin/capture_backup.sh --all               every day that has sets (up to today), one zip per day, oldest first
#   ./bin/capture_backup.sh --dry-run           only say what would be done, change nothing
#   ./bin/capture_backup.sh --delete --date 2026-10-15   NO backup: only delete that day's data (asks first, --yes skips it)
#   ./bin/capture_backup.sh --delete --all --now         NO backup: delete everything up to today (asks first)
#   options: --at HH:MM (start time, default 23:59)  --out DIR (zip folder, default ON_BACKUP_DIR = backups/)
#            --keep (make the zip but keep the files)
# Details, the readme and what is deleted: docs/capture-backup.md. The output is also appended to logs/backup.log.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Run ./bin/setup_mac.sh first" >&2; exit 1; }

log_dir="$(.venv/bin/python -c 'from backend.core import config as C; print(C.LOG_DIR)')"
mkdir -p "$log_dir"

cmd=(.venv/bin/python -u -m backend.cli.capture_backup "$@")
# the Mac must not fall asleep while it waits for 23:59 (the display may)
if command -v caffeinate >/dev/null 2>&1; then
    cmd=(caffeinate -i "${cmd[@]}")
fi
"${cmd[@]}" 2>&1 | tee -a "$log_dir/backup.log"
exit "${PIPESTATUS[0]}"
