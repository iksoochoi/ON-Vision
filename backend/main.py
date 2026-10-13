"""OAK auto-capture + labeling tool - entry point.  Run:  python -m backend.main   (or bin/run.sh)

    core     config (paths, ports, cameras, tuning, label categories), state, events, rotation
    detect   trigger (center-crossing logic), background subtraction, YOLO detections
    qr       QR decoding, the hub of recent reads, the live scanners
    storage  sample files + sidecar JSON, shoe ids, capture log, dataset export
    capture  sharpest-frame pick, capture sets + trigger session, disk guard
    camera   slots + registry, devices, controls, pipeline, one depthai worker thread per camera
    api      REST + MJPEG + SSE server, serves frontend/
"""

import threading
import time

from .api.server import start_server
from .core import config as C


def run(stop=None):
    """Start the HTTP server and one worker per camera, then block until Ctrl+C (or until `stop` is set)."""
    stop = stop or threading.Event()
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
            stop.set()
            for t in threads:
                t.join(timeout=10)
    finally:
        server.shutdown()
        print("Server stopped.")


def main():
    run()


if __name__ == "__main__":
    main()
