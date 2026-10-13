"""Export labeled samples as an Ultralytics-style YOLO detection dataset (single class: shoe).

    exports/shoe_<timestamp>/
        images/{train,val}/<id>.jpg
        labels/{train,val}/<id>.txt      "0 cx cy w h" (normalized), empty file = negative image
        data.yaml
        manifest.json
    exports/shoe_<timestamp>.zip

Positives: samples with a bbox (human-reviewed only if requested). Samples labeled "reject"
(false detections, no shoe in view) become negative images with an empty label file."""

import json
import re
import shutil
import zlib
from datetime import datetime

import cv2
import numpy as np

from ..core.config import EXPORT_MAX_SIDE, EXPORTS_DIR
from .paths import primary_image_path
from .store import store

ZIP_RE = re.compile(r"^shoe_\d{8}_\d{6}\.zip$")


def _write_image(meta, dest, max_side):
    """The set's reference-camera view (the bbox belongs to it)."""
    sample_id, src = meta["id"], primary_image_path(meta)
    if not max_side:
        shutil.copyfile(src, dest)
        return
    img = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError(f"encode failed: {sample_id}")
    dest.write_bytes(buf.tobytes())


def _split(sample_id, val_ratio):
    """Stable train/val assignment: the same sample always lands in the same split."""
    return "val" if zlib.crc32(sample_id.encode()) % 1000 < val_ratio * 1000 else "train"


def export_dataset(reviewed_only=False, val_ratio=0.2, include_negatives=True,
                   max_side=EXPORT_MAX_SIDE):
    """Write the dataset and return a summary dict. Raises ValueError if nothing qualifies."""
    val_ratio = min(max(float(val_ratio), 0.0), 0.5)
    items, skipped = [], {"no_bbox": 0, "unreviewed": 0, "negatives_off": 0}
    # The shots of one shoe pass look almost identical: they must land in the same split, or the
    # validation set would contain near-copies of training images.
    group_of = {}
    for meta in store.list():
        group_of[meta["id"]] = (meta.get("burst") or {}).get("id") or meta["id"]
        status = (meta.get("label") or {}).get("status")
        bbox = meta.get("bbox")
        if status == "reject":
            if include_negatives:
                items.append((meta, None))
            else:
                skipped["negatives_off"] += 1
        elif not bbox:
            skipped["no_bbox"] += 1
        elif reviewed_only and not bbox.get("reviewed"):
            skipped["unreviewed"] += 1
        else:
            items.append((meta, bbox))
    if not any(b for _, b in items):
        raise ValueError("no samples with a bbox to export")

    name = datetime.now().strftime("shoe_%Y%m%d_%H%M%S")
    root = EXPORTS_DIR / name
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)

    group_split = {g: _split(g, val_ratio) for g in {group_of[m["id"]] for m, _ in items}}
    positives = sorted({group_of[m["id"]] for m, b in items if b}, key=lambda g: zlib.crc32(g.encode()))
    if val_ratio > 0 and len(positives) >= 2 and not any(group_split[g] == "val" for g in positives):
        group_split[positives[0]] = "val"  # tiny dataset: make sure validation has at least one shoe

    counts = {"train": 0, "val": 0, "negatives": 0}
    for meta, bbox in items:
        sample_id = meta["id"]
        split = group_split[group_of[sample_id]]
        _write_image(meta, root / "images" / split / f"{sample_id}.jpg", max_side)
        line = ""
        if bbox:
            cx, cy = bbox["x"] + bbox["w"] / 2, bbox["y"] + bbox["h"] / 2
            line = f"0 {cx:.6f} {cy:.6f} {bbox['w']:.6f} {bbox['h']:.6f}\n"
        else:
            counts["negatives"] += 1
        (root / "labels" / split / f"{sample_id}.txt").write_text(line, encoding="utf-8")
        counts[split] += 1

    (root / "data.yaml").write_text(
        "# Ultralytics YOLO dataset; paths are relative to this file\n"
        "train: images/train\nval: images/val\nnc: 1\nnames:\n  0: shoe\n", encoding="utf-8")
    summary = {
        "name": name, "dir": str(root), "zip": f"{name}.zip", **counts,
        "positives": counts["train"] + counts["val"] - counts["negatives"],
        "skipped": skipped, "reviewed_only": bool(reviewed_only),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (root / "manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    shutil.make_archive(str(root), "zip", root_dir=root)
    return summary
