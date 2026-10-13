#!/usr/bin/env bash
# Run the app as a macOS LaunchAgent: starts at login, restarts after a crash or an unplugged camera,
# and opens the live screen in a browser at login (a second, small agent that runs `_open_ui` below).
#   ./bin/install_launchd.sh install     create + start both agents
#   ./bin/install_launchd.sh uninstall   stop + remove both
#   ./bin/install_launchd.sh restart     restart the app
#   ./bin/install_launchd.sh status | logs
#   ./bin/install_launchd.sh ui          open the screen now
# Whether the screen opens, and in which browser, is set in .env (ON_OPEN_BROWSER, ON_BROWSER_APP,
# ON_BROWSER_KIOSK): no reinstall needed after changing them.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="com.on.labeling"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UI_LABEL="$LABEL.ui"
UI_PLIST="$HOME/Library/LaunchAgents/$UI_LABEL.plist"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/labeler.log"
UI_LOG_FILE="$LOG_DIR/ui.log"
DOMAIN="gui/$(id -u)"

# write_plist KIND LABEL PLIST LOG_FILE: KIND "app" = bin/run.sh, kept alive; "ui" = `_open_ui`, once per login
write_plist() {
    local kind="$1"
    [ -x .venv/bin/python ] || { echo "Run ./bin/setup_mac.sh first" >&2; exit 1; }
    mkdir -p "$LOG_DIR" "$(dirname "$3")"
    ROOT="$ROOT" KIND="$kind" LABEL="$2" PLIST="$3" LOG_FILE="$4" .venv/bin/python - <<'EOF'
import os
import plistlib

root = os.environ["ROOT"]
log = os.environ["LOG_FILE"]
env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
plist = {
    "Label": os.environ["LABEL"],
    "WorkingDirectory": root,
    "RunAtLoad": True,
    "StandardOutPath": log,
    "StandardErrorPath": log,
    "EnvironmentVariables": env,
}
if os.environ["KIND"] == "app":
    plist["ProgramArguments"] = ["/bin/bash", os.path.join(root, "bin", "run.sh")]
    plist["KeepAlive"] = True
    plist["ThrottleInterval"] = 10
    env["ON_NO_KILL"] = "1"
else:
    # no KeepAlive: the screen is opened once per login, not again after every app restart
    plist["ProgramArguments"] = ["/bin/bash", os.path.join(root, "bin", "install_launchd.sh"), "_open_ui", "--if-enabled"]
    plist["LimitLoadToSessionType"] = "Aqua"   # needs the logged-in desktop to open a window
with open(os.environ["PLIST"], "wb") as f:
    plistlib.dump(plist, f)
EOF
}

# print_status LABEL: state / pid / runs / last exit code of one agent
print_status() {
    if launchctl print "$DOMAIN/$1" >/tmp/on_labeling_status.$$ 2>&1; then
        grep -E '^\s*(state|pid|runs|last exit code) =' /tmp/on_labeling_status.$$ | head -4 || true
    else
        echo "  Not installed (or not loaded)"
    fi
    rm -f /tmp/on_labeling_status.$$
}

