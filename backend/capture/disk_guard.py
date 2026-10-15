"""Stops the automatic capture before the saved pictures fill the disk.

ON_DISK_STOP_PERCENT (default 90): when the used part of the disk's total capacity reaches this value, the
automatic capture is switched off, and it cannot be switched on again until space has been freed.
The disk is the one that holds the captures folder (ON_CAPTURES_DIR). "Used" is everything that is not
free on it (the system, other files and the pictures alike), the way the disk itself reports it.

It is checked in two places: when the trigger is about to save a shoe pass (so the stop is immediate), and
by a monitor thread every few seconds (so it is noticed while nothing is passing, too). Manual captures
are not blocked. When the disk is below the limit again nothing resumes by itself: the person switches the
automatic capture on again."""

import shutil
import threading
import time
from datetime import datetime

from ..core import config as C
from ..core.events import broadcast
from ..core.state import state, state_lock

CACHE_SEC = 5.0          # one disk reading serves every question within this time
MONITOR_SEC = 15.0

_lock = threading.Lock()
_cache = {"t": -1e9, "status": None}


def _read():
    total, _used, free = shutil.disk_usage(C.CAPTURES_DIR)
    used = total - free
    pct = used / total * 100 if total else 0.0
    limit = C.DISK_STOP_PERCENT
    return {"total": total, "used": used, "free": free, "used_pct": round(pct, 1),
            "limit_pct": limit, "blocked": limit > 0 and pct >= limit}


def status(fresh=False):
    """{"total", "used", "free" (bytes), "used_pct", "limit_pct", "blocked"}. blocked = the limit is reached
    (always False when ON_DISK_STOP_PERCENT is 0). A reading that fails (the disk is gone for a moment)
    is not a reason to stop: the last good one is returned, or an unblocked unknown one."""
    with _lock:
        now = time.monotonic()
        if fresh or _cache["status"] is None or now - _cache["t"] >= CACHE_SEC:
            try:
                _cache["status"] = _read()
            except OSError as e:
                print(f"[disk] could not read the disk usage: {e}")
                if _cache["status"] is None:
                    _cache["status"] = {"total": 0, "used": 0, "free": 0, "used_pct": 0.0,
                                        "limit_pct": C.DISK_STOP_PERCENT, "blocked": False}
            _cache["t"] = now
        return dict(_cache["status"])


def blocked_message(s):
    """Why the automatic capture cannot be on, as text for the person (None when it can)."""
    if not s["blocked"]:
        return None
    return (f"The disk is {s['used_pct']}% full (limit {s['limit_pct']:g}%): auto capture cannot be turned on. "
            "Free up space first.")


def enforce(fresh=False):
    """Look at the disk; if the limit is reached and the automatic capture is on, switch it off (once, with a
    message and an event for the page). Returns the status."""
    s = status(fresh)
    switched = False
    with state_lock:
        if s["blocked"] and state["auto_enabled"]:
            state["auto_enabled"] = False
            state["disk_stop"] = {"time": datetime.now().isoformat(timespec="seconds"),
                                  "used_pct": s["used_pct"], "limit_pct": s["limit_pct"]}
            switched = True
        elif not s["blocked"] and state.get("disk_stop"):
            state["disk_stop"] = None   # space is free again; switching on again is up to the person
    if switched:
        print(f"[disk] {s['used_pct']}% of the disk is used (limit {s['limit_pct']:g}%): "
              "automatic capture stopped")
        broadcast({"key": "auto_enabled", "value": False})
        broadcast({"key": "disk"})
        try:   # the collection log and the alerts (backend/health)
            from ..health import collect
            collect.emit("DISK_STOP", "ERROR", f"the disk is {s['used_pct']}% full (limit {s['limit_pct']:g}%): "
                         "automatic capture was switched off and stays off until space is freed", key=("DISK_STOP", "-"))
        except Exception as e:
            print(f"[disk] could not report to the collection log: {e}")
    return s


def start_monitor(stop, interval=MONITOR_SEC):
    """Check the disk every `interval` seconds until `stop` is set (a daemon thread)."""
    def run():
        while not stop.wait(interval):
            try:
                enforce(fresh=True)
            except Exception as e:  # the monitor must not die on one bad reading
                print(f"[disk] check failed: {e}")
    thread = threading.Thread(target=run, name="disk-monitor", daemon=True)
    thread.start()
    return thread
