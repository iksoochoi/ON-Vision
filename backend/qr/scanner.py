"""Live QR scanning: one thread per camera looks at its newest full-size frame."""

import time
import threading

from ..core import config as C
from .decode import decode_qr, qr_roles
from .hub import hub


class QrScanner(threading.Thread):
    """Live scanner of ONE camera: its newest full-size frame, C.QR_SCAN_HZ times per second (a frame that
    was already scanned is not scanned again). Every camera has its own thread: decoding releases the GIL."""

    def __init__(self, slot, stop):
        super().__init__(name=f"qr-scanner-{slot.role}", daemon=True)
        self.slot = slot
        self.stop_event = stop

    def run(self):
        interval = 1.0 / max(0.1, C.QR_SCAN_HZ)
        last_ts = None
        while True:
            started = time.monotonic()
            item = self.slot.latest() if self.slot.online else None
            if item is not None and item[0] != last_ts:
                last_ts = item[0]
                try:
                    found = decode_qr(item[1].getCvFrame())
                except Exception as e:  # a camera dropping out mid-scan must not kill the scanner
                    print(f"[qr] scan failed on {self.slot.role}: {e}")
                else:
                    hub.mark_scanned(self.slot.role, bool(found))
                    for code in found:
                        hub.add(self.slot.role, code["text"], "live")
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
    print(f"[qr] scanning {', '.join(chosen)}: {C.QR_SCAN_HZ:g} scans/s each")
    return [QrScanner(registry.get(r), stop) for r in chosen]
