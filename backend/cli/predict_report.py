"""Defect predictions of the saved sets of some days, with the defect model ALONE, to hand to the customer for feedback.

    python -m backend.cli.predict_report --from 2026-11-02 --to 2026-11-04
    python -m backend.cli.predict_report --from 2026-11-02 --to 2026-11-04 --captures /path/to/captures --zip
    python -m backend.cli.predict_report --from 2026-11-02 --to 2026-11-04 --dry-run      # only count: sets, pictures, size

What it does NOT do (on purpose: the raw output of the model): no shoe segmentation / background removal (the picture is
predicted as it was saved) and none of the corrections of the Predict page (recurring boxes, off-shoe boxes, fine classes
are not left out). A set is "defect" when any picture has a box of a class that is not an ok class (ON_PREDICT_OK_CLASSES).

The captures folder is only READ. The predictions of the worker (captures/.predict) are not touched either: the results
go to --out only (default exports/predict_raw_<from>_<to>, which git ignores):

    <out>/README.txt                what the folder contains
    <out>/manifest.json             range, settings (model, conf, imgsz ...), counts
    <out>/summary.csv               one row per set: verdict, number of boxes, classes
    <out>/detections.csv            one row per box: set, camera, class, score, box in pixels
    <out>/<set id>/<camera>.jpg     the picture with the boxes drawn (long side at most --max-side)
    <out>/<set id>/result.json      the boxes of the set (fractions and pixels of the saved picture)
    <out>/<set id>/original/<camera>.jpg   only with --originals: the picture as it was saved

--dry-run predicts nothing and writes nothing: it counts the sets and pictures of the range, adds up the size of the saved
pictures, and estimates the size of the result folder (the pictures with boxes are measured on a few samples).

A run that stopped can be started again with the same command: the sets already done are skipped (as long as the
model / --conf / --imgsz are the same; --redo does everything again). Needs backend/predict/requirements.txt."""

import argparse
import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # also runnable as a plain file

README = """Defect detection results (raw model output)

Model: {model} (conf >= {conf}, imgsz {imgsz}), predicted {created}.
Sets: {first} ~ {last}, {sets} sets, {pictures} pictures.
Not used: shoe segmentation / background removal, and the corrections of the Predict page. Every picture was given to the
defect model as it was saved, and every box it found is listed.

summary.csv        one row per set: verdict (DEFECT = a box of a class that is not an ok class, OK, ERROR), boxes, classes
detections.csv     one row per box: set, camera, class, score, box (pixels of the saved picture)
<set id>/<camera>.jpg   the picture with the boxes (red = defect class, green = ok class: {ok})
<set id>/result.json    the same boxes for the set, as fractions and pixels
Set ids look like <shoe QR value>_<date>_<time>_<ms>; a set without a QR value is only <date>_<time>_<ms>.
"""


def parse_day(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"{text}: a day as YYYY-MM-DD")


def log(message):
    print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


