"""Capture sets: pick the frame of every camera for a shot, build the set, and the trigger session.

One camera is the *reference* camera (default "top"): it detects the shoe (background subtraction or
YOLO) and drives the trigger. When CenterTrigger fires it reports up to three shots (before the center /
at the center / after the center); for each shot capture_set() takes from EVERY camera the frame closest
in time and stores them as one set."""

import time
from dataclasses import replace
from datetime import datetime

from ..camera.registry import registry
from ..core import config as C
from ..core.events import broadcast
from ..core.state import state, state_lock
from ..detect.trigger import CenterTrigger, Shot
from ..storage import capture_log
from ..storage.store import make_bbox, store
from . import disk_guard, sharpness

def describe_frame(frame):
    """Sensor size and the exposure/ISO the camera's auto-exposure picked (API varies by version)."""
    info = {}
    try:
        info["sensor_resolution"] = [frame.getWidth(), frame.getHeight()]
    except (AttributeError, TypeError):
        pass
    try:
        exp = frame.getExposureTime()
        info["exposure_us"] = (int(exp.total_seconds() * 1_000_000) if hasattr(exp, "total_seconds")
                               else int(exp))
    except (AttributeError, TypeError):
        info["exposure_us"] = None
    try:
        info["iso"] = int(frame.getSensitivity())
    except (AttributeError, TypeError):
        info["iso"] = None
    return info


PICK_MIN_GAIN = 0.05   # a frame further from the shot moment must be this much sharper than the nearest


def pick_frame(slot, ts, roi=None, exact=False):
    """The frame to save for one camera at moment ts: (frame_ts, frame, info), or None without frames.

    The nearest frame in time, unless ON_PICK_WINDOW_SEC lets it choose: then the sharpest frame within
    that window wins, but only when it is clearly sharper than the nearest one (the shoe keeps moving,
    so a frame further from ts must earn its place). roi = region of the shoe, normalized, if known.
    exact=True always takes the nearest frame (its bbox was found on exactly that frame).
    info = None when nothing was compared, else {"sharpness", "picked", "candidates": [[ms, score]]}."""
    nearest = slot.nearest(ts)
    if nearest is None:
        return None
    if exact or C.PICK_WINDOW_SEC <= 0:
        return (*nearest, None)
    cands = [(fts, fr, sharpness.score(gray, roi))
             for fts, fr, gray in slot.candidates(ts, C.PICK_WINDOW_SEC) if gray is not None]
    if len(cands) < 2:
        return (*nearest, None)
    near = min(cands, key=lambda c: abs(c[0] - ts))
    best = max(cands, key=lambda c: c[2])
    chosen = best if best[2] > near[2] * (1 + PICK_MIN_GAIN) else near
    info = {"sharpness": round(chosen[2], 1), "picked": chosen is not near,
            "candidates": [[round((c[0] - ts) * 1000), round(c[2], 1)] for c in cands]}
    return chosen[0], chosen[1], info


def pick_adjacent(slot, center_ts, direction, roi=None):
    """The frame right before (direction -1) or after (+1) the center frame (ON_SHOT_MODE=adjacent).

    A frame whose sharpness is below ON_ADJACENT_SHARP_RATIO x the center frame's is passed over for the
    next one further out, up to ON_ADJACENT_MAX_STEP frames; when none is sharp enough the sharpest of
    them wins. Without sharpness data the next frame is taken.
    Returns (frame_ts, info) or None when there is no frame on that side."""
    cands = slot.neighbors(center_ts, direction, C.ADJACENT_MAX_STEP)
    if not cands:
        return None
    center = slot.candidates(center_ts, 0)
    if not center or center[0][2] is None or any(gray is None for _, _, gray in cands):
        return cands[0][0], {"step": 1}
    ref = sharpness.score(center[0][2], roi)
    scores = [sharpness.score(gray, roi) for _, _, gray in cands]
    sharp = [i for i, s in enumerate(scores) if s >= ref * C.ADJACENT_SHARP_RATIO]
    i = sharp[0] if sharp else max(range(len(scores)), key=lambda k: scores[k])
    return cands[i][0], {"step": i + 1, "sharpness": round(scores[i], 1), "center_sharpness": round(ref, 1),
                         "candidates": [round(s, 1) for s in scores]}


