"""Live QR scanning: one thread per camera looks at its newest full-size frame."""

import time
import threading

from ..core import config as C
from ..core.rotation import view_gray
from .decode import decode_qr, qr_roles
from .hub import hub


LOCATE_EVERY = 4   # every n-th scan that finds nothing also measures a code it could only locate


class QrScanner(threading.Thread):
    """Live scanner of ONE camera: its newest full-size frame, C.QR_SCAN_HZ times per second (a frame that
    was already scanned is not scanned again). Every camera has its own thread: decoding releases the GIL.
    The scan works on the brightness image (no colour conversion) and, with ON_QR_ROI, only on that area."""

    def __init__(self, slot, stop):
        super().__init__(name=f"qr-scanner-{slot.role}", daemon=True)
        self.slot = slot
        self.stop_event = stop

    def run(self):
        interval = 1.0 / max(0.1, C.QR_SCAN_HZ)
        last_ts = None
        scans = 0
        while True:
            started = time.monotonic()
            item = self.slot.latest() if self.slot.online else None
            if item is not None and item[0] != last_ts:
                last_ts = item[0]
                scans += 1
                locate = scans % LOCATE_EVERY == 1   # placing an unreadable code costs ~30% more: not every scan
                try:
                    seen = [] if locate else None   # sizes of codes that could only be located, not read
                    found = decode_qr(view_gray(item[1]), roi=C.QR_ROI, roi_only=True, seen=seen)
                except Exception as e:  # a camera dropping out mid-scan must not kill the scanner
                    print(f"[qr] scan failed on {self.slot.role}: {e}")
                else:
                    if found:
                        size = found[0]["size_px"]
                    elif locate:
                        size = round(max(seen)) if seen else None
                    else:
                        size = ...   # not measured: the QR page keeps the last size
                    hub.mark_scanned(self.slot.role, bool(found), size)
                    for code in found:
                        xs = [p[0] for p in code.get("points") or []]
                        hub.add(self.slot.role, code["text"], "live", frame_ts=last_ts,
                                cx=sum(xs) / len(xs) if xs else None)
            # keep the rate: the time the scan took counts as part of the interval
            if self.stop_event.wait(max(0.0, interval - (time.monotonic() - started))):
                return


def make_scanners(registry, stop):
    """One QrScanner per camera that is searched for QR codes (see qr_roles)."""
    roles = registry.roles()
    unknown = [r for r in C.QR_CAMERAS if r not in roles]
    if unknown:
        print(f"[qr] ON_QR_CAMERAS: no camera named {', '.join(unknown)} (cameras: {', '.join(roles)})")
    chosen = qr_roles(roles)
    area = "x,y,w,h = " + ",".join(f"{v:g}" for v in C.QR_ROI) if C.QR_ROI else "the whole picture"
    print(f"[qr] scanning {', '.join(chosen)}: {C.QR_SCAN_HZ:g} scans/s each, search area {area}")
    return [QrScanner(registry.get(r), stop) for r in chosen]