def fmt_bytes(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def dry_run(args, ids, out, C, image_path, primary_role, read_picture, time_key):
    """Count what a run would do (no model, nothing written): sets, pictures, the size of the saved pictures and an estimate of
    the result folder. The pictures with boxes are estimated from a few samples: scaled to --max-side and encoded like the real
    ones (the boxes add little)."""
    import cv2
    per_day, pictures, missing, total_bytes, files, done = {}, 0, 0, 0, [], 0
    for sid in ids:
        day = time_key(sid)[:8]
        per_day.setdefault(day, [0, 0, 0])[0] += 1
        if (out / sid / "result.json").exists() and not args.redo:
            done += 1
        try:
            meta = json.loads((C.SETS_DIR / f"{sid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing += 1
            continue
        primary = primary_role(meta)
        for role in list(meta.get("views") or [primary]):
            path = image_path(sid, role, primary=role == primary)
            try:
                size = path.stat().st_size
            except OSError:
                missing += 1
                continue
            pictures += 1
            total_bytes += size
            per_day[day][1] += 1
            per_day[day][2] += size
            files.append((path, size))
    sample_in = sample_out = 0
    for path, size in files[::max(1, len(files) // 12)][:12]:   # about 12 pictures spread over the range
        image = read_picture(path)
        if image is None:
            continue
        if args.max_side and max(image.shape[:2]) > args.max_side:
            k = args.max_side / max(image.shape[:2])
            image = cv2.resize(image, (round(image.shape[1] * k), round(image.shape[0] * k)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            sample_in += size
            sample_out += len(buf)
    ratio = sample_out / sample_in if sample_in else 1.0
    boxes_bytes = round(total_bytes * ratio)
    originals_bytes = total_bytes if args.originals else 0
    small = len(ids) * 6 * 1024   # result.json per set, the csv files
    estimate = boxes_bytes + originals_bytes + small
    print(f"dry run (nothing is predicted or written): {C.CAPTURES_DIR}")
    print(f"  range     {args.first} ~ {args.last or args.first}" + ("  (only sets with a QR value)" if args.qr_only else "  (all sets, with and without QR value)"))
    for day, (n_sets, n_pics, n_bytes) in sorted(per_day.items()):
        print(f"  {day[:4]}-{day[4:6]}-{day[6:]}  {n_sets:5d} sets  {n_pics:6d} pictures  {fmt_bytes(n_bytes):>10}")
    print(f"  total     {len(ids)} sets, {pictures} pictures, {fmt_bytes(total_bytes)} as saved"
          + (f" ({missing} pictures or set files missing)" if missing else ""))
    size_text = f"long side at most {args.max_side} px" if args.max_side else "full size"
    print(f"  result    pictures with boxes ({size_text}): about {fmt_bytes(boxes_bytes)}"
          + (f" ({ratio * 100:.0f}% of the saved size, measured on {min(12, len(files))} pictures)" if sample_in else ""))
    if args.originals:
        print(f"            + the saved pictures (--originals): {fmt_bytes(originals_bytes)}")
    print(f"            + result.json / csv: about {fmt_bytes(small)}")
    print(f"            = about {fmt_bytes(estimate)} in {out}" + ("; the zip is about as large again while it is made" if args.zip else ""))
    if done:
        print(f"  already in {out.name}: {done} of {len(ids)} sets (they are skipped; --redo does them again)")
    try:
        probe = out if out.exists() else out.parent
        while not probe.exists():
            probe = probe.parent
        print(f"  free disk space at {probe}: {fmt_bytes(shutil.disk_usage(probe).free)}")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="first", required=True, help="first day, YYYY-MM-DD (by the time in the set id)")
    ap.add_argument("--to", dest="last", help="last day (included), YYYY-MM-DD (default: the first day)")
    ap.add_argument("--captures", help="the captures folder (default ON_CAPTURES_DIR / captures); only read")
    ap.add_argument("--out", help="where to write (default exports/predict_raw_<from>_<to>)")
    ap.add_argument("--qr-only", action="store_true", help="only the sets whose shoe QR value was read (default: all sets)")
    ap.add_argument("--conf", type=float, help="minimum score of a box (default ON_PREDICT_CONF, 0.25)")
    ap.add_argument("--imgsz", type=int, help="model input size (default ON_PREDICT_IMGSZ, 1280)")
    ap.add_argument("--device", help="auto | cpu | mps | 0 (default ON_PREDICT_DEVICE)")
    ap.add_argument("--max-side", type=int, default=2000,
                    help="long side of the pictures with boxes in pixels (default 2000; larger ones are scaled down, JPEG quality 92); "
                         "0 = as saved (full size)")
    ap.add_argument("--originals", action="store_true", help="also copy the pictures as they were saved")
    ap.add_argument("--zip", action="store_true", help="also make <out>.zip")
    ap.add_argument("--limit", type=int, default=0, help="only the first N sets (a test)")
    ap.add_argument("--dry-run", action="store_true", help="only count the sets, pictures and their size; predict and write nothing")
    ap.add_argument("--redo", action="store_true", help="do the sets that are already in --out again")
    args = ap.parse_args()

    first = parse_day(args.first)
    last = parse_day(args.last or args.first)
    if first > last:
        raise SystemExit("--from is after --to")
    if args.captures:   # the settings are read when backend.core.config is imported: set it before
        captures = Path(args.captures).expanduser().resolve()
        if not (captures / ".sets").is_dir():
            raise SystemExit(f"{captures} has no .sets folder: it is not a captures folder")
        os.environ["ON_CAPTURES_DIR"] = str(captures)

    from backend.core import config as C
    from backend.storage import predictions
    from backend.storage.paths import ID_RE, has_qr_prefix, image_path, primary_role, time_key
    from backend.storage.predict_draw import draw, read_picture, write_jpg

    conf = C.PREDICT_CONF if args.conf is None else args.conf
    imgsz = C.PREDICT_IMGSZ if args.imgsz is None else args.imgsz
    ok_classes = list(C.PREDICT_OK_CLASSES)
    ids = sorted((e.name[:-5] for e in os.scandir(C.SETS_DIR) if e.name.endswith(".json") and ID_RE.match(e.name[:-5])),
                 key=time_key)
    ids = [i for i in ids if first <= time_key(i)[:8] <= last and (has_qr_prefix(i) or not args.qr_only)]
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        raise SystemExit(f"no sets from {args.first} to {args.last or args.first} in {C.CAPTURES_DIR}")
    out = Path(args.out).expanduser() if args.out else C.EXPORTS_DIR / f"predict_raw_{first}_{last}"
    if args.dry_run:
        dry_run(args, ids, out, C, image_path, primary_role, read_picture, time_key)
        return
    out.mkdir(parents=True, exist_ok=True)

    settings = {"model": C.PREDICT_MODEL.name, "model_size": C.PREDICT_MODEL.stat().st_size, "conf": conf, "imgsz": imgsz,
                "segmentation": False, "corrections": False, "ok_classes": ok_classes}
    old = out / "manifest.json"
    if old.exists() and not args.redo:
        try:
            before = json.loads(old.read_text(encoding="utf-8")).get("settings")
        except (OSError, ValueError):
            before = None
        if before is not None and before != settings:
            raise SystemExit(f"{out} was made with other settings ({before}): use --redo, or another --out")

    import numpy as np
    import torch
    from backend.predict import engine
    torch.set_num_threads(C.PREDICT_THREADS)
    model, device = engine.load(C.PREDICT_MODEL, args.device or C.PREDICT_DEVICE)
    engine.predict(model, device, np.zeros((64, 64, 3), np.uint8), imgsz, conf)   # warm-up
    log(f"{len(ids)} sets of {args.first} ~ {args.last or args.first} from {C.CAPTURES_DIR}: model {C.PREDICT_MODEL.name} on {device}, "
        f"imgsz {imgsz}, conf {conf}, no segmentation, no corrections -> {out}")

    rows, boxes, done, skipped_sets, started = [], [], 0, 0, time.time()
    for n, sid in enumerate(ids, 1):
        folder = out / sid
        doc = None
        if (folder / "result.json").exists() and not args.redo:
            try:
                doc = json.loads((folder / "result.json").read_text(encoding="utf-8"))
                skipped_sets += 1
            except (OSError, ValueError):
                doc = None
        if doc is None:
            try:
                meta = json.loads((C.SETS_DIR / f"{sid}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log(f"{sid}: set file cannot be read ({e}): left out")
                continue
            primary = primary_role(meta)
            folder.mkdir(parents=True, exist_ok=True)
            views, errors = {}, {}
            for role in list(meta.get("views") or [primary]):
                path = image_path(sid, role, primary=role == primary)
                image = read_picture(path) if path.exists() else None
                if image is None:
                    errors[role] = "picture missing" if not path.exists() else "picture cannot be read"
                    continue
                h, w = image.shape[:2]
                res = engine.predict(model, device, image, imgsz, conf)
                dets = [{"class": d["class"], "conf": d["conf"],
                         "box": [round(d["box"][0] / w, 4), round(d["box"][1] / h, 4), round(d["box"][2] / w, 4), round(d["box"][3] / h, 4)],
                         "box_px": [round(v) for v in d["box"]]} for d in res["detections"]]
                write_jpg(folder / f"{role}.jpg", draw(image, dets, False, ok_classes, args.max_side))
                if args.originals:
                    (folder / "original").mkdir(exist_ok=True)
                    shutil.copyfile(path, folder / "original" / f"{role}.jpg")
                views[role] = {"image": f"{role}.jpg", "original_size": [w, h], "predict_ms": res["ms"], "detections": dets}
            all_dets = [d for v in views.values() for d in v["detections"]]
            doc = {"id": sid, "shoe_id": meta.get("shoe_id"), "timestamp": meta.get("timestamp"),
                   "defect": predictions.is_defect(all_dets, ok_classes) if views else None, "count": len(all_dets),
                   "views": views, "errors": errors}
            (folder / "result.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
            done += 1
            if done % 10 == 0 or n == len(ids):
                rate = (time.time() - started) / done
                log(f"{n}/{len(ids)} sets, about {rate:.1f} s per set, {rate * (len(ids) - n) / 60:.0f} min left")
        classes = {}
        for role, v in doc["views"].items():
            for d in v["detections"]:
                classes[d["class"]] = classes.get(d["class"], 0) + 1
                boxes.append({"set": sid, "camera": role, "class": d["class"], "score": d["conf"],
                              "x0": d["box_px"][0], "y0": d["box_px"][1], "x1": d["box_px"][2], "y1": d["box_px"][3]})
        verdict = "ERROR" if doc["defect"] is None else ("DEFECT" if doc["defect"] else "OK")
        rows.append({"set": sid, "shoe_id": doc.get("shoe_id") or "", "time": doc.get("timestamp") or "", "verdict": verdict,
                     "boxes": doc["count"], "classes": "; ".join(f"{k} x{v}" for k, v in sorted(classes.items())),
                     "cameras": ",".join(doc["views"]), "missing": ",".join(doc.get("errors") or {})})

    if not rows:
        raise SystemExit("no set could be read")
    for name, data, fields in (("summary.csv", rows, ["set", "shoe_id", "time", "verdict", "boxes", "classes", "cameras", "missing"]),
                               ("detections.csv", boxes, ["set", "camera", "class", "score", "x0", "y0", "x1", "y1"])):
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel reads it right
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
    pictures = sum(len(r["cameras"].split(",")) for r in rows if r["cameras"])
    created = datetime.now().isoformat(timespec="seconds")
    verdicts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("DEFECT", "OK", "ERROR")}
    (out / "manifest.json").write_text(json.dumps({
        "created": created, "from": args.first, "to": args.last or args.first, "captures": str(C.CAPTURES_DIR),
        "qr_only": args.qr_only, "sets": len(rows), "pictures": pictures, "boxes": len(boxes), "verdicts": verdicts,
        "settings": settings, "device": device, "max_side": args.max_side, "originals": args.originals}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    times = sorted(r["time"] for r in rows if r["time"])
    (out / "README.txt").write_text(README.format(model=C.PREDICT_MODEL.name, conf=conf, imgsz=imgsz, created=created,
                                                  first=times[0][:10] if times else "-", last=times[-1][:10] if times else "-",
                                                  sets=len(rows), pictures=pictures, ok=", ".join(ok_classes) or "-"), encoding="utf-8")
    if args.zip:
        log("making the zip ...")
        shutil.make_archive(str(out), "zip", root_dir=out)
    log(f"done: {len(rows)} sets ({verdicts['DEFECT']} defect, {verdicts['OK']} ok, {verdicts['ERROR']} error), {len(boxes)} boxes, "
        f"{skipped_sets} sets were already in {out.name} -> {out}" + (f" and {out}.zip" if args.zip else ""))


if __name__ == "__main__":
    main()
