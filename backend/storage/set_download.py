"""Download shoes (all their sets: pictures + the data saved with them) from the Gallery as one zip.

    shoes_<shoe QR | N shoes>_<yyyymmdd_hhmmss>.zip
        manifest.json                            what is in it (shoes, sets, frames, pictures, what was missing)
        <shoe>/<set>/<id>.json                   the data of one frame as it is saved (camera settings, QR, label ...)
        <shoe>/<set>/<id>_<camera>.jpg           the pictures of that frame, as saved (full size)

<shoe> is the shoe's QR value as it goes in file names (no-id = no QR value was read), <set> the id of the shoe's pass
(the first frame of it, with the QR value in front). Asked for by shoe (the QR values, optionally one day), by some
sets of one shoe (sets_<shoe>_<N>-sets_<time>.zip) or by frame ids. The zip is written to a temporary file (never held in memory) and removed when the answer is sent."""

import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime

from ..core.config import DOWNLOAD_MAX_MB
from .paths import ID_RE, image_path, primary_role, qr_prefix
from .store import store

MAX_IDS = 600      # frames per download when asked for by frame ids (they travel in the address)
MAX_FRAMES = 20000  # frames per download when asked for by shoe (the size limit below is the real one)
MAX_SHOES = 500    # shoes per download (their QR values travel in the address)
MAX_SETS = 500     # sets per download when asked for by set (their ids travel in the address)
SET_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")   # a burst id (20261105_124441_836-t60) or a frame id
MB = 1024 * 1024


class DownloadError(Exception):
    """A request the zip cannot be made for; the text is shown to the user. `code` is the HTTP status."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def collect(ids):
    """[(id, meta, set name, [(camera, path, size)])] for the frames that exist; the ids that do not: [id]."""
    found, missing = [], []
    for sample_id in dict.fromkeys(ids):   # no duplicates, in the order asked for
        meta = store.get(sample_id)
        if meta is None:
            missing.append(sample_id)
            continue
        primary = primary_role(meta)
        views = list(meta.get("views") or [primary])
        files = []
        for role in views:
            path = image_path(sample_id, role, primary=role == primary)
            try:
                files.append((role, path, path.stat().st_size))
            except OSError:
                pass
        set_name = (meta.get("burst") or {}).get("id") or sample_id
        found.append((sample_id, meta, set_name if ID_RE.match(set_name) else sample_id, files))
    return found, missing


def frame_ids(shoes, date=None):
    """The frame ids of the sets of these shoes (QR values; None = no QR value was read), only the day `date` when given."""
    if not shoes or len(shoes) > MAX_SHOES or not all(s is None or isinstance(s, str) for s in shoes):
        raise DownloadError(f"shoes: 1 to {MAX_SHOES} shoes")
    ids = [m["id"] for shoe in dict.fromkeys(shoes) for m in store.by_shoe(shoe, date)]
    if not ids:
        raise DownloadError("these shoes have no sets (any more)" + (f" on {date}" if date else ""), 404)
    return ids


def set_frame_ids(shoe, sets, date=None):
    """The frame ids of some sets of one shoe (QR value; None = no QR value was read). A set is named by its id as the
    Gallery groups it: the burst id of its frames, or the frame's own id for a manual capture."""
    keys = set(sets or [])
    if not keys or len(keys) > MAX_SETS or not all(isinstance(k, str) and SET_KEY_RE.match(k) for k in keys):
        raise DownloadError(f"sets: 1 to {MAX_SETS} set ids")
    ids = [m["id"] for m in store.by_shoe(shoe, date) if ((m.get("burst") or {}).get("id") or m["id"]) in keys]
    if not ids:
        raise DownloadError("these sets do not exist (any more)", 404)
    return ids


def build(ids, limit=MAX_IDS, kind="shoes"):
    """The zip of the frames `ids` as (open temporary file at the start, file name, size). The caller closes the file.
    kind "sets": the name says how many sets it holds (sets_<shoe>_<N>-sets_<time>.zip)."""
    if not ids or len(ids) > limit or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
        raise DownloadError(f"ids: 1 to {limit} frame ids")
    found, missing = collect(ids)
    if not found:
        raise DownloadError("these sets do not exist (any more)", 404)
    total = sum(size for _, _, _, files in found for _, _, size in files)
    if DOWNLOAD_MAX_MB and total > DOWNLOAD_MAX_MB * MB:
        raise DownloadError(f"{total / MB:.0f} MB is more than the limit of {DOWNLOAD_MAX_MB} MB per download: "
                            f"pick fewer {'sets' if kind == 'sets' else 'shoes'} (ON_DOWNLOAD_MAX_MB changes the limit)", 413)
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < total * 1.05 + 200 * MB:
        raise DownloadError(f"not enough free disk space for the zip ({total / MB:.0f} MB needed, {free / MB:.0f} MB free)", 507)

    folders = {}   # shoe QR value -> its folder name in the zip (two values that clean to the same name get a number)
    for _, meta, _, _ in found:
        shoe = meta.get("shoe_id")
        if shoe not in folders:
            base = qr_prefix(shoe) or "no-id"
            name, n = base, 1
            while name in folders.values():
                n += 1
                name = f"{base}_{n}"
            folders[shoe] = name
    tree = {}   # shoe -> set name -> frames
    for sample_id, meta, set_name, files in found:
        tree.setdefault(meta.get("shoe_id"), {}).setdefault(set_name, []).append(
            {"id": sample_id, "timestamp": meta.get("timestamp"), "cameras": [role for role, _, _ in files]})
    manifest = {"created": datetime.now().isoformat(timespec="seconds"),
                "shoes": [{"shoe_id": shoe, "folder": folders[shoe], "sets": [{"set": n, "frames": f} for n, f in sets.items()]}
                          for shoe, sets in tree.items()],
                "pictures": sum(len(files) for _, _, _, files in found), "bytes": total, "not_found": missing}

    tmp = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:   # JPEG does not shrink: no compression
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for sample_id, meta, set_name, files in found:
                zf.writestr(f"{folders[meta.get('shoe_id')]}/{set_name}/{sample_id}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                for role, path, _ in files:
                    try:
                        zf.write(path, f"{folders[meta.get('shoe_id')]}/{set_name}/{sample_id}_{role}.jpg")
                    except OSError:   # deleted while the zip was made: left out
                        pass
        size = tmp.tell()
        tmp.seek(0)
    except BaseException:
        tmp.close()
        raise
    label = next(iter(folders.values())) if len(folders) == 1 else f"{len(folders)}-shoes"
    if kind == "sets":
        n_sets = sum(len(sets) for sets in tree.values())
        label = f"{label}_{n_sets}-{'set' if n_sets == 1 else 'sets'}"
    name = f"{kind}_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return tmp, name, size
