"""One picture per camera for a shoe pass: every camera picks its best frame of a time window (ON_PICK_MODE=best).

For each camera the candidates are the frames of its ring within +-ON_BEST_WINDOW_SEC of the center moment (about ten
frames; most were never saved before). Among them:
  1. the shoe must be CLEARLY in the frame: the reference camera (top) by its detection, the other cameras by
     comparing the frame with the empty-belt picture they learn by themselves (presence.py). It must cover enough of the
     picture (ON_PICK_MIN_AREA) and not be cut off by the border in the direction the belt moves (ON_PICK_EDGE_MARGIN);
  2. of those frames the sharpest one wins, measured over the shoe (for a frame further from the center to win, it must
     be clearly sharper than the nearest one: ON_PICK gain below);
  3. the cameras that look for the QR code (ON_QR_CAMERAS) measure the sharpness over the QR band of the picture at full
     resolution (a blur of a few pixels is invisible in the small copy of the frame ring, but it spoils a code), and
     prefer a frame in which the code can really be read: a frame the live scan has already read wins (free); else,
     if the live scan saw the code or its place lately, the sharpest few (ON_PICK_QR_TRIES) are read until one reads.
     A camera that sees no label (the code is on the other side of the shoe) costs no time. The saved picture then
     holds a readable code.
No clear shoe in any frame: the frame nearest to the center is saved and its JSON says so (ON_PICK_REQUIRE_SHOE=1: that
camera is left out instead).

This runs on its own thread: choosing and reading codes takes a second or two, the camera loops must not wait for it."""

import queue
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime

from ..camera.registry import registry
from ..core import config as C
from ..core.events import broadcast
from ..core.rotation import view_gray
from ..detect.trigger import Shot
from ..qr.hub import hub as qr_hub
from ..storage import capture_log
from ..storage.store import store
from . import sharpness
from .presence import axes_of, cut_off, presence
from .session import _count_auto, assemble_set, qr_window, view_entry

OBS_TOL_SEC = 0.15    # a detection counts for a frame at most this far from it
MIN_GAIN = 0.03       # a frame further from the center must be this much sharper than the nearest clear one
CENTER_BAND = (0.1, 0.1, 0.9, 0.9)
DEFAULT_QR_BAND = (0.0, 0.45, 1.0, 0.8)
QR_SEEN_SEC = 6.0     # a camera whose live scan saw a code this lately probably sees the label now
LIVE_MATCH_SEC = 0.002  # a live read belongs to a frame of the ring when their times differ by less than this
MAX_JOBS = 4


def qr_band():
    """The QR band as a box of the picture (x0, y0, x1, y1): ON_QR_ROI, else the band where the label usually is."""
    if C.QR_ROI:
        x, y, w, h = C.QR_ROI
        return (x, y, x + w, y + h)
    return DEFAULT_QR_BAND


def band_sharpness(frame, box):
    """Sharpness (the measure of sharpness.score) over a box of the picture at FULL resolution."""
    gray = view_gray(frame)
    h, w = gray.shape[:2]
    part = gray[int(box[1] * h):max(int(box[3] * h), int(box[1] * h) + 8), int(box[0] * w):max(int(box[2] * w), int(box[0] * w) + 8)]
    return sharpness.score(part)


def obs_at(sample, ts):
    """(det, dist) of the reference camera's observation nearest to the frame time ts, within OBS_TOL_SEC, else (None, None)."""
    history = sample.history or [(sample.ts, sample.det, sample.dist)]
    t, det, dist = min(history, key=lambda h: abs(h[0] - ts))
    return (det, dist) if abs(t - ts) <= OBS_TOL_SEC else (None, None)


