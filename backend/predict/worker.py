"""The defect detection worker: its own process (a launchd service next to the camera app, see docs/predict.md).

    python -m backend.predict.worker            run until stopped
    python -m backend.predict.worker --once     predict what is waiting and stop (a test)
    python -m backend.predict.worker --backfill predict every set without a result, however old

It looks at captures/.sets/ for sets that have no result yet in captures/.predict/, newest first (only the sets whose
shoe's QR value was read: a set without one is only collected, never predicted), waits until a
set is complete, predicts its pictures (one per camera) and writes captures/.predict/<id>.json. Every picture goes
through two models: the shoe segmentation (backend/models/shoe_segmentation) cuts the shoe out and the background is replaced
by flat grey, then the defect model looks at that picture (ON_PREDICT_SEG=0: at the picture as it was saved). The camera app never
waits for it and never imports it: a slow or crashed worker cannot disturb the data collection. It runs at a low
priority, with few CPU threads (the GPU does the work on a Mac), and a backlog is never worked off at the cost of the
new sets: only the newest ON_PREDICT_MAX_BACKLOG waiting sets within ON_PREDICT_MAX_AGE_HOURS are considered."""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # also runnable as a plain file

from backend.core import config as C  # noqa: E402
from backend.storage import cutout, predictions  # noqa: E402
from backend.storage.paths import ID_RE, has_qr_prefix, image_path, primary_role, time_key  # noqa: E402

POLL_SEC = 2.0
SEG_NAME = f"{C.PREDICT_SEG_MODEL.parent.name}/{C.PREDICT_SEG_MODEL.name}"   # both model files are called best.pt: say which
SETTLE_SEC = 1.5       # a set file must be this old: the app writes the pictures first, but be sure
INCOMPLETE_SEC = 30.0  # pictures still missing after this long: predict what exists
STATUS_EVERY_SEC = 5.0
ONCE_WAIT_SEC = 60.0   # --once: how long to wait for sets that are not readable yet


def log(message):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [predict] {message}", flush=True)


