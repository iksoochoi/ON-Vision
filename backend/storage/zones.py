"""The shoe zone of every camera, learned from the saved pictures (no empty-belt picture, no drawing).

For one camera the saved pictures of the latest sets are laid over each other. The camera does not move and the scene
behind the shoe (the frame, the window, the belt) does not change from one shoe to the next, so a pixel that is often very
different from the median over all the pictures is a pixel the shoe passes over; the rest is background. That map
(how often a pixel differs strongly from the median) is cut at ON_PREDICT_ZONE_THR, cleaned (closing, opening, small bits
dropped) and its holes are filled (the inside of the shoe is the median itself, so it shows no difference): the zone is
the union of the places the shoe was, as a mask of ZONE_W x ZONE_H.

The Predict page uses it for a detection whose picture has no shoe region of its own (every camera but the reference
one): a box that is less than ON_PREDICT_ON_SHOE_MIN inside the zone is "off shoe" and does not count (see
predictions.py). The zone follows the pictures: it is made again after ON_PREDICT_ZONE_REFRESH_SETS new results, or by the
Rebuild button of the page (do that after a camera was moved: the latest sets must be the ones of the new position).

Files: captures/.predict/zones/<camera>.png (the mask, 255 = zone) and zones.json (when, from how many sets, the outline
of the zone as polygons in fractions of the picture, for the page to draw)."""

import json
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from ..core.config import (PREDICT_DIR, PREDICT_ZONE, PREDICT_ZONE_MIN_SETS, PREDICT_ZONE_REFRESH_SETS, PREDICT_ZONE_SETS,
                           PREDICT_ZONE_THR)

ZONE_W, ZONE_H = 320, 240
DIFF = 25            # a pixel is "different" from the median when it is this many grey levels away
MIN_BIT = 0.01       # pieces of the zone smaller than this part of the picture are noise
FOLDER = PREDICT_DIR / "zones"


def read_small(path):
    """The picture as a grey ZONE_W x ZONE_H array (decoded at 1/8 size: fast even for 4000 x 3000), or None."""
    try:
        img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8)
    except OSError:
        return None
    if img is None:
        return None
    return cv2.GaussianBlur(cv2.resize(img, (ZONE_W, ZONE_H), interpolation=cv2.INTER_AREA), (5, 5), 0)


def fill_holes(mask):
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(flooded, None, (0, 0), 1)
    return (mask | (1 - flooded[1:-1, 1:-1])).astype(np.uint8)