def _evaluate(slot, center_ts, sample, window):
    """Every candidate frame of a camera with what is known about it: {"ts", "frame", "gray", "clear" (True / False /
    None = could not be told), "shoe" (box and area), "score"}."""
    is_top = slot is registry.trigger
    axes = axes_of(slot.role)
    out = []
    for fts, frame, gray in slot.candidates(center_ts, window):
        ev = {"ts": fts, "frame": frame, "gray": gray, "clear": None, "shoe": None, "score": 0.0, "det": None, "dist": None}
        crop = getattr(frame, "crop", None)
        roi = crop or CENTER_BAND
        if is_top:
            det, dist = obs_at(sample, fts)
            ev["det"], ev["dist"] = det, dist
            if det is None:
                ev["clear"] = False   # the detector did not see the shoe in this frame
            else:
                box = (det.xmin, det.ymin, det.xmax, det.ymax)
                ev["shoe"] = {"area": round(det.area, 3), "box": [round(v, 3) for v in box]}
                ev["clear"] = det.area >= C.PICK_MIN_AREA and not cut_off(box, None, axes, C.PICK_EDGE_MARGIN)
                roi = sharpness.roi_from_box(*box)
        elif gray is not None:
            found = presence.analyse(slot.role, gray, slot.rotation)
            if found is not None:
                if found["found"]:
                    box = found["box"]
                    ev["shoe"] = {"area": round(found["area"], 3), "box": [round(v, 3) for v in box]}
                    ev["clear"] = found["area"] >= C.PICK_MIN_AREA and not cut_off(box, crop, axes, C.PICK_EDGE_MARGIN)
                    roi = sharpness.roi_from_box(*box)
                else:
                    ev["clear"] = False
        ev["score"] = sharpness.score(gray, roi) if gray is not None else 0.0
        out.append(ev)
    return out


def _qr_likely(role, now):
    """Did the live scan of this camera see a QR code (or just place one) a moment ago?"""
    seen = qr_hub.scanning.get(role) or {}
    return now - seen.get("epoch", 0) < QR_SEEN_SEC and bool(seen.get("found") or seen.get("size_px"))


def choose(slot, center_ts, sample, use_qr, window=None):
    """The best frame of one camera: (evaluation dict of the chosen frame, pick info for the JSON), or (None, reason)."""
    window = C.BEST_WINDOW_SEC if window is None else window
    cands = _evaluate(slot, center_ts, sample, window)
    if not cands:
        return None, "offline" if not slot.online else "no frame in the window"
    if use_qr:   # the sharpness that matters is the one over the QR band, at full resolution
        band = qr_band()
        for ev in cands:
            try:
                ev["score"] = band_sharpness(ev["frame"], band)
            except Exception:
                pass
    clear = [e for e in cands if e["clear"]]
    unknown = all(e["clear"] is None for e in cands)
    info = {"mode": "best", "candidates": len(cands), "clear": len(clear)}
    if clear:
        pool = clear
    elif unknown:   # the empty-belt picture is not known yet: nothing to compare with
        pool, info["shoe_check"] = cands, "unverified"
    elif C.PICK_REQUIRE_SHOE:
        return None, "no clear shoe in any frame"
    else:
        pool, info["shoe_check"] = cands, "not clear"
    nearest = min(pool, key=lambda e: abs(e["ts"] - center_ts))
    best = max(pool, key=lambda e: e["score"])
    chosen = best if best["score"] > nearest["score"] * (1 + MIN_GAIN) else nearest
    if use_qr:
        tried, readable, source = 0, False, None
        # 1. the live scan already read the code in some of these frames (it scans about every other frame): free
        known = qr_hub.read_frames(slot.role, center_ts - window, center_ts + window)
        live = [e for e in pool if any(abs(e["ts"] - t) < LIVE_MATCH_SEC for t in known)]
        if live:
            chosen, readable, source = max(live, key=lambda e: e["score"]), True, "live"
        elif _qr_likely(slot.role, time.time()):
            # 2. the live scan saw a code or its place lately: read the sharpest few until one reads
            started = time.monotonic()
            from ..qr.decode import decode_qr
            for ev in sorted(pool, key=lambda e: -e["score"])[:C.PICK_QR_TRIES]:
                if tried and time.monotonic() - started >= C.PICK_QR_BUDGET_SEC:
                    break
                tried += 1
                try:
                    found = decode_qr(ev["frame"].getCvFrame(), roi=C.QR_ROI)   # the plain reading: fast; the store reads the saved one in full
                except Exception:
                    found = []
                if found:
                    chosen, readable, source = ev, True, "read"
                    break
        # else: this camera sees no label (the code is on the other side of the shoe): no time is spent on it
        info["qr"] = {"tried": tried, "readable": readable}
        if source:
            info["qr"]["by"] = source
    info["score"] = round(chosen["score"], 1)
    if chosen["shoe"]:
        info["shoe"] = chosen["shoe"]
    return chosen, info


