"""A sample's JSON: writing it (the set file and the copy next to each camera's image), the label status
and the bbox helpers."""

import json
import os

from ..core.config import CAPTURES_DIR, CATEGORIES
from ..detect.background import get_background
from .paths import meta_path, primary_role, view_meta_path

LABEL_STATUSES = ("ok", "ng", "reject")  # normal / defective / false detection (exclude)


def is_valid_category(folder):
    group, _, item = (folder or "").partition("/")
    return group in CATEGORIES and item in CATEGORIES[group]


def write_json(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def view_meta(meta, role):
    """What is stored next to one camera's image: that view's own info (size, exposure, offset to
    the trigger moment, its QR codes) plus the set's label. bbox and detection are in the reference
    camera's picture, so only that view carries them."""
    primary = primary_role(meta)
    own = (meta.get("views") or {}).get(role, {})
    out = {
        "id": meta["id"], "timestamp": meta.get("timestamp"), "source": meta.get("source"),
        "view": role, "is_primary": role == primary, "primary_view": primary,
        "set_views": list(meta.get("views") or []),
        **own,
        "qr": [c for c in meta.get("qr") or [] if c.get("view") == role],
        "shoe_id": meta.get("shoe_id"),
        "shoe_id_source": meta.get("shoe_id_source"),
        "label": meta.get("label"),
        "burst": meta.get("burst"),
    }
    if role == primary:
        out["bbox"] = meta.get("bbox")
        out["detection"] = meta.get("detection")
    return out


def save_meta(meta):
    """Write the set's JSON (captures/<id>.json, what the app lists) and a copy of each camera's
    part next to its image (captures/<role>/<id>.json). The set file goes last: a listed sample is
    always complete. Sets saved with the old layout have no camera folders and get only the set file."""
    sample_id = meta["id"]
    for role in meta.get("views") or []:
        if (CAPTURES_DIR / role / f"{sample_id}.jpg").exists():
            write_json(view_meta_path(sample_id, role), view_meta(meta, role))
    write_json(meta_path(sample_id), meta)


def label_status(meta):
    label = meta.get("label")
    return label.get("status") if label else "unlabeled"


def make_bbox(xmin, ymin, xmax, ymax, source, reviewed=False):
    """bbox as stored in the sample JSON: normalized top-left x/y plus width/height."""
    return {
        "x": round(xmin, 4), "y": round(ymin, 4),
        "w": round(xmax - xmin, 4), "h": round(ymax - ymin, 4),
        "source": source, "reviewed": reviewed,
    }


def bbox_from_background(img, role):
    """Largest foreground object vs. the empty-belt reference of that camera, or None."""
    box = get_background(role).best_box(img)
    return make_bbox(*box[:4], source="background") if box else None