def build_set(source, ts=None, sample=None, burst=None, exact=False):
    """One frame from every camera, the best one around ts (default: the latest reference frame):
    the nearest in time, or the sharpest within ON_PICK_WINDOW_SEC (see pick_frame).

    Cameras that are offline, have no frame, or whose chosen frame is further than
    ON_MAX_SKEW_SEC away are left out and listed in meta["missing_views"].
    burst: {"id", "kind", "index", "count"} when this set is one of the shots of a shoe pass.
    exact=True: ts is a reference camera frame that was already chosen; that very frame is saved.
    Returns (views, meta) for SampleStore.enqueue_burst, or None if there is nothing to save."""
    trigger = registry.trigger
    if ts is None:
        latest = trigger.latest()
        if latest is None:
            capture_log.event("SKIPPED", source, f"reason=\"no picture from the reference camera ({trigger.role}) yet\"")
            return None
        ts = latest[0]

    views, view_meta, missing = {}, {}, {}
    for slot in registry.slots():
        roi = None
        if slot is trigger and sample is not None:
            d = sample.det
            roi = sharpness.roi_from_box(d.xmin, d.ymin, d.xmax, d.ymax)
        # in model mode the reference camera's bbox belongs to the frame at ts: no swapping there
        picked = pick_frame(slot, ts, roi, exact=slot is trigger and (exact or C.DETECTOR == "model"))
        if picked is None:
            missing[slot.role] = "offline" if not slot.online else "no frame yet"
            continue
        frame_ts, frame, pick = picked
        offset = frame_ts - ts
        if abs(offset) > C.MAX_SKEW_SEC:
            missing[slot.role] = f"stale frame ({offset * 1000:+.0f} ms)"
            continue
        views[slot.role] = frame
        ae = describe_frame(frame)
        view_meta[slot.role] = {
            "offset_ms": round(offset * 1000, 1),
            "camera": slot.snapshot_params(),
            "ae": {"exposure_us": ae.pop("exposure_us", None), "iso": ae.pop("iso", None)},
            **ae,
        }
        if pick:
            view_meta[slot.role]["sharpness"] = pick["sharpness"]
            view_meta[slot.role]["pick"] = {"picked": pick["picked"], "candidates": pick["candidates"]}
    if trigger.role not in views:
        kind = f" ({burst['kind']} shot)" if burst else ""
        print(f"Capture skipped{kind}: no usable frame from the reference camera ({missing.get(trigger.role)})")
        capture_log.event("SKIPPED", source, f"{(burst or {}).get('kind') or ''} reason=\"no usable frame from the "
                          f"reference camera ({trigger.role}): {missing.get(trigger.role)}\"".strip())
        return None

    meta = {
        "source": source,
        "camera": view_meta[trigger.role]["camera"],      # kept for single-camera readers
        "ae": view_meta[trigger.role]["ae"],
        "sensor_resolution": view_meta[trigger.role].get("sensor_resolution"),
        "views": view_meta,
    }
    if burst:
        meta["burst"] = burst
    if missing:
        meta["missing_views"] = missing
        print("Missing views:", ", ".join(f"{r} ({why})" for r, why in missing.items()))
    if sample is not None:
        d = sample.det
        meta["detection"] = {
            "label": d.label, "confidence": round(d.conf, 3),
            "bbox_norm": [round(d.xmin, 4), round(d.ymin, 4), round(d.xmax, 4), round(d.ymax, 4)],
            "center_dist": round(sample.dist, 4),
            "track_id": sample.track_id, "n_obs": sample.n_obs,
            "frame_offset_ms": view_meta[trigger.role]["offset_ms"],
        }
        if C.DETECTOR == "model":  # background mode recomputes the bbox on the saved image
            meta["bbox"] = make_bbox(d.xmin, d.ymin, d.xmax, d.ymax, source="model")

    return views, meta


def qr_window(shots, latest_ts, now):
    """(since, until) in epoch seconds: the live QR reads that belong to a shoe pass. From
    ON_QR_LIVE_WINDOW_SEC before its first shot until ON_QR_WAIT_SEC after now (the trigger fires when the
    shoe has left the center, so the last shot is about now). The shots' times are on the device clock:
    the age of the first one is measured against the newest frame (latest_ts), then taken off `now`."""
    first = min((s.ts for s in shots), default=latest_ts)
    age = max(0.0, latest_ts - first) if latest_ts is not None and first is not None else 0.0
    return now - age - C.QR_LIVE_WINDOW_SEC, now + C.QR_WAIT_SEC


def _count_auto(n):
    with state_lock:
        state["auto_count"] += n


def capture_set(source, ts=None, sample=None, burst=None):
    """Build and queue one set (see build_set). Returns its base id, or None if nothing was saved."""
    job = build_set(source, ts, sample, burst)
    if job is None:
        return None
    ids = store.enqueue_burst([job])
    if ids and source == "auto":
        _count_auto(1)
    return ids[0] if ids else None


ADJACENT_WAIT_SEC = 1.0   # longest wait for the frames after the center frame (ON_SHOT_MODE=adjacent)


def _det_at(sample, ts):
    """(det, dist) of the track's observation nearest in time to ts (the bbox of an adjacent frame)."""
    if not sample.history:
        return sample.det, sample.dist
    _, det, dist = min(sample.history, key=lambda h: abs(h[0] - ts))
    return det, dist


