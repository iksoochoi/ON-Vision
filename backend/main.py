"""OAK auto-capture + labeling tool - entry point.  Run:  python -m backend.main   (or bin/run.sh)

    core     config (paths, ports, cameras, tuning, label categories), state, events, rotation
    detect   trigger (center-crossing logic), background subtraction, YOLO detections
    qr       QR decoding, the hub of recent reads, the live scanners
    storage  sample files + sidecar JSON, shoe ids, capture log, dataset export
    capture  sharpest-frame pick, capture sets + trigger session, disk guard
    camera   slots + registry, devices, controls, pipeline, one depthai worker thread per camera
    api      REST + MJPEG + SSE server, serves frontend/
    health   app.log (everything the app prints), collect.log (problems of the collection), the camera monitor,
             alerts to a Google Chat webhook
"""

import os
import signal
import threading
import time

from .api.server import start_server
from .core import config as C
from .health import applog, collect, monitor


def run(stop=None):
    """Start the HTTP server and one worker per camera, then block until Ctrl+C (or until `stop` is set)."""
    stop = stop or threading.Event()
    collect.install_exception_hooks()
    previous = applog.abnormal_exit()   # also marks this run as going on
    collect.emit("APP_START", "INFO", "app started", pid=os.getpid(), cameras=",".join(r for r, _ in C.CAMERAS))
    if previous:
        collect.emit("ABNORMAL_RESTART", "WARNING",
                     f"the previous run (pid {previous[0]}, started {previous[1]}) did not stop cleanly: "
                     "a crash, kill -9 or a power loss", key=("ABNORMAL_RESTART", "-"))
    try:   # a service stop or restart (SIGTERM) is a normal end, like Ctrl+C
        signal.signal(signal.SIGTERM, lambda *_: (print("SIGTERM - stopping"), stop.set()))
    except ValueError:   # not the main thread
        pass
    server = start_server()
    try:
        # imported late: the camera package needs the depthai package
        from .camera.registry import registry
        from .camera.worker import start_workers
        from .capture import disk_guard
        from .qr.scanner import make_scanners
        from .storage import capture_log

        threads = start_workers(stop)   # cameras that are missing keep retrying
        slots = registry.slots()
        capture_log.start(f"cameras={','.join(s.role for s in slots)} reference={registry.reference_role} "
                          f"auto_capture=off disk_limit={C.DISK_STOP_PERCENT:g}% qr_cameras={','.join(C.QR_CAMERAS) or 'all'}")
        disk_guard.start_monitor(stop)
        monitor.start(registry, stop)
        if C.QR_ENABLED:
            for scanner in make_scanners(registry, stop):
                scanner.start()
                threads.append(scanner)
        try:
            while not stop.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nCtrl+C - 종료 중...")
        finally:
            applog.mark_stopping()   # told to stop: not a crash, even if the slow shutdown below gets killed
            stop.set()
            for t in threads:
                t.join(timeout=10)
    finally:
        server.shutdown()
        collect.emit("APP_STOP", "INFO", "app stopped", pid=os.getpid())
        print("Server stopped.")


def main():
    applog.setup()   # first: everything printed from here on is also in logs/app.log
    run()


if __name__ == "__main__":
    main()