# env_value NAME DEFAULT: real environment variable, else NAME from .env, else DEFAULT
env_value() {
    local v="${!1:-}"
    if [ -z "$v" ] && [ -f .env ]; then
        v=$(grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tr -d '\r' | tail -1 \
            | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'"'"']//; s/["'"'"']$//' || true)
    fi
    echo "${v:-$2}"
}

app_installed() {
    [ -d "/Applications/$1.app" ] || [ -d "$HOME/Applications/$1.app" ] || [ -d "/System/Applications/$1.app" ]
}

# open_ui [--if-enabled]: opens the live page in a browser once the app answers on its port
# (`ui` runs it by hand, the login agent runs it as `_open_ui --if-enabled`, which does nothing when ON_OPEN_BROWSER=0).
# Settings: real environment variables, else .env.
#   ON_PORT             port of the app (default 8090)
#   ON_OPEN_BROWSER     0 = the login agent does not open the screen
#   ON_BROWSER_APP      browser app name for `open -a` (empty = the default browser), e.g. "Google Chrome"
#   ON_BROWSER_KIOSK    1 = full-screen kiosk window without address bar (Chrome-based browsers only;
#                       it uses its own browser profile; quit it with Cmd+Q)
#   ON_BROWSER_WAIT_SEC how long to wait for the app (default 180)
#   OPEN_BIN            replaces `open` (for tests)
open_ui() {
    if [ "${1:-}" = "--if-enabled" ] && [ "$(env_value ON_OPEN_BROWSER 1)" = "0" ]; then
        echo "ON_OPEN_BROWSER=0: not opening the screen"
        exit 0
    fi

    local port wait_sec url waited open_bin app kiosk browser chromium profile
    port=$(env_value ON_PORT 8090)
    case "$port" in ''|*[!0-9]*) port=8090 ;; esac
    wait_sec=$(env_value ON_BROWSER_WAIT_SEC 180)
    case "$wait_sec" in ''|*[!0-9]*) wait_sec=180 ;; esac
    url="http://localhost:$port/"

    # the service may still be booting (the cameras take a while): wait until the page answers
    waited=0
    until curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$port/api/state?light=1" 2>/dev/null; do
        if [ "$waited" -ge "$wait_sec" ]; then
            echo "warning: nothing answered on port $port after ${wait_sec}s - opening the page anyway"
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    open_bin="${OPEN_BIN:-open}"
    app=$(env_value ON_BROWSER_APP "")
    kiosk=$(env_value ON_BROWSER_KIOSK 0)

    if [ -n "$app" ] && ! app_installed "$app"; then
        echo "warning: browser '$app' was not found - using the default"
        app=""
    fi

    if [ "$kiosk" = "1" ]; then
        browser="${app:-Google Chrome}"
        case "$browser" in *Chrome*|*Chromium*|*Edge*|*Brave*) chromium=1 ;; *) chromium=0 ;; esac
        if [ "$chromium" = "1" ] && app_installed "$browser"; then
            profile="$HOME/Library/Application Support/on_labeling_ui"   # own profile: the flags always apply
            echo "Opening $url in $browser (kiosk)"
            exec "$open_bin" -na "$browser" --args --kiosk --user-data-dir="$profile" --no-first-run \
                --no-default-browser-check --disable-session-crashed-bubble --disable-infobars "$url"
        fi
        echo "warning: kiosk mode needs Google Chrome (or another Chrome-based browser) - opening normally"
    fi

    if [ -n "$app" ]; then
        echo "Opening $url in $app"
        exec "$open_bin" -a "$app" "$url"
    fi
    echo "Opening $url in the default browser"
    exec "$open_bin" "$url"
}

case "${1:-}" in
    install)
        case "$ROOT" in
            "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
                echo "WARNING: macOS blocks background services from ~/Desktop, ~/Documents and ~/Downloads."
                echo "         The service will fail there with 'Operation not permitted' (exit code 126)."
                echo "         Move the project elsewhere first (for example ~/apps/on_data_collector)."
                ;;
        esac
        write_plist app "$LABEL" "$PLIST" "$LOG_FILE"
        write_plist ui "$UI_LABEL" "$UI_PLIST" "$UI_LOG_FILE"
        for l in "$LABEL" "$UI_LABEL"; do launchctl bootout "$DOMAIN/$l" 2>/dev/null || true; done
        launchctl bootstrap "$DOMAIN" "$PLIST"
        launchctl bootstrap "$DOMAIN" "$UI_PLIST"
        echo "Installed and started: $LABEL (app) and $UI_LABEL (opens the screen at login)"
        echo "Logs:   ./bin/install_launchd.sh logs      ($LOG_FILE, screen: $UI_LOG_FILE)"
        echo "Status: ./bin/install_launchd.sh status"
        ;;
    uninstall)
        for l in "$LABEL" "$UI_LABEL"; do launchctl bootout "$DOMAIN/$l" 2>/dev/null || true; done
        rm -f "$PLIST" "$UI_PLIST"
        echo "Removed: $LABEL, $UI_LABEL"
        ;;
    restart)
        launchctl kickstart -k "$DOMAIN/$LABEL"
        echo "Restarted: $LABEL"
        ;;
    status)
        echo "$LABEL (app):"
        print_status "$LABEL"
        echo "$UI_LABEL (screen at login):"
        print_status "$UI_LABEL"
        if tail -n 20 "$LOG_FILE" 2>/dev/null | grep -q "Operation not permitted"; then
            echo
            echo "The app cannot start: macOS blocks services from ~/Desktop, ~/Documents and ~/Downloads."
            echo "Move the project (for example to ~/apps/on_data_collector), run ./bin/setup_mac.sh there and"
            echo "./bin/install_launchd.sh install again."
        fi
        ;;
    logs)
        mkdir -p "$LOG_DIR"
        touch "$LOG_FILE"
        exec tail -n 100 -f "$LOG_FILE"
        ;;
    ui)
        open_ui
        ;;
    _open_ui)   # internal: what the login agent runs
        open_ui "${2:-}"
        ;;
    *)
        echo "usage: $0 install | uninstall | restart | status | logs | ui" >&2
        exit 2
        ;;
esac