class Picker(threading.Thread):
    def __init__(self):
        super().__init__(name="picker", daemon=True)
        self._jobs = queue.Queue(maxsize=MAX_JOBS)
        self._start_lock = threading.Lock()

    def submit(self, sample):
        """A shoe pass fired: pick its pictures (on this thread). The thread starts with the first one."""
        with self._start_lock:
            if not self.is_alive():
                self.start()
        try:
            self._jobs.put_nowait(sample)
        except queue.Full:
            print("[pick] the picker is backed up - capture dropped")
            capture_log.event("DROPPED", "auto", "cuts=1 reason=\"the picker is backed up\"")

    def run(self):
        while True:
            sample = self._jobs.get()
            try:
                self.process(sample)
            except Exception:   # one bad pass must not end the thread
                print("[pick] failed:\n" + traceback.format_exc())
                capture_log.event("SKIPPED", "auto", "reason=\"the picture choice failed (see app.log)\"")

    def process(self, sample, enqueue=None):
        """Choose a frame for every camera and queue the set. Returns the ids (or [])."""
        enqueue = enqueue or store.enqueue_burst
        center_ts = sample.ts
        trigger = registry.trigger
        qr_roles = set()
        if C.QR_ENABLED:
            from ..qr.decode import qr_roles as _roles
            qr_roles = set(_roles(registry.roles()))
        views, view_meta, missing, notes = {}, {}, {}, []
        part = sample
        for slot in registry.slots():
            chosen, info = choose(slot, center_ts, sample, use_qr=slot.role in qr_roles and slot is not trigger)
            if chosen is None:
                missing[slot.role] = info
                notes.append(f"{slot.role} left out ({info})")
                continue
            views[slot.role] = chosen["frame"]
            entry = view_entry(slot, chosen["frame"], chosen["ts"] - center_ts)
            entry["sharpness"] = info["score"]
            entry["pick"] = info
            view_meta[slot.role] = entry
            if slot is trigger and chosen["det"] is not None:
                part = replace(sample, ts=chosen["ts"], det=chosen["det"], dist=chosen["dist"])
            qr = info.get("qr")
            notes.append(f"{slot.role} {entry['offset_ms']:+.0f}ms score {info['score']} ({info['clear']}/{info['candidates']} clear"
                         + (f", {info['shoe_check']}" if "shoe_check" in info else "")
                         + ((f", QR readable ({qr.get('by')})" if qr["readable"] else
                             f", QR not read after {qr['tried']}" if qr["tried"] else ", no label seen") if qr else "") + ")")
        print(f"[pick] track {sample.track_id}: " + "; ".join(notes))
        burst_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3] + f"-t{sample.track_id}"
        burst = {"id": burst_id, "kind": "center", "index": 0, "count": 1}
        job = assemble_set("auto", views, view_meta, missing, part, burst)
        if job is None:
            return []
        latest = trigger.latest()
        window = qr_window([Shot("center", part.ts, part.det, part.dist)], latest[0] if latest else None, time.time())
        ids = enqueue([job], qr_window=window, center_ts=part.ts)
        if ids:
            _count_auto(1)
            broadcast({"key": "auto_capture"})
        return ids


picker = Picker()
