#!/usr/bin/env bash
# Run the app as a macOS LaunchAgent: starts at login, restarts after a crash or an unplugged camera,
# and opens the live screen in a browser at login (a second, small agent that runs `_open_ui` below).
#   ./bin/install_launchd.sh install     create + start both agents
#   ./bin/install_launchd.sh uninstall   stop + remove both
#   ./bin/install_launchd.sh restart     restart the app
#   ./bin/install_launchd.sh status
#   ./bin/install_launchd.sh logs [app|collect|capture|run|service]   follow a log (default: app)
#   ./bin/install_launchd.sh ui          open the screen now
#   ./bin/install_launchd.sh stop | start         stop / start the app by hand (the screen agent is not touched)
#   ./bin/install_launchd.sh predict-install      also run the defect detection worker (backend/predict, docs/predict.md)
#   ./bin/install_launchd.sh predict-uninstall    remove the worker (restart: predict-restart)
#   ./bin/install_launchd.sh quiet-install        stop the app every night and start it again in the morning
#   ./bin/install_launchd.sh quiet-uninstall      back to running all day
# The quiet hours (nothing is shot at night, so the load is saved) are ON_QUIET_START .. ON_QUIET_END in .env,
# default 23:00 .. 06:00. The app is stopped cleanly (it is not a crash) and started again at the end; after
# changing the times run quiet-install again. Details: docs/operations.md.
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
QUIET_LABEL="$LABEL.quiet"
QUIET_PLIST="$HOME/Library/LaunchAgents/$QUIET_LABEL.plist"
QUIET_LOG_FILE="$LOG_DIR/quiet.log"
PREDICT_LABEL="$LABEL.predict"
PREDICT_PLIST="$HOME/Library/LaunchAgents/$PREDICT_LABEL.plist"
PREDICT_LOG_FILE="$LOG_DIR/predict.log"
LAUNCHCTL="${LAUNCHCTL_BIN:-launchctl}"   # replaced in tests

