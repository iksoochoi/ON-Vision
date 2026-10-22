"""Defect detection results: one JSON per set in captures/.predict/<set id>.json, written by the predict worker
(backend/predict/worker.py, its own process) and read by the web pages.

    {"id", "timestamp", "shoe_id", "model", "device", "imgsz", "conf", "predicted_at", "ms",
     "defect": true | false | null (null = could not be predicted), "count", "classes": {name: n},
     "views": {role: {"ms", "detections": [{"class", "conf", "box": [x0, y0, x1, y1] as fractions of the picture}]}},
     "errors": {role: reason}, "error": reason (the whole set failed)}

The worker also keeps captures/.predict/.status.json (is it alive, how fast, how many waiting).
The index keeps a short summary of every result in memory (like the shoe index), so a page never reads the disk per
request; it looks at the folder at most every REFRESH_SEC."""

import json
import os
import threading
import time

from ..core.config import PREDICT_DIR, PREDICT_OK_CLASSES
from .paths import time_key

REFRESH_SEC = 1.5
STATUS_NAME = ".status.json"
STATUS_FRESH_SEC = 20.0   # the worker counts as alive when its status is this young


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


def is_defect(detections, ok_classes):
    """A detection of any class that is not an 'ok' class is a defect."""
    return any(d["class"] not in ok_classes for d in detections)


def summarise(result, ok_classes=()):
    """The short form the lists use."""
    classes = result.get("classes") or {}
    best = max((d["conf"] for v in (result.get("views") or {}).values() for d in v.get("detections", [])
                if d["class"] not in ok_classes), default=None)
    views = result.get("views") or {}
    return {"id": result["id"], "tk": time_key(result["id"]), "time": result.get("timestamp") or "",
            "shoe_id": result.get("shoe_id"), "defect": result.get("defect"), "count": result.get("count", 0),
            "classes": classes, "best": best, "ms": result.get("ms"), "error": result.get("error"),
            "defect_views": sorted(r for r, v in views.items()
                                   if any(d["class"] not in ok_classes for d in v.get("detections", [])))}


class PredictIndex:
    def __init__(self, ok_classes=()):
        self._ok = tuple(ok_classes)
        self._lock = threading.RLock()
        self._sets = {}      # id -> summary
        self._mtime = {}     # id -> mtime_ns of its file
        self._checked = 0.0
        self.version = 0
        self._status = None

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
                self._sets[sid], self._mtime[sid], changed = summarise(result, self._ok), mtime, True
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

    def page(self, flt="all", cls=None, date=None, limit=60, offset=0):
        """(rows newest first, totals) for the list: flt all | defect | ok | error; cls: only sets with this class;
        date: "2026-10-20". Totals count the whole index, `matching` the filter."""
        self.refresh()
        with self._lock:
            rows = sorted(self._sets.values(), key=lambda r: r["tk"], reverse=True)
            totals = {"all": len(rows), "defect": 0, "ok": 0, "error": 0}
            classes, dates = {}, {}
            for r in rows:
                if r["error"] or r["defect"] is None:
                    totals["error"] += 1
                elif r["defect"]:
                    totals["defect"] += 1
                else:
                    totals["ok"] += 1
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
            return picked[offset:offset + limit if limit else None], totals

    def remove(self, sample_ids):
        with self._lock:
            for sid in sample_ids:
                if self._sets.pop(sid, None) is not None:
                    self._mtime.pop(sid, None)
                    self.version += 1


index = PredictIndex(PREDICT_OK_CLASSES)
