"""Camera slot: one configured camera's settings, latest preview and ring of recent full-size frames.

No depthai import here (frames are duck-typed), so all of this runs and is tested without hardware.
Timestamps are seconds on the host-synchronized clock (ImgFrame.getTimestamp()), comparable
across devices."""

import threading
import time
from collections import deque

from ..core import config as C
from ..core.config import CAM_PARAMS
from ..core.events import broadcast
from ..core.zoom import crop_box

FPS_WINDOW_SEC = 3.0   # the fps readout averages over this long


def fmt_size(size):
    return "full" if size is None else f"{size[0]}x{size[1]}"


def parse_size(text):
    """'full' -> None (sensor size), '1280x960' -> (1280, 960)."""
    if text == "full":
        return None
    w, _, h = text.partition("x")
    return (int(w), int(h))


class CameraSlot:
    def __init__(self, role, device_id=None, is_trigger=False):
        self.role = role
        self.device_id = device_id       # configured id (None = any free OAK)
        self.actual_id = None            # id of the device that was actually opened
        self.is_trigger = is_trigger     # True for the reference camera
        self.lock = threading.Lock()
        self.params = {k: spec["default"] for k, spec in CAM_PARAMS.items()}
        self.online = False
        self.error = "not started"
        self.preview_jpg = None
        self.preview_seq = 0
        self.focus_request = None
        self.preview_size = C.PREVIEW_SIZE if is_trigger else C.SIDE_PREVIEW_SIZE
        self.capture_size = C.CAPTURE_SIZE   # None = sensor full size
        self.stream_on = True                # live preview on/off (capturing is not affected)
        self.rotation = 0                    # view rotation, clockwise degrees (backend/core/rotation.py)
        self.zoom = 1.0                      # zoom factor of the view (backend/core/zoom.py), 1 = whole view
        self.zoom_cx = 0.5                   # center of the zoomed part, in the rotated view (0..1)
        self.zoom_cy = 0.5
        self.usb_speed = None                # "SUPER" = USB3, "HIGH" = USB2 (known once the device is open)
        self._restart = False
        self._ring = deque()             # (timestamp_sec, frame object, small luma copy), oldest first
        self._seen = {"full": deque(), "preview": deque()}   # arrival times, for the fps readout
        self.last_full = None                # monotonic time of the last full-size picture (the monitor reads it)
        self.online_since = None             # monotonic time the camera last came online

    # ---- frames ---------------------------------------------------------------

    def push_frame(self, ts, frame, gray=None):
        """gray: small luma copy of the frame (backend/sharpness.py), None = not a pick candidate."""
        with self.lock:
            self._ring.append((ts, frame, gray))
            while self._ring and self._ring[0][0] < ts - C.RING_SEC:
                self._ring.popleft()

    def latest(self):
        with self.lock:
            return self._ring[-1][:2] if self._ring else None

    def nearest(self, ts):
        """(frame_ts, frame) closest in time to ts, or None when the ring is empty."""
        with self.lock:
            if not self._ring:
                return None
            return min(self._ring, key=lambda item: abs(item[0] - ts))[:2]

    def candidates(self, ts, window):
        """[(frame_ts, frame, gray)] taken within `window` seconds of ts, oldest first."""
        with self.lock:
            return [item for item in self._ring if abs(item[0] - ts) <= window]

    def neighbors(self, ts, direction, n):
        """Up to n [(frame_ts, frame, gray)] right before (direction < 0) or after (direction > 0) ts,
        nearest first."""
        with self.lock:
            if direction < 0:
                items = [item for item in reversed(self._ring) if item[0] < ts]
            else:
                items = [item for item in self._ring if item[0] > ts]
        return items[:n]

    def clear_frames(self):
        with self.lock:
            self._ring.clear()

    def count(self, kind):
        """Note that a frame of this stream ("full" / "preview") just arrived from the device."""
        now = time.monotonic()
        with self.lock:
            seen = self._seen[kind]
            seen.append(now)
            while seen and seen[0] < now - FPS_WINDOW_SEC:
                seen.popleft()
            if kind == "full":
                self.last_full = now

    def fps(self):
        """Frames per second received over the last few seconds, per stream."""
        now = time.monotonic()
        with self.lock:
            out = {}
            for kind, seen in self._seen.items():
                while seen and seen[0] < now - FPS_WINDOW_SEC:
                    seen.popleft()
                out[kind] = round(len(seen) / FPS_WINDOW_SEC, 1)
            return out

    def set_preview(self, jpg):
        with self.lock:
            self.preview_jpg = jpg
            self.preview_seq += 1

    def get_preview(self):
        with self.lock:
            return self.preview_jpg, self.preview_seq

    # ---- settings / status ------------------------------------------------------

    def snapshot_params(self):
        with self.lock:
            return dict(self.params)

    def set_param(self, name, value):
        with self.lock:
            self.params[name] = value

    def view_crop(self):
        """Normalized box of the rotated view that this camera shows (zoom), None = the whole view."""
        return crop_box(self.zoom, self.zoom_cx, self.zoom_cy)

    def take_focus_request(self):
        with self.lock:
            request, self.focus_request = self.focus_request, None
            return request

    def request_restart(self):
        """Settings that change the pipeline (resolution, reference role) need a rebuild."""
        with self.lock:
            self._restart = True

    def take_restart(self):
        with self.lock:
            restart, self._restart = self._restart, False
            return restart

    def set_online(self, online, error=""):
        """Update the connection status; broadcasts only when it changed."""
        with self.lock:
            changed = (self.online, self.error) != (online, error)
            if online and not self.online:
                self.online_since = time.monotonic()
            self.online, self.error = online, error
            if not online:
                self._ring.clear()
        if changed:
            broadcast({"key": "camera_status", "role": self.role, "online": online, "error": error})
            try:   # the collection log notes every change (backend/health/collect.py)
                from ..health import collect
                collect.camera_state(self.role, online, error)
            except Exception as e:   # reporting must never break the camera
                print(f"[{self.role}] could not log the status change: {e}")

    def describe(self):
        fps = self.fps()
        with self.lock:
            return {
                "role": self.role, "is_trigger": self.is_trigger, "online": self.online,
                "error": self.error, "device_id": self.actual_id or self.device_id,
                "params": dict(self.params),
                "preview": fmt_size(self.preview_size), "capture": fmt_size(self.capture_size),
                "stream_on": self.stream_on, "rotation": self.rotation,
                "zoom": {"factor": self.zoom, "x": self.zoom_cx, "y": self.zoom_cy},
                "restart_pending": self._restart,
                "usb": self.usb_speed, "fps": fps if self.online else {"full": 0.0, "preview": 0.0},
            }
