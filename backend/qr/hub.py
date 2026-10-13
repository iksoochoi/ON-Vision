"""Recent QR results for the UI (newest last) plus per-camera scan status."""

import threading
import time
from collections import Counter, deque
from datetime import datetime

from ..core import config as C
from ..core.events import broadcast


class QrHub:
    """Recent QR results for the UI (newest last) plus per-camera scan status."""

    def __init__(self, maxlen=200):
        self._lock = threading.Lock()
        self.history = deque(maxlen=maxlen)
        self._recent = {}        # (role, text) -> last time a live scan saw it
        self._reads = deque(maxlen=2000)   # (time, role, text) of EVERY live read, repeats included
        self.scanning = {}       # role -> {"epoch": last scan time, "found": bool}

    def add(self, role, text, source, sample_id=None, dedup_sec=5.0):
        """Record a result. Live scans of the same code on the same camera within dedup_sec only
        refresh the timestamp. Returns the new entry (or None when it was a repeat)."""
        now = time.time()
        with self._lock:
            if source == "live":
                self._reads.append((now, role, text))
                key = (role, text)
                last = self._recent.get(key)
                self._recent[key] = now
                if last is not None and now - last < dedup_sec:
                    return None
            entry = {"time": datetime.fromtimestamp(now).isoformat(timespec="seconds"), "epoch": now,
                     "role": role, "text": text, "source": source, "sample": sample_id}
            self.history.append(entry)
        broadcast({"key": "qr", "entry": entry})
        return entry

    def live_code(self, since, until):
        """The code the live scanners read most often between the epoch seconds `since` and `until` (the most
        recent one on a tie), as {"role", "text", "epoch"} (its latest read), or None."""
        with self._lock:
            hits = [r for r in self._reads if since <= r[0] <= until]
        if not hits:
            return None
        count, last = Counter(), {}
        for epoch, role, text in hits:
            count[text] += 1
            last[text] = (epoch, role)
        top = max(count, key=lambda text: (count[text], last[text][0]))
        return {"role": last[top][1], "text": top, "epoch": last[top][0]}

    def mark_scanned(self, role, found, size_px=...):
        """size_px: side of the code in the picture (read, or only located), None = no code placed,
        ... = not measured this time (the previous value stays)."""
        with self._lock:
            if size_px is ...:
                size_px = self.scanning.get(role, {}).get("size_px")
            self.scanning[role] = {"epoch": time.time(), "found": bool(found), "size_px": size_px}

    def snapshot(self, limit=50):
        with self._lock:
            items = list(self.history)[-limit:][::-1]  # newest first
            return {"latest": items[0] if items else None, "history": items,
                    "scanning": {r: dict(v) for r, v in self.scanning.items()},
                    "enabled": C.QR_ENABLED}


hub = QrHub()
