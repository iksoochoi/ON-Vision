"""Defect detection results: one JSON per set in captures/.predict/<set id>.json, written by the predict worker
(backend/predict/worker.py, its own process) and read by the web pages.

    {"id", "timestamp", "shoe_id", "model", "device", "imgsz", "conf", "predicted_at", "ms",
     "defect": true | false | null (null = could not be predicted), "count", "classes": {name: n},
     "views": {role: {"ms", "sharpness", "shoe": {"area", "box"} (the shoe region of the picture, when the set has one),
                      "detections": [{"class", "conf", "box": [x0, y0, x1, y1] as fractions of the picture}]}},
     "errors": {role: reason}, "error": reason (the whole set failed)}
"defect" there is the RAW verdict of the model (any detection of a class that is not an ok class). The Predict page
corrects it (see Analysis below) and shows the corrected verdict.

The worker also keeps captures/.predict/.status.json (is it alive, how fast, how many waiting).
The index keeps a short summary of every result in memory (like the shoe index), so a page never reads the disk per
request; it looks at the folder at most every REFRESH_SEC.

Analysis (corrections, worked out from the stored results only, nothing is predicted again). A detection is left out of
the verdict (it is "not counted") when
  * its class is an ok class (the model has a class for a good print),
  * it is RECURRING: the same camera has the same class at the same place (IoU >= PREDICT_RECUR_IOU) in at least
    PREDICT_RECUR_MIN other sets. A defect of one shoe does not sit on the same pixels of most shoes: such a box is
    the background or something the model reads into every shoe (a logo, the print), or
  * it lies OFF the shoe: less than PREDICT_ON_SHOE_MIN of the box is inside the shoe region of that picture
    ("unknown" = no usable region: the detection is kept).
The corrected verdict of a set is "defect" when at least one detection is counted."""

import json
import os
import threading
import time

from ..core.config import (PREDICT_DIR, PREDICT_MODEL, PREDICT_OK_CLASSES, PREDICT_ON_SHOE_MIN, PREDICT_RECUR_IOU,
                           PREDICT_RECUR_MIN, PREDICT_SHOE_MAX_AREA)
from .paths import time_key
from .zones import zones

REFRESH_SEC = 1.5
STATUS_NAME = ".status.json"
STATUS_FRESH_SEC = 20.0   # the worker counts as alive when its status is this young
CHUNK = 512               # rows of the overlap matrix worked out at a time


def result_path(sample_id):
    return PREDICT_DIR / f"{sample_id}.json"


