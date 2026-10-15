"""The app log: everything the process writes to its screen also goes to <ON_LOG_DIR>/app.log, with the time
in front of every line. Works the same whether the app was started by bin/run.sh or by the launchd service.

It is done on the file descriptors (stdout and stderr are redirected into a pipe that a thread copies to the
original screen / launchd log and to the file), not on Python's print: so what native code writes directly
(the depthai library reports lost devices and USB errors that way) is in the log too, and so are tracebacks.

Also here: faulthandler writes the stack of a hard crash (a segfault in native code) to <ON_LOG_DIR>/fault.log,
and a marker file that tells at the next start whether the last run ended cleanly (see abnormal_exit)."""

import atexit
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime

from ..core import config as C

BACKUPS = 5
MARKER = ".app_running"        # in the log folder: exists while a run is going on
_END = "\x00app-log-flush\x00"  # written at exit: when the pump has seen it, everything before it is in the file
_state = {"installed": False, "flushed": threading.Event(), "fault_file": None}


def _pump(read_fd, screen_fd, logger):
    """Copy what arrives on the pipe to the original screen and, line by line with a time, to the log file."""
    pending = b""
    while True:
        try:
            chunk = os.read(read_fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        shown = chunk.replace((_END + "\n").encode(), b"")   # the flush marker is for the pump only
        try:
            if shown:
                os.write(screen_fd, shown)
        except OSError:   # the screen is gone (terminal closed): the file still gets everything
            pass
        pending += chunk
        *lines, pending = pending.split(b"\n")
        if len(pending) > 16384:   # a line without an end: do not grow forever
            lines.append(pending)
            pending = b""
        for raw in lines:
            text = raw.decode("utf-8", errors="replace").rstrip("\r")
            text = text.rsplit("\r", 1)[-1]   # a progress bar redraws one line with \r: keep the last drawing
            if _END in text:
                _state["flushed"].set()
                continue
            if text.strip():
                logger.info(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {text}")


def _finish():
    """At exit: let the pump write out what is still in the pipe."""
    if not _state["installed"]:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        _state["flushed"].clear()
        os.write(2, (_END + "\n").encode())
        _state["flushed"].wait(1.0)
    except (OSError, ValueError):
        pass


def setup():
    """Start writing app.log. Safe to call once at the start of the program; returns the log file path or None
    (ON_APP_LOG=0, or the log folder cannot be used: then the app simply runs without it)."""
    if _state["installed"] or not C.APP_LOG:
        return None
    try:
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            C.LOG_DIR / "app.log", maxBytes=C.APP_LOG_MAX_MB * 1024 * 1024, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("on_app_log")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        sys.stdout.flush()
        sys.stderr.flush()
        screen_fd = os.dup(1)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except OSError as e:
        print(f"[log] app.log could not be set up: {e}")
        return None
    threading.Thread(target=_pump, args=(read_fd, screen_fd, logger), name="app-log", daemon=True).start()
    _state["installed"] = True
    atexit.register(_finish)
    try:   # a segfault in native code leaves no Python traceback: this file gets its stack
        _state["fault_file"] = open(C.LOG_DIR / "fault.log", "a", encoding="utf-8")
        faulthandler.enable(file=_state["fault_file"], all_threads=True)
    except OSError:
        pass
    return C.LOG_DIR / "app.log"


# ---- did the last run end cleanly? ---------------------------------------------------------------------

def abnormal_exit():
    """Tell whether the previous run did not end cleanly (crash, kill -9, power loss): (pid, started) of it, or
    None. Then mark this run as going on. Call mark_stopping() when the app is told to stop."""
    path = C.LOG_DIR / MARKER
    previous = None
    try:
        if path.exists():
            parts = path.read_text(encoding="utf-8").split()
            previous = (parts[0] if parts else "?", parts[1] if len(parts) > 1 else "?")
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    except OSError:
        pass
    return previous


def mark_stopping():
    """The app was told to stop (Ctrl+C, a service stop, a restart): that is not a crash. Removed first, before
    the slow part of the shutdown, so that a service manager that runs out of patience does not look like one."""
    try:
        (C.LOG_DIR / MARKER).unlink()
    except OSError:
        pass
