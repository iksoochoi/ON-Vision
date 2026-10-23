"""Save defect detection results as files: for every set the pictures with the boxes drawn on them and a result.json.

    exports/predict_<yyyymmdd_hhmmss>/
        manifest.json                what was saved (sets, filter, settings)
        <set id>/result.json         the verdict (model alone and corrected), per camera the detections with their box
                                     (fractions and pixels of the original picture), where they lie, whether they count
        <set id>/<camera>.jpg        the picture with the boxes (scaled down to ON_PREDICT_EXPORT_MAX_SIDE)
        <set id>/original/<camera>.jpg   only when asked: the picture as it was saved
    exports/predict_<yyyymmdd_hhmmss>.zip

With the corrections on, a box that does not count (recurring, off the shoe, a fine class) is drawn thin and grey with the
reason; the boxes that count are red. With them off every box is red (the raw result of the model)."""

import json
import re
import shutil
from datetime import datetime

import cv2
import numpy as np

from ..core.config import (EXPORTS_DIR, PREDICT_CORRECT, PREDICT_EXPORT_MAX_SETS, PREDICT_EXPORT_MAX_SIDE,
                           PREDICT_MODEL, PREDICT_ON_SHOE_MIN, PREDICT_RECUR_IOU, PREDICT_RECUR_MIN,
                           PREDICT_SHOE_MAX_AREA)
from . import predictions
from .store import store

ZIP_RE = re.compile(r"^predict_\d{8}_\d{6}\.zip$")
RED, GREEN, GREY = (40, 40, 255), (90, 200, 60), (170, 170, 170)


