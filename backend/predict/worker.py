"""The defect detection worker: its own process (a launchd service next to the camera app, see docs/predict.md).

    python -m backend.predict.worker            run until stopped
    python -m backend.predict.worker --once     predict what is waiting and stop (a test)
    python -m backend.predict.worker --backfill predict every set without a result, however old

It looks at captures/.sets/ for sets that have no result yet in captures/.predict/, newest first (only the sets whose
shoe's QR value was read: a set without one is only collected, never predicted), waits until a
set is complete, predicts its pictures (one per camera) and writes captures/.predict/<id>.json. Every picture goes
through two models: the shoe segmentation (backend/models/shoe_segmentation) cuts the shoe out and the background is replaced
by flat grey, then the defect model looks at that picture (ON_PREDICT_SEG=0: at the picture as it was saved). After the
model the bonding gap is measured on the left and right pictures as they were saved (bonding_gap.py, the QC viewer's
measurement; ON_PREDICT_GAP=0: not): the set is a defect when the model or the gap says so. The camera app never
waits for it and never imports it: a slow or crashed worker cannot disturb the data collection. It runs at a low
priority, with few CPU threads (the GPU does the work on a Mac), and a backlog is never worked off at the cost of the
new sets: only the newest ON_PREDICT_MAX_BACKLOG waiting sets within ON_PREDICT_MAX_AGE_HOURS are considered.
The new sets are predicted only while "Predict new sets" is ticked on the Predict page (captures/.predict/.live.json; off by
default: then only the periods are predicted). --once and --backfill always predict the waiting sets.
A period asked for on the Predict page (captures/.predict/.job.json, see PeriodJob) is worked off only while no new set waits."""

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
from backend.storage import cutout, gap_calib, predictions  # noqa: E402
from backend.storage.paths import ID_RE, has_qr_prefix, image_path, primary_role, time_key  # noqa: E402

POLL_SEC = 2.0
SEG_NAME = f"{C.PREDICT_SEG_MODEL.parent.name}/{C.PREDICT_SEG_MODEL.name}"   # both model files are called best.pt: say which
SETTLE_SEC = 1.5       # a set file must be this old: the app writes the pictures first, but be sure
INCOMPLETE_SEC = 30.0  # pictures still missing after this long: predict what exists
STATUS_EVERY_SEC = 5.0
ONCE_WAIT_SEC = 60.0   # --once: how long to wait for sets that are not readable yet
YOUNG_RETRY_SEC = 30.0  # a period's set file this young that cannot be read yet is tried again later, an older one is left out


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


class PeriodJob:
    """A period asked for on the Predict page (storage/predictions.py JOB_NAME). Its sets are predicted only when no new set
    is waiting, newest first; with "redo" also the sets that already have a result. self.summary goes into the status."""

    def __init__(self, req):
        self.req = req
        ids = predictions.period_ids(req["from"], req["to"])
        self.todo = [s for s in ids if not predictions.job_done(s, req)]
        self.summary = {"id": req["id"], "from": req["from"], "to": req["to"], "redo": bool(req.get("redo")),
                        "sets": len(ids), "total": len(self.todo), "done": 0, "failed": 0, "skipped": 0,
                        "state": "running", "started": time.time()}

    def finish(self, state):
        self.summary.update(state=state, finished=time.time())
        if state == "done":
            predictions.clear_job(self.req["id"])


def current_job(job):
    """The job to work on now: the same one while its request stays, a new one for a new request. A request that went
    away (cancelled on the page) ends the job. Returns (job or None, the summary of a job that just ended or None)."""
    req = predictions.read_job()
    if job is not None and (req is None or req.get("id") != job.req["id"]):
        job.finish("cancelled")
        ended, job = job.summary, None
        if req is None:
            return None, ended
    else:
        ended = None
    if job is None and req is not None:
        try:
            job = PeriodJob(req)
        except (KeyError, TypeError, AttributeError) as e:
            log(f"period request not understood ({type(e).__name__}: {e}): removed")
            predictions.clear_job(req.get("id"))
            return None, ended
        log(f"period {req['from']} ~ {req['to']}{' (again)' if req.get('redo') else ''}: {job.summary['sets']} sets, "
            f"{job.summary['total']} to predict")
    return job, ended


