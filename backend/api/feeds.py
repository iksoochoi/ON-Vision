"""What the server pushes to the pages over SSE instead of the pages asking again and again.

One thread, and it works only while a page listens (events.has_subscribers):
  state       every STATE_PUSH_SEC, the Live page: cameras (fps, online, USB), auto capture, disk, counts
  live_data   a moment after a capture is saved / changed / deleted, the Live page: the newest sets and the counts
  qr_scan     every QR_SCAN_PUSH_SEC, the QR page: what the live scan of each camera sees
  qr_stats    a moment after the sets changed (at most every STATS_MIN_SEC), the QR page: the daily statistics
  predict_changed  the Predict page: a defect detection result arrived or the worker's state changed (the page asks again)
Nothing here reads the disk, except the folder of the defect detection results (every PREDICT_CHECK_SEC, only while a
Predict page listens): the sets come from the index in memory."""

import threading
import time

from ..core import events
from ..core.config import UI
from ..qr.hub import hub as qr_hub
from ..storage import predictions
from ..storage.store import store
from .state_payload import build_changing_state

STATE_PUSH_SEC = 2.5
QR_SCAN_PUSH_SEC = 2.0
DATA_DEBOUNCE_SEC = 0.3
STATS_MIN_SEC = 2.0
TICK_SEC = 0.25
PREDICT_CHECK_SEC = 2.0
RECENT_MIN = 6


class Feeds(threading.Thread):
    def __init__(self, stop=None, clock=time.monotonic):
        super().__init__(name="feeds", daemon=True)
        self.stop_event = stop or threading.Event()
        self.clock = clock
        self._dirty_since = None     # when the sets last changed and the pages have not been told yet
        self._stats_due = False      # the QR page still has to be sent new statistics
        self._next = {"state": 0.0, "scan": 0.0, "stats": 0.0, "predict": 0.0}
        events.add_listener(self._on_event)

    def _on_event(self, payload):
        if isinstance(payload, dict) and payload.get("key") == "samples_changed":
            if self._dirty_since is None:
                self._dirty_since = self.clock()
            self._stats_due = True

    def live_data(self):
        """The newest sets and the counts: what the Live page draws from a capture."""
        return {"key": "live_data", "recent": store.list("all", max(UI["recent_count"], RECENT_MIN)),
                "counts": store.counts()}

    def tick(self):
        """One look at what is due. Returns the keys that were pushed (for tests)."""
        now = self.clock()
        sent = []
        if now >= self._next["state"] and events.has_subscribers("live"):
            self._next["state"] = now + STATE_PUSH_SEC
            events.broadcast({"key": "state", "state": build_changing_state()})
            sent.append("state")
        if self._dirty_since is not None and now - self._dirty_since >= DATA_DEBOUNCE_SEC:
            self._dirty_since = None
            if events.has_subscribers("live"):
                events.broadcast(self.live_data())
                sent.append("live_data")
        if self._stats_due and now >= self._next["stats"]:
            if events.has_subscribers("qrpage"):
                self._next["stats"] = now + STATS_MIN_SEC
                events.broadcast({"key": "qr_stats", "stats": store.daily_qr()})
                sent.append("qr_stats")
            self._stats_due = False
        if now >= self._next["scan"] and events.has_subscribers("qrpage"):
            self._next["scan"] = now + QR_SCAN_PUSH_SEC
            snap = qr_hub.snapshot(0)
            events.broadcast({"key": "qr_scan", "scanning": snap["scanning"], "enabled": snap["enabled"],
                              "now": time.time()})
            sent.append("qr_scan")
        if now >= self._next["predict"] and events.has_subscribers("predict"):
            self._next["predict"] = now + PREDICT_CHECK_SEC
            if predictions.index.refresh(force=True):
                events.broadcast({"key": "predict_changed"})
                sent.append("predict_changed")
        return sent

    def run(self):
        while not self.stop_event.wait(TICK_SEC):
            try:
                self.tick()
            except Exception as e:  # a feed that fails must not take the others down
                print(f"[feeds] {type(e).__name__}: {e}")


def start(stop=None):
    feeds = Feeds(stop)
    feeds.start()
    return feeds