def zone_from_pictures(pictures, thr=None):
    """The zone mask (0/1, ZONE_H x ZONE_W) from a list of ZONE_W x ZONE_H grey pictures of one camera."""
    thr = PREDICT_ZONE_THR if thr is None else thr
    stack = np.stack(pictures).astype(np.float32)
    often = (np.abs(stack - np.median(stack, axis=0)) > DIFF).mean(axis=0)
    zone = (often > thr).astype(np.uint8)
    zone = cv2.morphologyEx(zone, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    zone = cv2.morphologyEx(zone, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(zone)
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > MIN_BIT * ZONE_W * ZONE_H]
    return fill_holes(np.isin(labels, keep).astype(np.uint8))


def polygons_of(mask):
    """The outline of a mask as polygons in fractions of the picture ([[[x, y], ...], ...]), simplified."""
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in found:
        c = cv2.approxPolyDP(c, 1.5, True)
        if len(c) >= 3:
            out.append([[round(float(x) / ZONE_W, 4), round(float(y) / ZONE_H, 4)] for x, y in c[:, 0, :]])
    return out


class Zones:
    def __init__(self):
        self._lock = threading.RLock()
        self._masks = {}     # camera -> mask (0/1)
        self._meta = {}      # camera -> {"sets", "area", "polygons", ...}
        self._info = {"built_at": None, "results": 0}
        self.version = 0     # +1 whenever the zones were made or loaded again
        self._building = False
        self._loaded = False

    # ---- reading what is on the disk ---------------------------------------------------------------------------

    def load(self):
        with self._lock:
            self._loaded = True
            try:
                doc = json.loads((FOLDER / "zones.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            masks = {}
            for role in doc.get("cameras", {}):
                img = cv2.imdecode(np.fromfile(str(FOLDER / f"{role}.png"), dtype=np.uint8), cv2.IMREAD_GRAYSCALE) \
                    if (FOLDER / f"{role}.png").exists() else None
                if img is not None and img.shape == (ZONE_H, ZONE_W):
                    masks[role] = (img > 127).astype(np.uint8)
            self._masks, self._meta = masks, {r: m for r, m in doc.get("cameras", {}).items() if r in masks}
            self._info = {"built_at": doc.get("built_at"), "results": doc.get("results", 0)}
            self.version += 1

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    # ---- using the zones ----------------------------------------------------------------------------------------

    def has(self, role):
        self._ensure_loaded()
        return PREDICT_ZONE and role in self._masks

    def inside(self, role, box):
        """The part of `box` (x0, y0, x1, y1 as fractions) that lies in the zone of that camera, or None without a zone."""
        if not self.has(role):
            return None
        mask = self._masks[role]
        x0, y0 = max(0, int(box[0] * ZONE_W)), max(0, int(box[1] * ZONE_H))
        x1, y1 = min(ZONE_W, max(int(np.ceil(box[2] * ZONE_W)), x0 + 1)), min(ZONE_H, max(int(np.ceil(box[3] * ZONE_H)), y0 + 1))
        part = mask[y0:y1, x0:x1]
        return float(part.mean()) if part.size else 0.0

    def status(self):
        self._ensure_loaded()
        return {"enabled": PREDICT_ZONE, "built_at": self._info.get("built_at"), "results": self._info.get("results", 0),
                "building": self._building, "threshold": PREDICT_ZONE_THR, "min_sets": PREDICT_ZONE_MIN_SETS,
                "cameras": {r: {k: m[k] for k in ("sets", "area", "polygons")} for r, m in self._meta.items()}}

    # ---- making them -------------------------------------------------------------------------------------------

    def due(self, n_results):
        """Time to make the zones (again)? Never while one is being made; first when there are PREDICT_ZONE_MIN_SETS results,
        then after PREDICT_ZONE_REFRESH_SETS new ones."""
        self._ensure_loaded()
        if not PREDICT_ZONE or self._building or n_results < PREDICT_ZONE_MIN_SETS:
            return False
        return not self._masks or n_results - self._info.get("results", 0) >= PREDICT_ZONE_REFRESH_SETS

    def build(self, ids_newest_first, picture_path):
        """Make the zones from the pictures of the newest sets. picture_path(set id, camera) -> a path or None.
        Returns {camera: sets used} (cameras with too few pictures get no zone)."""
        with self._lock:
            if self._building:
                return {}
            self._building = True
        try:
            roles = ("top", "back", "left", "front", "right")
            masks, meta, used = {}, {}, {}
            for role in roles:
                pictures = []
                for sid in ids_newest_first:
                    path = picture_path(sid, role)
                    small = read_small(path) if path else None
                    if small is not None:
                        pictures.append(small)
                    if len(pictures) >= PREDICT_ZONE_SETS:
                        break
                if len(pictures) < PREDICT_ZONE_MIN_SETS:
                    continue
                mask = zone_from_pictures(pictures)
                masks[role] = mask
                meta[role] = {"sets": len(pictures), "area": round(float(mask.mean()), 3), "polygons": polygons_of(mask)}
                used[role] = len(pictures)
            FOLDER.mkdir(parents=True, exist_ok=True)
            for role, mask in masks.items():
                ok, buf = cv2.imencode(".png", mask * 255)
                if ok:
                    (FOLDER / f"{role}.png").write_bytes(buf.tobytes())
            built_at = datetime.now().isoformat(timespec="seconds")
            doc = {"built_at": built_at, "results": len(ids_newest_first), "threshold": PREDICT_ZONE_THR, "cameras": meta}
            tmp = FOLDER / "zones.json.tmp"
            tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            tmp.replace(FOLDER / "zones.json")
            with self._lock:
                self._masks, self._meta = masks, meta
                self._info = {"built_at": built_at, "results": len(ids_newest_first)}
                self._loaded = True
                self.version += 1
            return used
        finally:
            self._building = False

    def build_async(self, ids_newest_first, picture_path, done=None):
        """build() on its own thread (a few seconds); done(used) is called when it is over. False when one is running."""
        if self._building:
            return False

        def run():
            try:
                used = self.build(ids_newest_first, picture_path)
            except Exception as e:   # the page keeps working without a zone
                print(f"[zones] could not make the shoe zones: {type(e).__name__}: {e}")
                used = {}
            if done:
                done(used)
        threading.Thread(target=run, name="shoe-zones", daemon=True).start()
        return True


zones = Zones()