class TriggerSession:
    """Feeds detections of the reference camera to CenterTrigger and saves the shots when it fires.

    ON_SHOT_MODE=adjacent: the trigger only finds the center; the "before" / "after" shots are the
    reference camera's frames right before / after the center frame (see pick_adjacent). A fired pass
    waits in _pending until the frames after the center have arrived."""

    def __init__(self, trigger=None):
        self.trigger = trigger or CenterTrigger(
            axis=C.TRIGGER_AXIS, center_tol=C.CENTER_TOL, away_hyst=C.AWAY_HYST,
            lost_sec=C.LOST_SEC, min_obs=C.MIN_OBS, edge_margin=C.EDGE_MARGIN,
            match_gate=C.MATCH_GATE, hold_sec=C.HOLD_SEC, shots=1 if C.ADJACENT else C.SHOTS,
            spread=C.SHOT_SPREAD, log=print,
        )
        self._was_auto = True
        self._pending = []   # (sample, monotonic time it fired), ON_SHOT_MODE=adjacent

    def reset(self):
        self.trigger.reset()
        self._pending = []

    def on_detections(self, ts, dets, auto_enabled, paused=False):
        """Returns the ids of the sets saved because of this detection message."""
        if auto_enabled != self._was_auto:
            self.reset()
            self._was_auto = auto_enabled
        if not auto_enabled or paused:
            return []
        if disk_guard.enforce()["blocked"]:   # the disk is too full: switched off, nothing more is saved
            self.reset()
            return []
        ids = []
        for sample in self.trigger.update(ts, [d for d in dets if d.target]):
            if C.ADJACENT:
                self._pending.append((sample, time.monotonic()))
            else:
                ids.extend(self._save(sample, sample.shots))
        if self._pending:
            ids.extend(self._flush_pending())
        return ids

    def _flush_pending(self):
        """Save the passes whose center frame already has ON_ADJACENT_MAX_STEP frames after it
        (or that waited ADJACENT_WAIT_SEC: the camera may have stalled)."""
        ref = registry.trigger
        ids, waiting = [], []
        for sample, fired in self._pending:
            near = ref.nearest(sample.ts)
            ready = (near is None or time.monotonic() - fired >= ADJACENT_WAIT_SEC
                     or len(ref.neighbors(near[0], 1, C.ADJACENT_MAX_STEP)) >= C.ADJACENT_MAX_STEP)
            if ready:
                ids.extend(self._save_adjacent(sample))
            else:
                waiting.append((sample, fired))
        self._pending = waiting
        return ids

    def _save_adjacent(self, sample):
        ref = registry.trigger
        d = sample.det
        roi = sharpness.roi_from_box(d.xmin, d.ymin, d.xmax, d.ymax)
        center = pick_frame(ref, sample.ts, roi, exact=C.DETECTOR == "model")
        if center is None:   # no frame at all: build_set logs why nothing is saved
            return self._save(sample, sample.shots)
        center_ts = center[0]
        shots, picks, notes = [], {}, []
        for kind, direction in (("before", -1), ("center", 0), ("after", 1)):
            if direction == 0:
                shots.append(Shot("center", center_ts, sample.det, sample.dist))
                continue
            got = pick_adjacent(ref, center_ts, direction, roi)
            if got is None:
                notes.append(f"{kind} none (no frame in the ring)")
                continue
            frame_ts, info = got
            det, dist = _det_at(sample, frame_ts)
            shots.append(Shot(kind, frame_ts, det, dist))
            picks[kind] = {**info, "offset_ms": round((frame_ts - center_ts) * 1000)}
            sharp = (f", sharpness {info['sharpness']} vs center {info['center_sharpness']}"
                     if "sharpness" in info else "")
            notes.append(f"{kind} {picks[kind]['offset_ms']:+d}ms (frame {info['step']}{sharp})")
        print(f"[trigger] track {sample.track_id} adjacent shots: " + ", ".join(notes))
        return self._save(sample, shots, exact=True, picks=picks)

    def _save(self, sample, shots, exact=False, picks=None):
        """Build one set per shot and queue them together. Returns the saved ids."""
        burst_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3] + f"-t{sample.track_id}"
        jobs = []
        for index, shot in enumerate(shots):
            part = replace(sample, ts=shot.ts, det=shot.det, dist=shot.dist)
            burst = {"id": burst_id, "kind": shot.kind, "index": index, "count": len(shots)}
            if picks and shot.kind in picks:
                burst["adjacent"] = picks[shot.kind]
            job = build_set("auto", ts=shot.ts, sample=part, burst=burst, exact=exact)
            if job:
                jobs.append(job)
        # all shots of the pass are saved together: the shoe's QR value, read from any of them,
        # then goes in front of the file names of every shot
        latest = registry.trigger.latest()
        window = qr_window(shots, latest[0] if latest else None, time.time())
        saved = store.enqueue_burst(jobs, qr_window=window) if jobs else []
        if saved:
            _count_auto(1)   # one shoe pass = one set, however many cuts it has
            broadcast({"key": "auto_capture"})
        return saved
