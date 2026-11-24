"""The cam plate calibration of the bonding gap made on the site (the Predict page: "Cam plate").

The bonding gap (backend/predict/bonding_gap.py) takes its ruler from the cam plate when the QR label is not on the
shoe: a template of the plate per side camera and K per camera (mm_per_px = K / s, s = the size of the plate against
the template). The default calibration is in the repo (backend/models/bonding_gap/, made on the PC from the QC sets
with a QR on the shoe). When a camera is moved the site makes its own, by hand from the Predict page (never on its own):

  1. the app writes the request captures/.predict/.calib.json {"id", "from", "to" (yyyy-mm-dd), "requested_at"}
  2. the worker (when no new set waits) finds the plate in the left / right pictures of up to CALIB_SETS sets of that
     period with the calibration in use, cuts a new template from the best picture (the QR label on the plate painted
     over) and ESTIMATES K: K_new = K_old / s of that picture (the plate and the side of the shoe are assumed to keep
     their distance ratio to the camera: the scale goes on where it was). Then it finds the plate again with the new
     template (how many found, how far the new scale is from the old one) and writes candidate/, removes the request.
  3. the page shows the candidate; Apply makes it the one in use (active/), the one before goes to history/.

Folders (captures/.predict/bonding_gap/, not in git: a git pull never touches them): active/ (in use; none = the
default of the repo), candidate/ (made, not applied), history/<time>/ (replaced). The worker reads active/ (or the
default) every round: no restart. Results made before keep the scale they were measured with."""

import json
import os
import shutil
import time
from pathlib import Path

from ..core.config import PREDICT_DIR, PROJECT_DIR

DEFAULT_DIR = PROJECT_DIR / "backend" / "models" / "bonding_gap"
SITE_DIR = PREDICT_DIR / "bonding_gap"
ACTIVE_DIR = SITE_DIR / "active"
CANDIDATE_DIR = SITE_DIR / "candidate"
HISTORY_DIR = SITE_DIR / "history"
REQUEST_NAME = ".calib.json"
CALIB_NAME = "plate_calib.json"
CALIB_SETS = 30      # sets of the period used (spread over it)
MIN_GOOD = 5         # plates found well needed per camera
SIDES = ("left", "right")


def read_calib(folder):
    try:
        data = json.loads((Path(folder) / CALIB_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def plate_dir():
    """The folder whose calibration is in use: the site's (active/) or the default of the repo."""
    return ACTIVE_DIR if (ACTIVE_DIR / CALIB_NAME).exists() else DEFAULT_DIR


def in_use():
    """("site" | "default", the version: the time it was made / the file time) - written into every gap result."""
    d = plate_dir()
    c = read_calib(d)
    if d == ACTIVE_DIR:
        return "site", c.get("made_at") or ""
    try:
        return "default", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime((d / CALIB_NAME).stat().st_mtime))
    except OSError:
        return "default", ""


def stamp():
    """A key that changes when the calibration in use changes (the worker reloads then)."""
    d = plate_dir()
    try:
        return str(d), (d / CALIB_NAME).stat().st_mtime_ns
    except OSError:
        return str(d), None


def template_path(which, view):
    """The template picture of active / candidate / default for a camera, or None."""
    folder = {"active": ACTIVE_DIR, "candidate": CANDIDATE_DIR, "default": DEFAULT_DIR, "in_use": plate_dir()}.get(which)
    if folder is None or view not in SIDES:
        return None
    name = (read_calib(folder).get(view) or {}).get("template")
    p = folder / name if name else None
    return p if p is not None and p.is_file() and p.parent == folder else None


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_request():
    try:
        req = json.loads((PREDICT_DIR / REQUEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return req if isinstance(req, dict) and req.get("id") else None


def write_request(req):
    _write_json(PREDICT_DIR / REQUEST_NAME, req)


def clear_request(req_id=None):
    req = read_request()
    if req is None or (req_id is not None and req.get("id") != req_id):
        return False
    try:
        (PREDICT_DIR / REQUEST_NAME).unlink()
        return True
    except OSError:
        return False


def write_candidate(calib, templates):
    """The worker's result: calib (the plate_calib.json content) and {file name: PNG bytes}. Replaces a candidate."""
    tmp = SITE_DIR / "candidate.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name, data in templates.items():
        (tmp / name).write_bytes(data)
    _write_json(tmp / CALIB_NAME, calib)
    shutil.rmtree(CANDIDATE_DIR, ignore_errors=True)
    os.replace(tmp, CANDIDATE_DIR)


def _to_history(folder):
    if folder.exists():
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        dest = HISTORY_DIR / time.strftime("%Y%m%d_%H%M%S")
        n = 1
        while dest.exists():
            dest = HISTORY_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{n}"
            n += 1
        os.replace(folder, dest)


def apply_candidate():
    """The candidate becomes the calibration in use (the one before goes to history/). False when there is none."""
    if not (CANDIDATE_DIR / CALIB_NAME).exists():
        return False
    _to_history(ACTIVE_DIR)
    os.replace(CANDIDATE_DIR, ACTIVE_DIR)
    return True


def discard_candidate():
    if not CANDIDATE_DIR.exists():
        return False
    shutil.rmtree(CANDIDATE_DIR, ignore_errors=True)
    return True


def use_default():
    """Back to the calibration of the repo (the site's goes to history/). False when it is in use already."""
    if not ACTIVE_DIR.exists():
        return False
    _to_history(ACTIVE_DIR)
    return True


def history():
    return sorted((p.name for p in HISTORY_DIR.iterdir() if (p / CALIB_NAME).exists()), reverse=True) if HISTORY_DIR.exists() else []


def health(results):
    """How well the cam plate is found in the latest results (their bonding gap): {camera: {"sets", "found",
    "score": median match, "by": {"qr": n, "plate": n}}}. A camera that was moved shows fewer found / lower scores."""
    out = {}
    for v in SIDES:
        scores, found, sets, by = [], 0, 0, {}
        for r in results:
            g = (((r.get("gap") or {}).get("views") or {}).get(v)) or {}
            p = g.get("plate") or {}
            if not g:
                continue
            sets += 1
            if p.get("mm_per_px"):
                found += 1
            if p.get("score") is not None:
                scores.append(p["score"])
            if g.get("scale"):
                by[g["scale"]] = by.get(g["scale"], 0) + 1
        scores.sort()
        out[v] = {"sets": sets, "found": found, "score": scores[len(scores) // 2] if scores else None, "by": by}
    return out


def summary(calib):
    """The short form the page shows of a calibration: {camera: {"k", "ref", "min_score", "estimated", ...}}."""
    keep = ("k", "ref", "min_score", "estimated", "from_k", "s_ref", "ang_ref", "used", "sets", "err_pct_median",
            "err_pct_p90", "check", "error", "kept")
    out = {v: {k: c.get(k) for k in keep if k in c} for v, c in calib.items() if v in SIDES and isinstance(c, dict)}
    for k in ("made_at", "from", "to", "estimated", "base"):
        if k in calib:
            out[k] = calib[k]
    return out