def current_calib(calib):
    """The cam plate calibration to work on (asked for on the Predict page, see storage/gap_calib.py), like
    current_job: (CalibJob or None, the summary of one that was cancelled or None)."""
    from backend.predict.calib_job import CalibJob
    req = gap_calib.read_request()
    ended = None
    if calib is not None and (req is None or req.get("id") != calib.req["id"]):
        calib.summary.update(state="cancelled", finished=time.time())
        ended, calib = calib.summary, None
    if calib is None and req is not None:
        try:
            calib = CalibJob(req)
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            log(f"cam plate calibration request not understood ({type(e).__name__}: {e}): removed")
            gap_calib.clear_request(req.get("id"))
            return None, ended
        log(f"cam plate calibration {req['from']} ~ {req['to']}: {calib.summary['sets']} sets, left and right")
    return calib, ended


def load_set(stem):
    """The set file as a dict, or None when it is not (yet) readable / too young."""
    path = C.SETS_DIR / f"{stem}.json"
    try:
        if time.time() - path.stat().st_mtime < SETTLE_SEC:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clean(v):
    """The gap result as JSON can hold it: NaN / inf (a line not found) as None."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if not str(k).startswith("_")}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    return v


def measure_gap(meta):
    """The bonding gap of both sides (left, right cameras) of a set: {"algo", "limit_mm", "over_min_mm", "ms", "defect",
    "views": {camera: bonding_gap result}}; "error" when the measurement itself broke."""
    from backend.predict import bonding_gap
    bonding_gap.OVER_MIN_MM = C.PREDICT_GAP_OVER_MM
    sid, primary = meta["id"], primary_role(meta)
    pics = {v: image_path(sid, v, primary=v == primary) for v in bonding_gap.SIDES}
    started = time.perf_counter()
    out = {"algo": bonding_gap.ALGO, "limit_mm": C.PREDICT_GAP_LIMIT_MM, "over_min_mm": C.PREDICT_GAP_OVER_MM,
           "calib": list(gap_calib.in_use())}   # the cam plate calibration that measured it: ("site" | "default", version)
    try:
        sides = _clean(bonding_gap.measure_both(C.SETS_DIR / f"{sid}.json", C.PREDICT_GAP_LIMIT_MM, pics=pics))
    except Exception as e:   # the model's result stays: only the gap is missing
        log(f"{sid} bonding gap failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        out.update(ms=round((time.perf_counter() - started) * 1000), defect=None, error=f"{type(e).__name__}: {e}")
        return out
    out.update(ms=round((time.perf_counter() - started) * 1000), views=sides,
               defect=any(bool(r.get("defect")) for r in sides.values()))
    return out


def gap_text(gap):
    """The gap for the log: "gap BONDING_GAP left 6.1 mm over 45 mm, right 3.2 mm (1840 ms)"."""
    if gap.get("error"):
        return f"gap ERROR {gap['error']}"
    parts = []
    for v, r in (gap.get("views") or {}).items():
        if r.get("max_mm") is not None:
            parts.append(f"{v} {r['max_mm']} mm" + (f" over {r['over_mm']:g} mm" if r.get("over_mm") else "")
                         + ("" if r.get("status") == "ok" else f" ({r.get('status')})"))
        else:
            parts.append(f"{v} {r.get('status')}")
    return f"gap {'BONDING_GAP ' if gap.get('defect') else ''}{', '.join(parts)} ({gap.get('ms')} ms)"


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
              "model": C.PREDICT_MODEL_NAME, "device": device, "imgsz": C.PREDICT_IMGSZ, "conf": C.PREDICT_CONF,
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
    if C.PREDICT_GAP and views:   # after the model: a slow or failing measurement never loses the model's result
        result["model_defect"] = result["defect"]
        result["gap"] = measure_gap(meta)
        result["defect"] = bool(result["defect"] or result["gap"].get("defect"))
        result["ms"] = round((time.perf_counter() - started) * 1000)
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
    cv2.setNumThreads(C.PREDICT_THREADS)   # the bonding gap (OpenCV) too: the camera app keeps its cores
    C.PREDICT_DIR.mkdir(parents=True, exist_ok=True)
    model, device = engine.load(C.PREDICT_MODEL, C.PREDICT_DEVICE)
    engine.predict(model, device, np.zeros((64, 64, 3), np.uint8), C.PREDICT_IMGSZ, C.PREDICT_CONF)   # warm-up: the first prediction is slow
    seg = None
    if C.PREDICT_SEG:
        from backend.predict import segment
        if not C.PREDICT_SEG_MODEL.exists():
            log(f"WARNING: segmentation model not found ({C.PREDICT_SEG_MODEL}): the pictures are predicted as they were saved")
        else:
            seg_model, seg_device = segment.load_warm(C.PREDICT_SEG_MODEL, C.PREDICT_DEVICE, C.PREDICT_SEG_IMGSZ, C.PREDICT_SEG_CONF, log)
            seg = (seg_model, seg_device, segment)
    log("background removal: " + (f"ON, {SEG_NAME} on {seg[1]}, imgsz {C.PREDICT_SEG_IMGSZ}, conf {C.PREDICT_SEG_CONF}, "
                                 f"grow {C.PREDICT_SEG_GROW}" if seg else "off"))
    log("bonding gap: " + (f"ON, a defect over {C.PREDICT_GAP_LIMIT_MM:g} mm along {C.PREDICT_GAP_OVER_MM:g} mm" if C.PREDICT_GAP else "off"))
    log(f"started: model {C.PREDICT_MODEL_NAME}, device {device}, imgsz {C.PREDICT_IMGSZ}, conf {C.PREDICT_CONF}, "
        f"threads {C.PREDICT_THREADS}, ok classes {C.PREDICT_OK_CLASSES}")

    stats = {"processed": 0, "failed": 0, "total_ms": 0, "last_id": None, "last_ms": None}
    started = time.time()
    last_status, skipped_logged = 0.0, 0
    limits = (0, 10 ** 9) if args.backfill else (C.PREDICT_MAX_AGE_HOURS, C.PREDICT_MAX_BACKLOG)
    first_pass = True
    job, last_job = None, None   # the period being predicted, and the one before it (shown on the page until a new one)
    calib, last_calib = None, None   # the cam plate calibration being made, and the one before it
    plate_stamp = None
    forced = args.once or args.backfill   # asked for on the command line: the page switch does not count
    live_was = None

    def predict_and_write(meta, tag_prefix=""):
        try:
            result = predict_set(meta, model, device, engine, cv2, seg)
        except Exception as e:   # one bad set must not stop the worker: it gets an error result and is not retried
            log(f"{meta['id']} failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            result = {"id": meta["id"], "timestamp": meta.get("timestamp"), "shoe_id": meta.get("shoe_id"),
                      "predicted_at": datetime.now().isoformat(timespec="seconds"), "defect": None, "count": 0,
                      "classes": {}, "views": {}, "error": f"{type(e).__name__}: {e}"}
        predictions.write_result(result)
        stats["processed"] += 1
        stats["failed"] += 1 if result.get("error") else 0
        stats["total_ms"] += result.get("ms", 0)
        stats["last_id"], stats["last_ms"] = result["id"], result.get("ms")
        tag = "ERROR " + result["error"] if result.get("error") else (
            "DEFECT " + ", ".join(f"{k} x{v}" for k, v in result["classes"].items() if k not in C.PREDICT_OK_CLASSES)
            if result.get("model_defect", result["defect"]) else "ok" + (f" ({', '.join(result['classes'])})" if result["classes"] else ""))
        log(f"{tag_prefix}{result['id']}: {result['ms']} ms, {result['count']} detections, {tag}"
            + (f", shoe cut out in {len(result['seg']['background_removed'])} of {len(result['views'])} pictures" if result.get("seg") else "")
            + (f", pictures missing: {', '.join(result['errors'])}" if result.get("errors") else "")
            + (f", segmentation failed: {', '.join(result['seg']['errors'])}" if (result.get("seg") or {}).get("errors") else "")
            + (f", {gap_text(result['gap'])}" if result.get("gap") else ""))
        return result

    while True:
        loop_started = time.time()
        live = forced or predictions.read_live()
        if live != live_was:
            live_was = live
            log("new sets: " + ("predicted" if live else "not predicted (Predict new sets is off on the Predict page)"))
            first_pass, last_status = True, 0.0   # the page shows the switch from the status: write it at once
        todo, left_out = waiting(*limits) if live else ([], 0)
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
            predict_and_write(meta)
            did = True
            if time.time() - last_status >= STATUS_EVERY_SEC:
                break   # look at the waiting list again (newer sets come first) and write the status
        if C.PREDICT_GAP and gap_calib.stamp() != plate_stamp:   # the calibration in use changed on the page: no restart
            from backend.predict import bonding_gap
            plate_stamp = gap_calib.stamp()
            bonding_gap.set_plate_dir(gap_calib.plate_dir())
            log("bonding gap cam plate calibration: %s %s" % gap_calib.in_use())
        job, ended = current_job(job)
        if ended:
            last_job = ended
            log(f"period {ended['from']} ~ {ended['to']} cancelled: {ended['done']} of {ended['total']} predicted")
            last_status = 0.0   # say so at once
        if job is not None and not did:   # a period only while no new set was predicted in this round
            young = []   # sets just being saved: again in a later round
            while job.todo and time.time() - loop_started < STATUS_EVERY_SEC:
                stem = job.todo.pop(0)
                if predictions.job_done(stem, job.req):   # the normal round did it in the meantime
                    job.summary["done"] += 1
                    continue
                meta = load_set(stem)
                if meta is None or "id" not in meta:
                    try:
                        fresh = time.time() - (C.SETS_DIR / f"{stem}.json").stat().st_mtime < YOUNG_RETRY_SEC
                    except OSError:
                        fresh = False
                    if fresh:
                        young.append(stem)
                    else:   # deleted, or not readable: left out
                        job.summary["skipped"] += 1
                    continue
                result = predict_and_write(meta, f"period {job.summary['done'] + job.summary['skipped'] + 1}/{job.summary['total']} ")
                job.summary["done"] += 1
                job.summary["failed"] += 1 if result.get("error") else 0
                did = True
            job.todo.extend(young)
            if not job.todo:
                job.finish("done")
                s = job.summary
                log(f"period {s['from']} ~ {s['to']} done: {s['done']} predicted ({s['failed']} failed), {s['skipped']} left out, "
                    f"{round(s['finished'] - s['started'])} s")
                last_job, job = s, None
                last_status = 0.0   # say so at once
        calib, ended = current_calib(calib) if C.PREDICT_GAP else (None, None)
        if ended:
            last_calib = ended
            log(f"cam plate calibration {ended['from']} ~ {ended['to']} cancelled")
            last_status = 0.0
        busy = False
        if calib is not None and not did and job is None:   # after the new sets and the period: a picture at a time
            busy = True
            while time.time() - loop_started < STATUS_EVERY_SEC:
                try:
                    more = calib.step()
                except Exception as e:   # a broken calibration must not stop the worker
                    log(f"cam plate calibration failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                    calib.summary.update(state="failed", error=f"{type(e).__name__}: {e}", finished=time.time())
                    gap_calib.clear_request(calib.req["id"])
                    last_calib, calib = calib.summary, None
                    last_status = 0.0
                    break
                if not more:
                    s = calib.finish()
                    log(f"cam plate calibration {s['from']} ~ {s['to']} {s['state']}: {s.get('errors') or 'candidate written'}, "
                        f"{round(s['finished'] - s['started'])} s")
                    last_calib, calib = s, None
                    last_status = 0.0
                    break
        now = time.time()
        if did or now - last_status >= STATUS_EVERY_SEC:
            last_status = now
            pending, _ = waiting(*limits) if live else ([], 0)
            predictions.write_status({
                "pid": os.getpid(), "ts": now, "started": started, "device": device, "model": C.PREDICT_MODEL_NAME,
                "seg_model": SEG_NAME if seg else None,
                "imgsz": C.PREDICT_IMGSZ, "processed": stats["processed"], "failed": stats["failed"],
                "pending": len(pending), "last_id": stats["last_id"], "last_ms": stats["last_ms"],
                "avg_ms": round(stats["total_ms"] / stats["processed"]) if stats["processed"] else None, "live": live,
                "job": dict(job.summary) if job is not None else last_job,
                "calib": dict(calib.summary) if calib is not None else last_calib})
        if args.once and not did and job is None and calib is None and (not todo or time.time() - started > ONCE_WAIT_SEC):
            return   # nothing left (sets that are too young to read are waited for a while)
        if not did and not busy:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
