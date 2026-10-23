"""Predict the pictures of a folder with the defect model and save the pictures with the boxes (a test on this PC).

    python backend/predict/run_folder.py <folder> [--out DIR] [--conf 0.25] [--imgsz 1280] [--device auto] [--limit N]

The folder is only READ (sub-folders too, so a captures day folder works). Results go to --out (default
exports/predict/<folder name>_<time>, which git ignores): the pictures with boxes, results.json, results.csv."""

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # run as a plain file too

import cv2  # noqa: E402

from backend.predict import engine  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="a folder of pictures (or one picture)")
    ap.add_argument("--out", help="where to write the results (default exports/predict/<name>_<time>)")
    ap.add_argument("--model", help=f"model file (default {engine.DEFAULT_MODEL.relative_to(engine.ROOT)})")
    ap.add_argument("--conf", type=float, default=engine.CONF, help="minimum score of a detection")
    ap.add_argument("--imgsz", type=int, default=engine.IMGSZ, help="input size (the model was trained at 1280)")
    ap.add_argument("--device", default="auto", help="auto | cpu | mps | 0 (first NVIDIA GPU)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N pictures")
    ap.add_argument("--no-images", action="store_true", help="write only results.json / results.csv")
    args = ap.parse_args()

    out = Path(args.out) if args.out else engine.ROOT / "exports" / "predict" / (Path(args.folder).name + time.strftime("_%Y%m%d_%H%M%S"))
    out_abs = out.resolve()
    # the results may be written inside the folder that is read (--out): never read them again as input pictures
    files = [f for f in engine.find_images(args.folder) if out_abs not in f.resolve().parents]
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no pictures under {args.folder}")
    out.mkdir(parents=True, exist_ok=True)

    model, device = engine.load(args.model, args.device)
    print(f"{len(files)} pictures, device {device}, imgsz {args.imgsz}, conf {args.conf} -> {out}")
    engine.predict(model, device, cv2.imread(str(files[0])), args.imgsz, args.conf)   # the first run is slower: not counted

    rows, counts, total_ms = [], Counter(), 0.0
    base = Path(args.folder) if Path(args.folder).is_dir() else Path(args.folder).parent
    for i, path in enumerate(files, 1):
        image = cv2.imread(str(path))
        if image is None:
            print(f"  [{i}/{len(files)}] {path.name}: cannot be read, skipped")
            continue
        res = engine.predict(model, device, image, args.imgsz, args.conf)
        total_ms += res["ms"]
        classes = [d["class"] for d in res["detections"]]
        counts.update(classes)
        rel = path.relative_to(base).as_posix()
        rows.append({"file": rel, "ms": res["ms"], "detections": res["detections"]})
        print(f"  [{i}/{len(files)}] {rel}: {res['ms']:.0f} ms, " + (", ".join(f"{d['class']} {d['conf']:.2f}" for d in res["detections"]) or "no defect found"))
        if not args.no_images:
            target = out / "images" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(target.with_suffix(".jpg")), engine.draw(image, res["detections"]))

    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump({"device": device, "imgsz": args.imgsz, "conf": args.conf, "pictures": rows}, f, ensure_ascii=False, indent=1)
    with open(out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "ms", "detections", "classes"])
        for r in rows:
            w.writerow([r["file"], r["ms"], len(r["detections"]), " / ".join(f"{d['class']}:{d['conf']}" for d in r["detections"])])
    print(f"\n{len(rows)} pictures, {total_ms / max(len(rows), 1):.0f} ms per picture on average, "
          f"{sum(1 for r in rows if r['detections'])} with at least one detection")
    for name, n in counts.most_common():
        print(f"  {name}: {n}")
    print(f"results: {out}")


if __name__ == "__main__":
    main()
