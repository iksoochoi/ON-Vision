"""Background-subtraction shoe finder. No AI model: compares a frame with a reference image of
the empty belt and returns the bounding boxes of what is different.

Used twice:
  * live detector for the center trigger (ON_DETECTOR=background)
  * automatic bbox draft for saved samples (also in model mode)

The camera sees a fixed scene (opaque enclosure, constant lighting), so this is robust there.
Exposure drift between the reference and a frame is compensated with a global gain on L;
chroma (a/b) differences are insensitive to brightness and catch same-brightness objects."""

import threading
from datetime import datetime

import cv2
import numpy as np

from ..core import config as C
from ..core.rotation import rotate_image
from .trigger import Det


class BackgroundModel:
    def __init__(self, path=None):
        self.path = path or C.BACKGROUND_PATH
        self._lock = threading.Lock()
        self._lab = None  # float32 Lab at C.BG_SIZE
        self._load()

    # ---- reference image ----------------------------------------------------

    def _prep(self, img):
        small = cv2.resize(img, C.BG_SIZE, interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        return cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)

    def _load(self):
        try:
            data = np.fromfile(str(self.path), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except OSError:
            img = None
        if img is not None:
            self._lab = self._prep(img)

    @property
    def exists(self):
        return self._lab is not None

    @property
    def updated(self):
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            return None

    def set_from_frames(self, frames):
        """Average BGR frames into the reference image and persist it."""
        if not frames:
            return False
        mean = np.mean([cv2.resize(f, C.BG_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
                        for f in frames], axis=0).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", mean, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(buf.tobytes())
        with self._lock:
            self._lab = self._prep(mean)
        return True

    def rotate(self, degrees):
        """Turn the stored reference with its camera's view (clockwise), so a rotation change
        does not need a new background shot."""
        if degrees % 360 == 0 or not self.exists:
            return
        try:
            img = cv2.imdecode(np.fromfile(str(self.path), dtype=np.uint8), cv2.IMREAD_COLOR)
        except OSError:
            img = None
        if img is None:
            return
        img = rotate_image(img, degrees)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            self.path.write_bytes(buf.tobytes())
        with self._lock:
            self._lab = self._prep(img)

    def image_jpg(self):
        try:
            return self.path.read_bytes()
        except OSError:
            return None

    # ---- detection ----------------------------------------------------------

    def _foreground_mask(self, img):
        lab = self._prep(img)
        bg = self._lab
        lum, bg_lum = lab[..., 0], bg[..., 0]
        valid = bg_lum > 10
        gain = 1.0
        if valid.any():
            gain = float(np.clip(np.median(lum[valid] / bg_lum[valid]), 0.5, 2.0))
        d_lum = np.abs(lum - bg_lum * gain)
        d_chroma = np.maximum(np.abs(lab[..., 1] - bg[..., 1]), np.abs(lab[..., 2] - bg[..., 2]))
        diff = np.maximum(d_lum, 1.5 * d_chroma)
        mask = (diff > C.BG_THRESH).astype(np.uint8) * 255

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        k = max(3, int(C.BG_SIZE[0] * C.BG_CLOSE)) | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    def boxes(self, img):
        """Foreground objects as [(xmin, ymin, xmax, ymax, area_ratio)], normalized, largest first.
        Empty list when there is no reference image yet."""
        with self._lock:
            ready = self._lab is not None
        if not ready or img is None:
            return []
        mask = self._foreground_mask(img)
        w, h = C.BG_SIZE
        n, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        found = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            ratio = area / float(w * h)
            if ratio >= C.BG_MIN_AREA:
                found.append((float(x) / w, float(y) / h, float(x + bw) / w, float(y + bh) / h,
                              float(ratio)))
        found.sort(key=lambda b: b[4], reverse=True)
        return found

    def best_box(self, img):
        found = self.boxes(img)
        return found[0] if found else None


_models = {}
_models_lock = threading.Lock()


def background_path(role):
    """"top" keeps the original file name, so references taken by the single-camera version still work."""
    return C.BACKGROUND_PATH if role == "top" else C.CAPTURES_DIR / f".background_{role}.jpg"


def get_background(role=None):
    """The empty-belt reference of one camera (default: the current reference camera)."""
    role = role or C.TRIGGER_CAMERA
    with _models_lock:
        if role not in _models:
            _models[role] = BackgroundModel(background_path(role))
        return _models[role]


background = get_background("top")  # kept for single-camera code and tests


def bg_dets(img, role):
    """Foreground objects vs. the empty-belt reference of `role` (the reference camera's role for the
    trigger), as Det objects (all count as targets)."""
    background = get_background(role)
    return [Det(xmin=b[0], ymin=b[1], xmax=b[2], ymax=b[3], conf=1.0, label="shoe", target=True)
            for b in background.boxes(img)]
