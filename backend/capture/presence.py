"""Is the shoe clearly in a camera's picture? (for the best frame of a camera, see picker.py)

Only the reference camera (top) has a shoe detector. For every other camera this module learns by itself what the
EMPTY belt looks like: while the reference camera has seen no shoe for IDLE_SEC, the brightness picture of every
camera (the small luma copy of the frame ring) is blended into a per-camera background, and only the pixels that
still look like the background are blended (a hand, a shoe, a shadow does not get in). The background follows slow
changes of the light and starts over when a lot differs for a while (the light, the camera or the zoom changed).

analyse() then compares a frame with that background and returns the biggest object: its box, how much of the
picture it covers, and whether it is cut off by the border (in the direction the belt moves in that view).
No camera and no depthai: it works on plain arrays, so it is tested without hardware."""

import threading
import time

import cv2
import numpy as np

IDLE_SEC = 4.0          # the belt counts as empty this long after the last shoe the reference camera saw
UPDATE_EVERY = 4        # of the frames of a camera one in this many is blended in
ALPHA = 0.05            # how fast the background follows the light
FG_DIFF = 28            # a pixel this much brighter / darker than the background belongs to an object
RESET_FRACTION = 0.35   # more than this part of the picture differs ...
RESET_AFTER = 12        # ... for this many updates in a row: start over
READY_UPDATES = 3       # the background is used after this many updates
MIN_OBJECT = 0.005      # an object smaller than this part of the picture is noise

# the direction in which the belt moves in each view: a shoe cut off by the border there is cut off for real. Front
# and back look along the belt: the shoe comes closer and fills the picture, that is no cut.
EDGE_AXES = {"top": "y", "left": "x", "right": "x", "front": "", "back": ""}
DEFAULT_AXES = "x"


class Presence:
    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._last_shoe = -1e9
        self._state = {}   # role -> {"key", "bg", "updates", "stale", "frames"}

    # ---- learning the empty belt ------------------------------------------------------------------------------

    def mark_shoe(self):
        """The reference camera sees a shoe now: the belt is not empty."""
        self._last_shoe = self._clock()

    def idle(self):
        return self._clock() - self._last_shoe >= IDLE_SEC

    def observe(self, role, gray, key=None):
        """Offer one frame (small luma copy) of a camera. key: anything that changes when the view changes (rotation,
        zoom): the background is dropped then."""
        if gray is None or not self.idle():
            return
        with self._lock:
            st = self._state.get(role)
            if st is None or st["key"] != key or st["bg"] is None or st["bg"].shape != gray.shape:
                st = self._state[role] = {"key": key, "bg": None, "updates": 0, "stale": 0, "frames": 0}
            st["frames"] += 1
            if st["bg"] is not None and st["frames"] % UPDATE_EVERY:
                return
            g = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
            if st["bg"] is None:
                st["bg"], st["updates"], st["stale"] = g, 1, 0
                return
            differs = np.abs(g - st["bg"]) > FG_DIFF
            if differs.mean() > RESET_FRACTION:
                st["stale"] += 1
                if st["stale"] >= RESET_AFTER:   # what we see now is the new normal
                    st["bg"], st["updates"], st["stale"] = g, 1, 0
                return
            st["stale"] = 0
            same = ~differs
            st["bg"][same] += ALPHA * (g[same] - st["bg"][same])
            st["updates"] += 1

    def ready(self, role):
        with self._lock:
            st = self._state.get(role)
            return bool(st and st["bg"] is not None and st["updates"] >= READY_UPDATES)

    # ---- looking at a frame ---------------------------------------------------------------------------------

    def analyse(self, role, gray, key=None):
        """The biggest object in this frame compared with the empty belt:
        {"found": bool, "box": (x0, y0, x1, y1) of the picture 0..1, "area": part of the picture the box covers}
        or None while the background is not known (yet) or does not belong to this view."""
        with self._lock:
            st = self._state.get(role)
            if (st is None or st["bg"] is None or st["updates"] < READY_UPDATES or st["key"] != key
                    or gray is None or st["bg"].shape != gray.shape):
                return None
            bg = st["bg"].copy()
        g = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
        mask = (np.abs(g - bg) > FG_DIFF).astype(np.uint8)
        h, w = mask.shape
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        k = max(3, int(w * 0.015) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        n, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            return {"found": False, "box": None, "area": 0.0}
        best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y, bw, bh, comp = (int(v) for v in stats[best])
        if comp < MIN_OBJECT * w * h:
            return {"found": False, "box": None, "area": 0.0}
        return {"found": True, "box": (x / w, y / h, (x + bw) / w, (y + bh) / h), "area": (bw * bh) / (w * h)}


def cut_off(box, crop, axes, margin):
    """Is `box` (a box of the whole view, 0..1) cut off by the border of the picture that is saved (the part `crop` of
    the view, None = all of it) in the directions `axes` ("x", "y", "xy" or "")?"""
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = crop or (0.0, 0.0, 1.0, 1.0)
    w, h = max(cx1 - cx0, 1e-6), max(cy1 - cy0, 1e-6)
    nx0, nx1, ny0, ny1 = (x0 - cx0) / w, (x1 - cx0) / w, (y0 - cy0) / h, (y1 - cy0) / h
    if "x" in axes and (nx0 <= margin or nx1 >= 1 - margin):
        return True
    if "y" in axes and (ny0 <= margin or ny1 >= 1 - margin):
        return True
    return False


def axes_of(role):
    return EDGE_AXES.get(role, DEFAULT_AXES)


presence = Presence()
