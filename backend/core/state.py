"""App-level shared state between HTTP request threads and the camera threads.
Per-camera state (settings, preview, frame ring) lives in backend/camera/."""

import threading

state = {
    "auto_enabled": False,   # off at start: turn it on from the live page once the belt is ready
    "manual_capture": False,
    "bg_capture_request": False,
    "detector": {"mode": "", "model": "", "classes": [], "targets": []},
    "auto_count": 0,
    "disk_stop": None,       # set when the automatic capture was switched off because the disk is full
}

state_lock = threading.Lock()