# write_plist KIND LABEL PLIST LOG_FILE: KIND "app" = bin/run.sh, kept alive; "ui" = `_open_ui`, once per login;
# "quiet" = `_quiet` at the two times of QUIET_TIMES ("23:00,06:00"); "predict" = the defect detection worker
# (its own process at a low priority, kept alive: the camera app never waits for it)
write_plist() {
    local kind="$1"
    [ -x .venv/bin/python ] || { echo "Run ./bin/setup_mac.sh first" >&2; exit 1; }
    mkdir -p "$LOG_DIR" "$(dirname "$3")"
    ROOT="$ROOT" KIND="$kind" LABEL="$2" PLIST="$3" LOG_FILE="$4" QUIET_TIMES="${QUIET_TIMES:-}" .venv/bin/python - <<'EOF'
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
elif os.environ["KIND"] == "predict":
    plist["ProgramArguments"] = [os.path.join(root, ".venv", "bin", "python"), "-u", "-m", "backend.predict.worker"]
    plist["KeepAlive"] = True
    plist["ThrottleInterval"] = 30
    plist["ProcessType"] = "Background"
    plist["Nice"] = 10
    plist["LowPriorityIO"] = True
    env["PYTHONUNBUFFERED"] = "1"
elif os.environ["KIND"] == "quiet":
    # at the start and at the end of the quiet hours (and at login): stop or start the app as the time says
    plist["ProgramArguments"] = ["/bin/bash", os.path.join(root, "bin", "install_launchd.sh"), "_quiet"]
    plist["StartCalendarInterval"] = [
        {"Hour": int(t.split(":")[0]), "Minute": int(t.split(":")[1])} for t in os.environ["QUIET_TIMES"].split(",")]
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
    if "$LAUNCHCTL" print "$DOMAIN/$1" >/tmp/on_labeling_status.$$ 2>&1; then
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

# app_log_dir: the folder of the app's logs (ON_LOG_DIR, default logs in the project folder)
app_log_dir() {
    local d
    d=$(env_value ON_LOG_DIR logs)
    case "$d" in "~"*) d="$HOME${d#"~"}" ;; esac
    case "$d" in /*) echo "$d" ;; *) echo "$ROOT/$d" ;; esac
}

# ---- quiet hours -----------------------------------------------------------------------------------------------

# valid_time TEXT DEFAULT: TEXT if it is HH:MM, else DEFAULT
valid_time() {
    case "$1" in
        [0-1][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) echo "$1" ;;
        *) echo "$2" ;;
    esac
}

quiet_start() { valid_time "$(env_value ON_QUIET_START 23:00)" 23:00; }
quiet_end()   { valid_time "$(env_value ON_QUIET_END 06:00)" 06:00; }

to_minutes() { local h="${1%%:*}" m="${1##*:}"; echo $((10#$h * 60 + 10#$m)); }

# in_quiet_window HH:MM: is that time inside [start, end)? The window may cross midnight (23:00 .. 06:00).
in_quiet_window() {
    local s e n
    s=$(to_minutes "$(quiet_start)"); e=$(to_minutes "$(quiet_end)"); n=$(to_minutes "$1")
    if [ "$s" -le "$e" ]; then
        [ "$n" -ge "$s" ] && [ "$n" -lt "$e" ]
    else
        [ "$n" -ge "$s" ] || [ "$n" -lt "$e" ]
    fi
}

app_loaded() { "$LAUNCHCTL" print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

# quiet_apply: what the quiet agent does at the start / end of the quiet hours and at login: stop the app inside the
# window, start it outside (QUIET_NOW=HH:MM replaces the clock, for tests)
quiet_apply() {
    local now stamp
    now="${QUIET_NOW:-$(date +%H:%M)}"
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    if in_quiet_window "$now"; then
        if app_loaded; then
            echo "$stamp quiet hours ($(quiet_start)-$(quiet_end)): stopping the app"
            "$LAUNCHCTL" bootout "$DOMAIN/$LABEL" || echo "$stamp could not stop the app"
        else
            echo "$stamp quiet hours ($(quiet_start)-$(quiet_end)): the app is stopped already"
        fi
    elif app_loaded; then
        echo "$stamp the app is running"
    elif [ -f "$PLIST" ]; then
        echo "$stamp quiet hours are over: starting the app"
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST" || echo "$stamp could not start the app"
    else
        echo "$stamp the app is not installed (./bin/install_launchd.sh install)"
    fi
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
        for l in "$LABEL" "$UI_LABEL"; do "$LAUNCHCTL" bootout "$DOMAIN/$l" 2>/dev/null || true; done
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST"
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$UI_PLIST"
        echo "Installed and started: $LABEL (app) and $UI_LABEL (opens the screen at login)"
        echo "Logs:   ./bin/install_launchd.sh logs [app|collect|capture|run|service]   (files in $(app_log_dir))"
        echo "        app = everything the app prints, collect = problems of the data collection, capture = every capture,"
        echo "        run = the start script, service = the raw output of the service ($LOG_FILE; screen: $UI_LOG_FILE)"
        echo "Status: ./bin/install_launchd.sh status"
        echo "Nobody works at night? ./bin/install_launchd.sh quiet-install stops the app $(quiet_start)-$(quiet_end) (saves the load)"
        ;;
    uninstall)
        for l in "$LABEL" "$UI_LABEL" "$QUIET_LABEL" "$PREDICT_LABEL"; do "$LAUNCHCTL" bootout "$DOMAIN/$l" 2>/dev/null || true; done
        rm -f "$PLIST" "$UI_PLIST" "$QUIET_PLIST" "$PREDICT_PLIST"
        echo "Removed: $LABEL, $UI_LABEL, $QUIET_LABEL, $PREDICT_LABEL"
        ;;
    restart)
        if ! app_loaded; then
            echo "The app is not running (stopped by hand or for the quiet hours): ./bin/install_launchd.sh start" >&2
            exit 1
        fi
        "$LAUNCHCTL" kickstart -k "$DOMAIN/$LABEL"
        echo "Restarted: $LABEL"
        ;;
    stop)
        "$LAUNCHCTL" bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "Stopped: $LABEL" || echo "The app was not running"
        [ -f "$QUIET_PLIST" ] && echo "The quiet-hours agent starts it again at $(quiet_end) (./bin/install_launchd.sh quiet-uninstall to stop that)"
        true
        ;;
    start)
        if app_loaded; then
            echo "The app is running already"
        elif [ -f "$PLIST" ]; then
            "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST"
            echo "Started: $LABEL"
        else
            echo "Not installed: ./bin/install_launchd.sh install" >&2
            exit 1
        fi
        ;;
    predict-install)
        if ! .venv/bin/python -c "import ultralytics" 2>/dev/null; then
            echo "The defect detection packages are not installed: .venv/bin/python -m pip install -r backend/predict/requirements.txt" >&2
            exit 1
        fi
        model=$(env_value ON_PREDICT_MODEL backend/models/defect_detection/best.pt)
        case "$model" in "~"*) model="$HOME${model#"~"}" ;; esac
        case "$model" in /*) ;; *) model="$ROOT/$model" ;; esac
        [ -f "$model" ] || { echo "Model file not found: $model (ON_PREDICT_MODEL)" >&2; exit 1; }
        write_plist predict "$PREDICT_LABEL" "$PREDICT_PLIST" "$PREDICT_LOG_FILE"
        "$LAUNCHCTL" bootout "$DOMAIN/$PREDICT_LABEL" 2>/dev/null || true
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$PREDICT_PLIST"
        echo "Installed and started: $PREDICT_LABEL (defect detection worker, low priority, separate from the app)"
        echo "Log:  $PREDICT_LOG_FILE   (./bin/install_launchd.sh logs predict)   Results: the Predict page of the screen"
        echo "It keeps running at night (idle, it costs nothing). Remove it: ./bin/install_launchd.sh predict-uninstall"
        ;;
    predict-uninstall)
        "$LAUNCHCTL" bootout "$DOMAIN/$PREDICT_LABEL" 2>/dev/null || true
        rm -f "$PREDICT_PLIST"
        echo "Removed: $PREDICT_LABEL (the results already made stay in captures/.predict/)"
        ;;
    predict-restart)
        if ! "$LAUNCHCTL" print "$DOMAIN/$PREDICT_LABEL" >/dev/null 2>&1; then
            echo "The worker is not running: ./bin/install_launchd.sh predict-install" >&2
            exit 1
        fi
        "$LAUNCHCTL" kickstart -k "$DOMAIN/$PREDICT_LABEL"
        echo "Restarted: $PREDICT_LABEL"
        ;;
    quiet-install)
        mkdir -p "$LOG_DIR"
        QUIET_TIMES="$(quiet_start),$(quiet_end)"
        export QUIET_TIMES
        write_plist quiet "$QUIET_LABEL" "$QUIET_PLIST" "$QUIET_LOG_FILE"
        "$LAUNCHCTL" bootout "$DOMAIN/$QUIET_LABEL" 2>/dev/null || true
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$QUIET_PLIST"
        echo "Installed: $QUIET_LABEL - the app is stopped from $(quiet_start) to $(quiet_end) and started again after that"
        echo "Now: $(if in_quiet_window "${QUIET_NOW:-$(date +%H:%M)}"; then echo "inside the quiet hours: the app is being stopped"; else echo "outside the quiet hours: the app keeps running"; fi)"
        echo "Log:  $QUIET_LOG_FILE   Back to running all day: ./bin/install_launchd.sh quiet-uninstall"
        echo "A sleeping Mac runs the 06:00 job only when it wakes: see docs/operations.md (wake schedule)."
        ;;
    quiet-uninstall)
        "$LAUNCHCTL" bootout "$DOMAIN/$QUIET_LABEL" 2>/dev/null || true
        rm -f "$QUIET_PLIST"
        echo "Removed: $QUIET_LABEL"
        if ! app_loaded && [ -f "$PLIST" ]; then   # do not leave the app stopped
            "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST" && echo "Started the app again: $LABEL"
        fi
        ;;
    _quiet)   # internal: what the quiet agent runs
        quiet_apply
        ;;
    status)
        echo "$LABEL (app):"
        print_status "$LABEL"
        echo "$UI_LABEL (screen at login):"
        print_status "$UI_LABEL"
        if [ -f "$PREDICT_PLIST" ]; then
            echo "$PREDICT_LABEL (defect detection worker):"
            print_status "$PREDICT_LABEL"
        else
            echo "Defect detection worker: not installed (./bin/install_launchd.sh predict-install)"
        fi
        if [ -f "$QUIET_PLIST" ]; then
            echo "$QUIET_LABEL (quiet hours $(quiet_start)-$(quiet_end)): now $(if in_quiet_window "${QUIET_NOW:-$(date +%H:%M)}"; then echo inside, the app should be stopped; else echo outside; fi)"
            print_status "$QUIET_LABEL"
        else
            echo "Quiet hours: not installed (./bin/install_launchd.sh quiet-install)"
        fi
        if tail -n 20 "$LOG_FILE" 2>/dev/null | grep -q "Operation not permitted"; then
            echo
            echo "The app cannot start: macOS blocks services from ~/Desktop, ~/Documents and ~/Downloads."
            echo "Move the project (for example to ~/apps/on_data_collector), run ./bin/setup_mac.sh there and"
            echo "./bin/install_launchd.sh install again."
        fi
        ;;
    logs)
        mkdir -p "$LOG_DIR"
        dir=$(app_log_dir)
        mkdir -p "$dir"
        case "${2:-app}" in
            app)     f="$dir/app.log"; [ -f "$f" ] || f="$LOG_FILE" ;;   # before the first start with the new logs
            collect) f="$dir/collect.log" ;;
            capture) f="$dir/capture.log" ;;
            run)     f="$dir/run.log" ;;
            service) f="$LOG_FILE" ;;
            quiet)   f="$QUIET_LOG_FILE" ;;
            predict) f="$PREDICT_LOG_FILE" ;;
            *) echo "usage: $0 logs [app|collect|capture|run|service|quiet|predict]" >&2; exit 2 ;;
        esac
        touch "$f"
        exec tail -n 100 -f "$f"
        ;;
    ui)
        open_ui
        ;;
    _open_ui)   # internal: what the login agent runs
        open_ui "${2:-}"
        ;;
    *)
        echo "usage: $0 install | uninstall | restart | stop | start | status | logs [app|collect|capture|run|service|quiet|predict] | ui | predict-install | predict-uninstall | predict-restart | quiet-install | quiet-uninstall" >&2
        exit 2
        ;;
esac
