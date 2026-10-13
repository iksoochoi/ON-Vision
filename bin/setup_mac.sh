#!/usr/bin/env bash
# One-time setup on macOS: creates .venv and installs the dependencies.
#   ./bin/setup_mac.sh            installs requirements.txt (exact versions verified on the dev machine)
# (if the file is not executable yet: bash bin/setup_mac.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(uname -s)" = "Darwin" ] || echo "note: written for macOS, running on $(uname -s)"

# depthai wheels cover Python 3.9-3.14; prefer the version the project was developed with (3.12).
pick_python() {
    local c
    for c in python3.12 python3.13 python3.11 python3.10 python3.9 python3; do
        if command -v "$c" >/dev/null 2>&1 \
            && "$c" -c 'import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 14) else 1)'; then
            echo "$c"
            return 0
        fi
    done
    return 1
}
PY=$(pick_python) || { echo "Python 3.9-3.14 not found. Install one with: brew install python@3.12"; exit 1; }
echo "Using $($PY --version) ($(command -v "$PY"))"

if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
    echo ".venv here was created on another OS (Windows?) and cannot be used."
    echo "Remove it and run again:  rm -rf .venv && ./bin/setup_mac.sh"
    exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python - <<'EOF'
import cv2, numpy, depthai as dai
print(f"depthai {dai.__version__} | opencv {cv2.__version__} | numpy {numpy.__version__}")
try:
    devices = dai.Device.getAllAvailableDevices()
    print(f"OAK devices found: {len(devices)}")
    for d in devices:
        print("  -", d.getDeviceId(), d.name)
except Exception as e:  # not fatal: the camera may simply not be plugged in yet
    print("Could not list OAK devices:", e)
EOF

chmod +x bin/*.sh
[ -f .env ] || echo "warning: .env not found - built-in defaults will be used"
echo
echo "Done. Start with:  ./bin/run.sh     (auto-start at login: ./bin/install_launchd.sh install)"
