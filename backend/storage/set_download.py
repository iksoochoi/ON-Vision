"""Download sets (pictures + the data saved with them) from the Gallery as one zip.

    sets_<shoe QR>_<yyyymmdd_hhmmss>.zip
        manifest.json                       what is in it (shoe, sets, frames, pictures, what was missing)
        <set>/<id>.json                     the data of one frame as it is saved (camera settings, QR, label ...)
        <set>/<id>_<camera>.jpg             the pictures of that frame, as saved (full size)

<set> is the id of the shoe's pass (the first frame of it, with the QR value in front). The zip is written to a temporary
file (never held in memory) and removed when the answer is sent."""

import json
import shutil
import tempfile
import zipfile
from datetime import datetime

from ..core.config import DOWNLOAD_MAX_MB
from .paths import ID_RE, image_path, primary_role, qr_prefix
from .store import store

MAX_IDS = 600   # frames per download (the ids travel in the address)
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


def build(ids):
    """The zip of the frames `ids` as (open temporary file at the start, file name, size). The caller closes the file."""
    if not ids or len(ids) > MAX_IDS or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
        raise DownloadError(f"ids: 1 to {MAX_IDS} frame ids")
    found, missing = collect(ids)
    if not found:
        raise DownloadError("these sets do not exist (any more)", 404)
    total = sum(size for _, _, _, files in found for _, _, size in files)
    if DOWNLOAD_MAX_MB and total > DOWNLOAD_MAX_MB * MB:
        raise DownloadError(f"{total / MB:.0f} MB is more than the limit of {DOWNLOAD_MAX_MB} MB per download: "
                            "pick fewer sets (ON_DOWNLOAD_MAX_MB changes the limit)", 413)
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < total * 1.05 + 200 * MB:
        raise DownloadError(f"not enough free disk space for the zip ({total / MB:.0f} MB needed, {free / MB:.0f} MB free)", 507)

    shoe = found[0][1].get("shoe_id")
    sets = {}
    for sample_id, meta, set_name, files in found:
        sets.setdefault(set_name, []).append({"id": sample_id, "timestamp": meta.get("timestamp"),
                                              "cameras": [role for role, _, _ in files]})
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "shoe_id": shoe,
                "sets": [{"set": name, "frames": frames} for name, frames in sets.items()],
                "pictures": sum(len(files) for _, _, _, files in found), "bytes": total, "not_found": missing}

    tmp = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:   # JPEG does not shrink: no compression
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for sample_id, meta, set_name, files in found:
                zf.writestr(f"{set_name}/{sample_id}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                for role, path, _ in files:
                    try:
                        zf.write(path, f"{set_name}/{sample_id}_{role}.jpg")
                    except OSError:   # deleted while the zip was made: left out
                        pass
        size = tmp.tell()
        tmp.seek(0)
    except BaseException:
        tmp.close()
        raise
    name = f"sets_{qr_prefix(shoe) or 'no-id'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return tmp, name, size