def set_age_hours(stem, now):
    """How old a set is, from its id (yyyymmdd_hhmmss), or None when the id has no usable time."""
    try:
        t = datetime.strptime(time_key(stem)[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return (now - t).total_seconds() / 3600


def waiting(max_age_hours, max_backlog, now=None):
    """(ids to predict, newest first; how many older ones were left out). The ids are the set file names. Only sets whose
    shoe's QR value was read (the id starts with it) are predicted: the others are only collected."""
    now = datetime.fromtimestamp(now or time.time())
    try:
        sets = [e for e in os.scandir(C.SETS_DIR) if e.name.endswith(".json") and ID_RE.match(e.name[:-5])]
        done = {e.name[:-5] for e in os.scandir(C.PREDICT_DIR)} if C.PREDICT_DIR.exists() else set()
    except OSError:
        return [], 0
    todo = []
    for e in sets:
        stem = e.name[:-5]
        if stem in done or not has_qr_prefix(stem):
            continue
        age = set_age_hours(stem, now)
        if max_age_hours and age is not None and age > max_age_hours:
            continue
        todo.append(stem)
    todo.sort(key=time_key, reverse=True)
    return todo[:max_backlog], max(0, len(todo) - max_backlog)


def load_set(stem):
    """The set file as a dict, or None when it is not (yet) readable / too young."""
    path = C.SETS_DIR / f"{stem}.json"
    try:
        if time.time() - path.stat().st_mtime < SETTLE_SEC:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def predict_set(meta, model, device, engine, cv2, seg=None):
    """Predict the pictures of one set. `seg`: (segmentation model, its device, the segment module) or None.
    Returns the result dict (see storage/predictions.py)."""
    sid = meta["id"]
    primary = primary_role(meta)
    started = time.perf_counter()
    views, errors, seg_errors = {}, {}, {}
    for role in list(meta.get("views") or [primary]):
        path = image_path(sid, role, primary=role == primary)
        image = cv2.imread(str(path)) if path.exists() else None
        if image is None:
            errors[role] = "picture missing" if not path.exists() else "picture cannot be read"
            continue
        h, w = image.shape[:2]
        found, look = None, image
        if seg is not None:
            try:
                found = seg[2].predict(seg[0], seg[1], image, C.PREDICT_SEG_IMGSZ, C.PREDICT_SEG_CONF)
            except Exception as e:   # a picture the segmentation could not do is predicted as it is
                seg_errors[role] = f"{type(e).__name__}: {e}"
            if found and found["found"]:
                look = cutout.cutout(image, found["polygon"], C.PREDICT_SEG_GROW)
        res = engine.predict(model, device, look, C.PREDICT_IMGSZ, C.PREDICT_CONF)
        info = (meta.get("views") or {}).get(role) if isinstance((meta.get("views") or {}).get(role), dict) else {}
        shoe = (info.get("pick") or {}).get("shoe")   # the shoe region of this picture (sets saved with ON_PICK_MODE=best)
        views[role] = {"ms": round(res["ms"] + (found["ms"] if found else 0), 1), "sharpness": info.get("sharpness"), "shoe": shoe,
                       "detections": [
            {"class": d["class"], "conf": d["conf"],
             "box": [round(d["box"][0] / w, 4), round(d["box"][1] / h, 4), round(d["box"][2] / w, 4), round(d["box"][3] / h, 4)]}
            for d in res["detections"]]}
        if found is not None:
            views[role]["seg"] = ({"found": True, "conf": found["conf"], "count": found["count"], "area": found["area"],
                                   "ms": found["ms"], "polygon": found["polygon"]} if found["found"]
                                  else {"found": False, "count": found["count"], "ms": found["ms"]})
            if found["found"]:   # the outline of the shoe is a better "shoe region" than the one of the camera app
                views[role]["shoe"] = {"area": found["area"], "box": found["box"]}
    dets = [d for v in views.values() for d in v["detections"]]
    classes = {}
    for d in dets:
        classes[d["class"]] = classes.get(d["class"], 0) + 1
    result = {"id": sid, "timestamp": meta.get("timestamp"), "shoe_id": meta.get("shoe_id"),
              "model": C.PREDICT_MODEL.name, "device": device, "imgsz": C.PREDICT_IMGSZ, "conf": C.PREDICT_CONF,
              "predicted_at": datetime.now().isoformat(timespec="seconds"),
              "ms": round((time.perf_counter() - started) * 1000),
              "defect": predictions.is_defect(dets, C.PREDICT_OK_CLASSES) if views else None,
              "count": len(dets), "classes": classes, "views": views}
    if seg is not None:
        result["seg"] = {"model": SEG_NAME, "imgsz": C.PREDICT_SEG_IMGSZ, "conf": C.PREDICT_SEG_CONF,
                         "grow": C.PREDICT_SEG_GROW, "background_removed": [r for r, v in views.items() if (v.get("seg") or {}).get("found")]}
        if seg_errors:
            result["seg"]["errors"] = seg_errors
    if errors:
        result["errors"] = errors
    if not views:
        result["error"] = "no picture could be read"
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="predict what is waiting, then stop")
    ap.add_argument("--backfill", action="store_true", help="every set without a result, however old (and no backlog limit)")
    args = ap.parse_args()

    if os.name == "posix":
        try:
            os.nice(10)   # the camera app comes first
        except OSError:
            pass
    import cv2
    import numpy as np
    import torch
    from backend.predict import engine
    torch.set_num_threads(C.PREDICT_THREADS)
    C.PREDICT_DIR.mkdir(parents=True, exist_ok=True)
    model, device = engine.load(C.PREDICT_MODEL, C.PREDICT_DEVICE)
    engine.predict(model, device, np.zeros((64, 64, 3), np.uint8), C.PREDICT_IMGSZ, C.PREDICT_CONF)   # warm-up: the first prediction is slow
    seg = None
    if C.PREDICT_SEG:
        from backend.predict import segment
        if not C.PREDICT_SEG_MODEL.exists():
            log(f"WARNING: segmentation model not found ({C.PREDICT_SEG_MODEL}): the pictures are predicted as they were saved")
        else:
            seg_model, seg_device = segment.load(C.PREDICT_SEG_MODEL, C.PREDICT_DEVICE)
            blank = np.zeros((64, 64, 3), np.uint8)
            try:
                segment.predict(seg_model, seg_device, blank, C.PREDICT_SEG_IMGSZ, C.PREDICT_SEG_CONF)   # warm-up
            except Exception as e:   # e.g. a device that cannot do this model: the processor does it
                log(f"segmentation on {seg_device} failed ({type(e).__name__}: {e}): using the cpu for it")
                seg_model, seg_device = segment.load(C.PREDICT_SEG_MODEL, "cpu")
                segment.predict(seg_model, seg_device, blank, C.PREDICT_SEG_IMGSZ, C.PREDICT_SEG_CONF)
            seg = (seg_model, seg_device, segment)
    log("background removal: " + (f"ON, {SEG_NAME} on {seg[1]}, imgsz {C.PREDICT_SEG_IMGSZ}, conf {C.PREDICT_SEG_CONF}, "
                                 f"grow {C.PREDICT_SEG_GROW}" if seg else "off"))
    log(f"started: model {C.PREDICT_MODEL.name}, device {device}, imgsz {C.PREDICT_IMGSZ}, conf {C.PREDICT_CONF}, "
        f"threads {C.PREDICT_THREADS}, ok classes {C.PREDICT_OK_CLASSES}")

    stats = {"processed": 0, "failed": 0, "total_ms": 0, "last_id": None, "last_ms": None}
    started = time.time()
    last_status, skipped_logged = 0.0, 0
    limits = (0, 10 ** 9) if args.backfill else (C.PREDICT_MAX_AGE_HOURS, C.PREDICT_MAX_BACKLOG)
    first_pass = True
    while True:
        todo, left_out = waiting(*limits)
        if left_out and left_out != skipped_logged:
            skipped_logged = left_out
            log(f"{left_out} older waiting sets are left out (ON_PREDICT_MAX_BACKLOG={C.PREDICT_MAX_BACKLOG}; --backfill does them)")
        if first_pass and todo:
            log(f"{len(todo)} sets waiting")
        first_pass = False
        did = False
        for stem in todo:
            meta = load_set(stem)
            if meta is None or "id" not in meta:
                continue
            try:
                result = predict_set(meta, model, device, engine, cv2, seg)
            except Exception as e:   # one bad set must not stop the worker: it gets an error result and is not retried
                log(f"{stem} failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                result = {"id": meta["id"], "timestamp": meta.get("timestamp"), "shoe_id": meta.get("shoe_id"),
                          "predicted_at": datetime.now().isoformat(timespec="seconds"), "defect": None, "count": 0,
                          "classes": {}, "views": {}, "error": f"{type(e).__name__}: {e}"}
            predictions.write_result(result)
            did = True
            stats["processed"] += 1
            stats["failed"] += 1 if result.get("error") else 0
            stats["total_ms"] += result.get("ms", 0)
            stats["last_id"], stats["last_ms"] = result["id"], result.get("ms")
            tag = "ERROR " + result["error"] if result.get("error") else (
                "DEFECT " + ", ".join(f"{k} x{v}" for k, v in result["classes"].items() if k not in C.PREDICT_OK_CLASSES)
                if result["defect"] else "ok" + (f" ({', '.join(result['classes'])})" if result["classes"] else ""))
            log(f"{result['id']}: {result['ms']} ms, {result['count']} detections, {tag}"
                + (f", shoe cut out in {len(result['seg']['background_removed'])} of {len(result['views'])} pictures" if result.get("seg") else "")
                + (f", pictures missing: {', '.join(result['errors'])}" if result.get("errors") else "")
                + (f", segmentation failed: {', '.join(result['seg']['errors'])}" if (result.get("seg") or {}).get("errors") else ""))
            if time.time() - last_status >= STATUS_EVERY_SEC:
                break   # look at the waiting list again (newer sets come first) and write the status
        now = time.time()
        if did or now - last_status >= STATUS_EVERY_SEC:
            last_status = now
            pending, _ = waiting(*limits)
            predictions.write_status({
                "pid": os.getpid(), "ts": now, "started": started, "device": device, "model": C.PREDICT_MODEL.name,
                "seg_model": SEG_NAME if seg else None,
                "imgsz": C.PREDICT_IMGSZ, "processed": stats["processed"], "failed": stats["failed"],
                "pending": len(pending), "last_id": stats["last_id"], "last_ms": stats["last_ms"],
                "avg_ms": round(stats["total_ms"] / stats["processed"]) if stats["processed"] else None})
        if args.once and not did and (not todo or time.time() - started > ONCE_WAIT_SEC):
            return   # nothing left (sets that are too young to read are waited for a while)
        if not did:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
