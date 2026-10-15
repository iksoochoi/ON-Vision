"""The log of the data collection: <ON_LOG_DIR>/collect.log, one line per problem or change of the collection.

Not the picture-by-picture record (that is capture.log) and not everything the app prints (app.log): only
what tells whether pictures are being collected, and why not when they are not.

    time level KIND camera=<role> message | key=value ...

Kinds: CAMERA_ONLINE / CAMERA_OFFLINE (every change, with the reason), CAMERA_STALLED (an ONLINE camera sends no
pictures any more) and CAMERA_DOWN (offline for a long time), CAMERA_RECOVERED, CAMERA_FLAPPING (offline again and
again), CAPTURE_PARTIAL / CAPTURE_FAILED / CAPTURE_DROPPED / CAPTURE_SKIPPED (a shoe pass that did not give every
picture), DISK_STOP, APP_START / APP_STOP, ABNORMAL_RESTART, APP_ERROR, STATUS (a line per camera set every
ON_HEALTH_STATUS_SEC seconds), ALERT_* (about the alerts themselves).

WARNING and ERROR lines are also handed to the alerts (alerts.py, a Google Chat webhook). Logging never raises: a full disk or a
missing folder is only printed."""

import json
import logging
import logging.handlers
import re
import threading
import time
from collections import deque
from datetime import datetime

from ..core import config as C

LEVELS = {"INFO": 0, "WARNING": 1, "ERROR": 2}
BACKUPS = 5
FLAP_COUNT, FLAP_WINDOW_SEC = 3, 600.0     # this many offline changes of one camera within this time = flapping

_lock = threading.Lock()
_logger = None
_offline_times = {}                          # role -> monotonic times of its recent offline changes


def _get():
    global _logger
    if _logger is None:
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            C.LOG_DIR / "collect.log", maxBytes=C.COLLECT_LOG_MAX_MB * 1024 * 1024, backupCount=BACKUPS,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("on_collect_log")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        _logger = logger
    return _logger


def _value(v):
    text = str(v)
    return json.dumps(text, ensure_ascii=False) if (" " in text or not text) else text


def format_line(when, level, kind, message, camera=None, fields=None):
    parts = [when.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], f"{level:<7}", kind]
    if camera:
        parts.append(f"camera={camera}")
    parts.append(message)
    if fields:
        parts.append("| " + " ".join(f"{k}={_value(v)}" for k, v in fields.items()))
    return " ".join(parts)


def emit(kind, level, message, camera=None, alert=None, key=None, recovers=None, snapshot=None, **fields):
    """Write one line to collect.log (and to the screen, so app.log has it in its timeline). alert: hand the
    event to the alerts: default yes for WARNING and ERROR. key: events with the same key are one problem
    for the cooldown (default: kind + camera). recovers: keys of problems this event ends (a recovery message is
    only sent for a problem that was sent). snapshot: text of the state at that moment, added to the message."""
    when = datetime.now()
    line = format_line(when, level, kind, message, camera, fields)
    try:
        with _lock:
            _get().info(line)
    except Exception as e:  # logging must never get in the way of the collection
        print(f"[collect log] could not write: {e}")
    print(f"[collect] {line[24:]}")
    if alert is None:
        alert = LEVELS.get(level, 0) >= LEVELS["WARNING"]
    if alert or recovers:
        try:
            from . import alerts
            alerts.notify({"time": when, "kind": kind, "level": level, "camera": camera, "message": message,
                           "fields": fields, "key": key or (kind, camera or "-"), "recovers": recovers or [],
                           "snapshot": snapshot, "alert": bool(alert)})
        except Exception as e:
            print(f"[collect] could not hand the event to the alerts: {e}")
    return line


# ---- the places that report into it ---------------------------------------------------------------

def camera_state(role, online, error=""):
    """A camera changed between online and offline (called by CameraSlot.set_online). Offline for a long time
    is reported by the monitor (CAMERA_DOWN); here: every change goes to the log, and a camera that goes offline
    again and again (FLAP_COUNT times within FLAP_WINDOW_SEC) is reported as flapping."""
    if online:
        emit("CAMERA_ONLINE", "INFO", "camera is online", camera=role)
        return
    restarting = str(error).startswith("restarting")   # a settings change on purpose
    emit("CAMERA_OFFLINE", "INFO" if restarting else "WARNING", f"camera went offline: {error or 'unknown'}",
         camera=role, alert=False)
    if restarting:
        return
    now = time.monotonic()
    times = _offline_times.setdefault(role, deque(maxlen=FLAP_COUNT))
    times.append(now)
    if len(times) == FLAP_COUNT and now - times[0] <= FLAP_WINDOW_SEC:
        emit("CAMERA_FLAPPING", "WARNING", f"camera went offline {FLAP_COUNT} times within {int(now - times[0])} s "
             f"(last reason: {error or 'unknown'})", camera=role, key=("CAMERA_FLAPPING", role))


def capture_problem(status, source, results, per_camera, burst=None):
    """A capture that did not give every picture (called by capture_log.job): status PARTIAL or FAILED.
    per_camera: ["left 0/3 [offline]", ...] as in capture.log."""
    missing = []
    for text in per_camera:   # "left 0/3 [offline]": the camera, the pictures it gave of the cuts it should have
        match = re.match(r"(\S+) (\d+)/(\d+)", text)
        if match and match.group(2) != match.group(3):
            missing.append(text)
    roles = sorted({m.split(" ", 1)[0] for m in missing})
    detail = "; ".join(m[:160] for m in missing)
    emit(f"CAPTURE_{status}", "ERROR" if status == "FAILED" else "WARNING",
         f"{source} capture {status.lower()}: {detail or 'see capture.log'}",
         key=(f"CAPTURE_{status}", ",".join(roles) or "-"), missing=",".join(roles) or "-",
         cuts=len(results), pass_id=burst or (results[0]["id"] if results else "-"))


def capture_lost(status, source, text):
    """A capture that never reached the saving: status DROPPED (the writer was backed up) or SKIPPED (no picture
    from the reference camera). text: what capture_log.event was given."""
    emit(f"CAPTURE_{status}", "WARNING", f"{source} capture {status.lower()}: {text}".strip(),
         key=(f"CAPTURE_{status}", "-"))


def install_exception_hooks():
    """Report a thread or the program dying from an unexpected exception (APP_ERROR). The normal traceback is
    still printed, so app.log has it as well."""
    import sys
    import threading as _threading

    previous_thread = _threading.excepthook
    previous_main = sys.excepthook

    def thread_hook(args):
        previous_thread(args)
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread else "?"
            emit("APP_ERROR", "ERROR", f"thread {name} died: {args.exc_type.__name__}: {args.exc_value}",
                 key=("APP_ERROR", name))

    def main_hook(exc_type, exc, tb):
        previous_main(exc_type, exc, tb)
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            emit("APP_ERROR", "ERROR", f"the program stopped on an exception: {exc_type.__name__}: {exc}",
                 key=("APP_ERROR", "main"))

    _threading.excepthook = thread_hook
    sys.excepthook = main_hook