def _write_json(path, obj):
    """Atomically: a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def write_result(result):
    _write_json(result_path(result["id"]), result)


def read_result(sample_id):
    try:
        return json.loads(result_path(sample_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_status(status):
    _write_json(PREDICT_DIR / STATUS_NAME, status)


def read_status():
    """The worker's status with "alive" added, or {"alive": False} when there is none / it is stale."""
    try:
        status = json.loads((PREDICT_DIR / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"alive": False}
    status["alive"] = time.time() - status.get("ts", 0) < STATUS_FRESH_SEC
    return status


_metrics = {"mtime": None, "data": {}}


def class_metrics():
    """The validation results of the model per class ({class: {"map50", "instances", ...}}), read from
    class_metrics.json next to the model file; {} when there is none."""
    path = PREDICT_MODEL.with_name("class_metrics.json")
    try:
        mtime = path.stat().st_mtime_ns
        if _metrics["mtime"] != mtime:
            _metrics["data"] = json.loads(path.read_text(encoding="utf-8")).get("classes") or {}
            _metrics["mtime"] = mtime
    except (OSError, ValueError):
        _metrics["data"] = {}
    return _metrics["data"]


def inside_fraction(box, region):
    """The part of `box` (x0, y0, x1, y1) that lies inside `region` (same form), 0..1."""
    w = min(box[2], region[2]) - max(box[0], region[0])
    h = min(box[3], region[3]) - max(box[1], region[1])
    area = (box[2] - box[0]) * (box[3] - box[1])
    return max(0.0, w) * max(0.0, h) / area if area > 0 else 0.0


def shoe_region(result_view, set_view):
    """(box, area) of the shoe in one picture: what the worker stored in the result, else what the set's JSON says
    (views.<camera>.pick.shoe); (None, None) when there is none."""
    shoe = (result_view or {}).get("shoe")
    if not shoe and isinstance(set_view, dict):
        shoe = (set_view.get("pick") or {}).get("shoe")
    shoe = shoe or {}
    return shoe.get("box"), shoe.get("area")


def on_shoe(box, region, area):
    """("on" | "off" | "unknown", part of the box inside the region or None). No region, or one that covers more of
    the picture than PREDICT_SHOE_MAX_AREA (a wrong region), = unknown."""
    if not region or (area is not None and area > PREDICT_SHOE_MAX_AREA):
        return "unknown", None
    inside = inside_fraction(box, region)
    return ("on" if inside >= PREDICT_ON_SHOE_MIN else "off"), round(inside, 2)


def context(result, meta):
    """What helps to judge the detections of a set, worked out from what is stored: per picture its sharpness and
    the number of detections, per detection where it lies (on / off the shoe), whether it recurs in other sets and
    whether it counts for the verdict (see the top of this file), per class the validation mAP50 of the model."""
    metrics = class_metrics()
    set_views = (meta or {}).get("views") or {}
    flags = index.flags(result["id"])
    out = {"views": {}, "classes": {}, "verdict": index.verdict(result["id"]), "recur_min": PREDICT_RECUR_MIN,
           "on_shoe_min": PREDICT_ON_SHOE_MIN}
    for role, v in (result.get("views") or {}).items():
        sv = set_views.get(role) if isinstance(set_views.get(role), dict) else {}
        region, area = shoe_region(v, sv)
        dets = []
        for i, d in enumerate(v.get("detections", [])):
            f = flags.get((role, i)) or {}
            place, inside = (f["on"], f["inside"]) if f else on_shoe(d["box"], region, area)
            dets.append({"on_shoe": place, "inside": inside, "by": f.get("by", "region"), "recurring": f.get("recurring"),
                         "counted": f.get("counted"), "reasons": f.get("reasons", [])})
            m = metrics.get(d["class"])
            out["classes"][d["class"]] = {"map50": m["map50"], "instances": m["instances"]} if m else None
        sharp = v.get("sharpness") if v.get("sharpness") is not None else sv.get("sharpness")
        out["views"][role] = {"sharpness": sharp, "count": len(dets), "detections": dets,
                              "shoe": {"area": area, "trusted": bool(region) and (area is None or area <= PREDICT_SHOE_MAX_AREA)}
                              if region else None}
    return out


def is_defect(detections, ok_classes):
    """A detection of any class that is not an 'ok' class is a defect."""
    return any(d["class"] not in ok_classes for d in detections)


def _recurrence(dets, thr):
    """For every detection of `dets` (dicts with "role", "cls", "box", "set"): in how many detections of OTHER sets with
    the same camera and class does its box overlap (IoU >= thr)? Returns a list in the same order."""
    import numpy as np
    out = [0] * len(dets)
    groups = {}
    for n, d in enumerate(dets):
        groups.setdefault((d["role"], d["cls"]), []).append(n)
    for members in groups.values():
        if len(members) < 2:
            continue
        box = np.array([dets[n]["box"] for n in members], dtype=np.float32)
        sid = np.array([dets[n]["set"] for n in members])
        area = np.maximum(box[:, 2] - box[:, 0], 0) * np.maximum(box[:, 3] - box[:, 1], 0)
        for s in range(0, len(members), CHUNK):
            b = box[s:s + CHUNK]
            w = np.minimum(b[:, None, 2], box[None, :, 2]) - np.maximum(b[:, None, 0], box[None, :, 0])
            h = np.minimum(b[:, None, 3], box[None, :, 3]) - np.maximum(b[:, None, 1], box[None, :, 1])
            inter = np.maximum(w, 0) * np.maximum(h, 0)
            union = area[s:s + CHUNK, None] + area[None, :] - inter
            hit = (inter > 0) & (inter >= thr * np.maximum(union, 1e-9)) & (sid[s:s + CHUNK, None] != sid[None, :])
            for k, count in enumerate(hit.sum(axis=1)):
                out[members[s + k]] = int(count)
    return out


def summarise(result, dets, ok_classes=()):
    """The short form the lists use (raw verdict); `dets` as made by PredictIndex._detections."""
    classes = result.get("classes") or {}
    best = max((d["conf"] for d in dets if d["cls"] not in ok_classes), default=None)
    return {"id": result["id"], "tk": time_key(result["id"]), "time": result.get("timestamp") or "",
            "shoe_id": result.get("shoe_id"), "defect": result.get("defect"), "count": result.get("count", 0),
            "classes": classes, "best": best, "ms": result.get("ms"), "error": result.get("error"),
            "defect_views": sorted({d["role"] for d in dets if d["cls"] not in ok_classes})}


class PredictIndex:
    def __init__(self, ok_classes=()):
        self._ok = tuple(ok_classes)
        self._lock = threading.RLock()
        self._sets = {}      # id -> summary (raw) with "dets"
        self._mtime = {}     # id -> mtime_ns of its file
        self._checked = 0.0
        self.version = 0
        self._status = None
        self._analysis = (None, {})   # ((index version, zone version) it was made for, id -> corrected summary and flags)

    def _detections(self, result):
        """The detections of a result as a flat list with where they lie (the set's JSON is read only for results
        written before the worker stored the shoe region)."""
        meta, dets = None, []
        for role, v in (result.get("views") or {}).items():
            sv = {}
            if "shoe" not in v and v.get("detections"):
                if meta is None:
                    from .store import store   # late: the store imports this module
                    meta = store.get(result["id"]) or {}
                sv = (meta.get("views") or {}).get(role)
            region, area = shoe_region(v, sv)
            for i, d in enumerate(v.get("detections", [])):
                place, inside = on_shoe(d["box"], region, area)
                dets.append({"role": role, "i": i, "cls": d["class"], "conf": d["conf"], "box": d["box"],
                             "on": place, "inside": inside, "set": result["id"]})
        return dets

    def refresh(self, force=False):
        """Look at the folder (at most every REFRESH_SEC). Returns True when something changed (results or status)."""
        now = time.monotonic()
        with self._lock:
            if not force and now - self._checked < REFRESH_SEC:
                return False
            self._checked = now
            seen, changed = set(), False
            try:
                entries = list(os.scandir(PREDICT_DIR))
            except OSError:
                entries = []
            for e in entries:
                if not e.name.endswith(".json") or e.name == STATUS_NAME:
                    continue
                sid = e.name[:-5]
                seen.add(sid)
                try:
                    mtime = e.stat().st_mtime_ns
                except OSError:
                    continue
                if self._mtime.get(sid) == mtime:
                    continue
                result = read_result(sid)
                if result is None or "id" not in result:
                    continue
                dets = self._detections(result)
                summary = summarise(result, dets, self._ok)
                summary["dets"] = dets
                self._sets[sid], self._mtime[sid], changed = summary, mtime, True
            for sid in [s for s in self._sets if s not in seen]:
                del self._sets[sid]
                del self._mtime[sid]
                changed = True
            status = read_status()
            if self._status is None or any(status.get(k) != self._status.get(k) for k in ("alive", "processed", "pending", "failed")):
                changed = changed or self._status is not None
                self._status = status
            if changed:
                self.version += 1
            return changed

    def status(self):
        self.refresh()
        return dict(self._status or {"alive": False})

    # ---- the shoe zones (zones.py) ---------------------------------------------------------------------------------

    def ids_newest_first(self):
        with self._lock:
            return sorted(self._sets, key=lambda s: self._sets[s]["tk"], reverse=True)

    def _make_zones(self, force):
        from ..core.events import broadcast
        from .store import store   # late: the store imports this module
        ids = self.ids_newest_first()
        if not force and not zones.due(len(ids)):
            return False
        return zones.build_async(ids, lambda sid, role: store.view_file(sid, role), lambda used: broadcast({"key": "predict_changed"}))

    def ensure_zones(self):
        """Start making the shoe zones in the background when it is time (first ones, or after enough new results)."""
        return self._make_zones(False)

    def rebuild_zones(self):
        """The Rebuild button: make them again now, from the latest sets (after a camera was moved)."""
        self.refresh(force=True)
        return self._make_zones(True)

    # ---- the corrections --------------------------------------------------------------------------------------------

    def _analyse(self):
        """The corrected verdict of every set and the flags of every detection, made once per version of the index."""
        with self._lock:
            key = (self.version, zones.version)
            if self._analysis[0] == key:
                return self._analysis[1]
            flat = [d for s in self._sets.values() for d in s["dets"]]
            recur = _recurrence(flat, PREDICT_RECUR_IOU)
            for d, n in zip(flat, recur):
                d["recurring"] = n
            result = {}
            for sid, s in self._sets.items():
                flags, counted = {}, []
                for d in s["dets"]:
                    reasons = []
                    on, inside, by = d["on"], d["inside"], "region"
                    if on == "unknown":   # no shoe region in that picture: the learned zone of the camera, when there is one
                        z = zones.inside(d["role"], d["box"])
                        if z is not None:
                            on, inside, by = ("on" if z >= PREDICT_ON_SHOE_MIN else "off"), round(z, 2), "zone"
                    if d["cls"] in self._ok:
                        reasons.append("fine")
                    if d["recurring"] >= PREDICT_RECUR_MIN:
                        reasons.append("recurring")
                    if on == "off":
                        reasons.append("off shoe")
                    flags[(d["role"], d["i"])] = {"on": on, "inside": inside, "by": by, "recurring": d["recurring"],
                                                  "counted": not reasons, "reasons": reasons}
                    if not reasons:
                        counted.append(d)
                classes = {}
                for d in counted:
                    classes[d["cls"]] = classes.get(d["cls"], 0) + 1
                undecided = s["error"] or s["defect"] is None
                result[sid] = {"flags": flags, "defect": None if undecided else bool(counted), "count": len(counted),
                               "classes": classes, "best": max((d["conf"] for d in counted), default=None),
                               "defect_views": sorted({d["role"] for d in counted})}
            self._analysis = (self.version, result)
            return result

    def flags(self, sample_id):
        """{(camera, index of the detection): {"on", "inside", "recurring", "counted", "reasons"}} of one set."""
        self.refresh()
        return (self._analyse().get(sample_id) or {}).get("flags", {})

    def verdict(self, sample_id):
        """{"raw": ..., "corrected": ...} (defect true / false / None) of one set."""
        self.refresh()
        a = self._analyse().get(sample_id)
        s = self._sets.get(sample_id)
        if a is None or s is None:
            return None
        return {"raw": s["defect"], "corrected": a["defect"], "raw_count": s["count"], "count": a["count"]}

    def page(self, flt="all", cls=None, date=None, limit=60, offset=0, corrected=True):
        """(rows newest first, totals) for the list: flt all | defect | ok | error; cls: only sets with this class;
        date: "2026-10-20"; corrected: the verdict and classes after the corrections (else the raw ones of the model).
        Totals count the whole index, `matching` the filter."""
        self.refresh()
        with self._lock:
            analysis = self._analyse()
            rows = []
            for s in sorted(self._sets.values(), key=lambda r: r["tk"], reverse=True):
                a = analysis[s["id"]]
                row = {k: v for k, v in s.items() if k != "dets"}
                row.update(raw_defect=s["defect"], raw_count=s["count"])
                if corrected:
                    row.update(defect=a["defect"], count=a["count"], classes=a["classes"], best=a["best"],
                               defect_views=a["defect_views"])
                rows.append(row)
            totals = {"all": len(rows), "defect": 0, "ok": 0, "error": 0, "raw_defect": 0}
            classes, dates = {}, {}
            for r in rows:
                if r["error"] or r["defect"] is None:
                    totals["error"] += 1
                elif r["defect"]:
                    totals["defect"] += 1
                else:
                    totals["ok"] += 1
                totals["raw_defect"] += 1 if r["raw_defect"] and not r["error"] else 0
                for name, n in r["classes"].items():
                    classes[name] = classes.get(name, 0) + n
                dates[r["time"][:10]] = dates.get(r["time"][:10], 0) + 1

            def keep(r):
                if flt == "defect" and not (r["defect"] and not r["error"]):
                    return False
                if flt == "ok" and not (r["defect"] is False and not r["error"]):
                    return False
                if flt == "error" and not (r["error"] or r["defect"] is None):
                    return False
                if cls and cls not in r["classes"]:
                    return False
                return not date or r["time"][:10] == date

            picked = [r for r in rows if keep(r)]
            totals["matching"] = len(picked)
            totals["classes"] = dict(sorted(classes.items(), key=lambda kv: -kv[1]))
            totals["ok_classes"] = list(self._ok)
            totals["dates"] = dict(sorted(dates.items(), reverse=True))
            totals["corrected"] = corrected
            totals["recur_min"] = PREDICT_RECUR_MIN
            return picked[offset:offset + limit if limit else None], totals

    def remove(self, sample_ids):
        with self._lock:
            for sid in sample_ids:
                if self._sets.pop(sid, None) is not None:
                    self._mtime.pop(sid, None)
                    self.version += 1


index = PredictIndex(PREDICT_OK_CLASSES)