def read_picture(path):
    """The picture as an array (also from a path with non-ASCII characters), or None."""
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def draw(image, detections, corrected, ok_classes, max_side):
    """The picture with the boxes drawn (a copy), scaled down to max_side (0 = as it is). detections: [{"class",
    "conf", "box": fractions, "ctx": {"counted", "reasons"}}]."""
    out = image.copy()
    h, w = out.shape[:2]
    thick = max(2, round(w / 700))
    scale = max(0.7, w / 1800)
    for d in detections:
        x0, y0, x1, y1 = (int(d["box"][0] * w), int(d["box"][1] * h), int(d["box"][2] * w), int(d["box"][3] * h))
        ctx = d.get("ctx") or {}
        left = corrected and ctx.get("counted") is False
        color = GREY if left else (GREEN if d["class"] in ok_classes else RED)
        t = max(1, thick // 3) if left else thick
        label = f"{d['class']} {d['conf']:.2f}" + (" - " + ", ".join(ctx.get("reasons") or []) if left and ctx.get("reasons") else "")
        cv2.rectangle(out, (x0, y0), (x1, y1), color, t)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, t - 1))
        top = max(y0 - th - base - 4, 0)
        cv2.rectangle(out, (x0, top), (x0 + tw + 8, top + th + base + 4), color, -1)
        cv2.putText(out, label, (x0 + 4, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), max(1, t - 1), cv2.LINE_AA)
    if max_side and max(h, w) > max_side:
        k = max_side / max(h, w)
        out = cv2.resize(out, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
    return out


def _write_jpg(path, image):
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError(f"encode failed: {path}")
    path.write_bytes(buf.tobytes())


def _set_record(sample_id, corrected, originals, folder, ok_classes, max_side):
    """Write one set into folder/<id>/ and return (its summary, None), or (None, reason) when it cannot be saved."""
    result = predictions.read_result(sample_id)
    if result is None:
        return None, "no result"
    meta = store.get(sample_id) or {}
    ctx = predictions.context(result, meta)
    metrics = predictions.class_metrics()
    out_dir = folder / sample_id
    out_dir.mkdir(parents=True, exist_ok=True)
    views, missing = {}, {}
    for role, v in (result.get("views") or {}).items():
        path = store.view_file(sample_id, role)
        image = read_picture(path) if path else None
        if image is None:
            missing[role] = "picture missing"
            continue
        h, w = image.shape[:2]
        vc = ctx["views"].get(role) or {}
        flags = vc.get("detections") or []
        dets = []
        for i, d in enumerate(v.get("detections", [])):
            c = flags[i] if i < len(flags) else {}
            x0, y0, x1, y1 = d["box"]
            dets.append({"class": d["class"], "conf": d["conf"], "box": d["box"],
                         "box_px": [round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)],
                         "on_shoe": c.get("on_shoe"), "inside_shoe": c.get("inside"), "recurring": c.get("recurring"),
                         "counts": c.get("counted"), "left_out": c.get("reasons") or [],
                         "class_map50": (metrics.get(d["class"]) or {}).get("map50"), "ctx": c})
        annotated = draw(image, dets, corrected, ok_classes, max_side)
        _write_jpg(out_dir / f"{role}.jpg", annotated)
        if originals:
            (out_dir / "original").mkdir(exist_ok=True)
            shutil.copyfile(path, out_dir / "original" / f"{role}.jpg")
        for d in dets:
            d.pop("ctx", None)
        views[role] = {"image": f"{role}.jpg", "image_size": [annotated.shape[1], annotated.shape[0]], "original_size": [w, h],
                       "sharpness": vc.get("sharpness"), "predict_ms": v.get("ms"), "detections": dets}
    verdict = ctx.get("verdict") or {}
    summary = {"id": sample_id, "shoe_id": result.get("shoe_id"), "timestamp": result.get("timestamp"),
               "verdict": {"shown": "corrected" if corrected else "raw", "raw_defect": verdict.get("raw"),
                           "corrected_defect": verdict.get("corrected"), "raw_count": verdict.get("raw_count"),
                           "counted": verdict.get("count")}}
    doc = {**summary, "model": {"file": PREDICT_MODEL.name, "device": result.get("device"), "imgsz": result.get("imgsz"),
                                "conf": result.get("conf"), "predicted_at": result.get("predicted_at")},
           "views": views, "missing_pictures": missing, "ok_classes": list(ok_classes)}
    (out_dir / "result.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary, None


def export_predictions(ids, corrected=None, originals=False, source=None):
    """Save the results of these sets (at most PREDICT_EXPORT_MAX_SETS). Returns a summary dict (name, dir, zip, saved,
    skipped, capped). Raises ValueError when nothing could be saved."""
    corrected = PREDICT_CORRECT if corrected is None else bool(corrected)
    ids = list(dict.fromkeys(ids))
    capped = max(0, len(ids) - PREDICT_EXPORT_MAX_SETS)
    ids = ids[:PREDICT_EXPORT_MAX_SETS]
    predictions.index.refresh(force=True)
    name = datetime.now().strftime("predict_%Y%m%d_%H%M%S")
    folder = EXPORTS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    saved, skipped = [], {}
    for sid in ids:
        try:
            summary, why = _set_record(sid, corrected, originals, folder, predictions.PREDICT_OK_CLASSES, PREDICT_EXPORT_MAX_SIDE)
        except Exception as e:   # one set that cannot be saved must not stop the others
            summary, why = None, f"{type(e).__name__}: {e}"
        if summary:
            saved.append(summary)
        else:
            skipped[sid] = why
    if not saved:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError("nothing to save: " + (next(iter(skipped.values())) if skipped else "no sets"))
    manifest = {"name": name, "created": datetime.now().isoformat(timespec="seconds"), "sets": len(saved),
                "corrected": corrected, "originals": bool(originals), "source": source or {},
                "settings": {"recur_min": PREDICT_RECUR_MIN, "recur_iou": PREDICT_RECUR_IOU, "on_shoe_min": PREDICT_ON_SHOE_MIN,
                             "shoe_max_area": PREDICT_SHOE_MAX_AREA, "ok_classes": list(predictions.PREDICT_OK_CLASSES)},
                "skipped": skipped, "capped": capped, "items": saved}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.make_archive(str(folder), "zip", root_dir=folder)
    return {"name": name, "dir": str(folder), "zip": f"{name}.zip", "saved": len(saved), "skipped": skipped,
            "capped": capped, "corrected": corrected}
