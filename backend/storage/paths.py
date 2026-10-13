"""Where a sample's files live and how its id is built: <QR>_<yyyymmdd_hhmmss_mmm>."""

import os
import re

from ..core.config import CAPTURES_DIR, SETS_DIR, THUMBS_DIR, TRIGGER_CAMERA

ID_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,39}_)?\d{8}_\d{6}_\d{3}(?:-\d+)?$")
_TIME_RE = re.compile(r"\d{8}_\d{6}_\d{3}(?:-\d+)?$")
QR_PREFIX_MAX = 40


def qr_prefix(text):
    """A QR value as it goes in front of file names: letters, digits, '.', '_' and '-' only (anything
    else becomes '-'; the file names and the web addresses stay plain ASCII), at most QR_PREFIX_MAX
    characters. '' when nothing usable is left (the raw value is still kept in the JSON)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned[:QR_PREFIX_MAX].strip("-._")


def time_key(sample_id):
    """The capture-time part of an id: what the sets are sorted by (the QR value in front is not)."""
    m = _TIME_RE.search(sample_id)
    return m.group(0) if m else sample_id


def _legacy_name(sample_id, role, primary):
    return f"{sample_id}.jpg" if primary else f"{sample_id}__{role}.jpg"


def _view_path(root, sample_id, role, primary):
    path = root / role / f"{sample_id}.jpg"
    if not path.exists():
        legacy = root / _legacy_name(sample_id, role, primary)
        if legacy.exists():
            return legacy
    return path


def image_path(sample_id, role, primary=False):
    """The file of one camera's view; primary=True for the set's reference view (old layout)."""
    return _view_path(CAPTURES_DIR, sample_id, role, primary)


def thumb_path(sample_id, role, primary=False):
    return _view_path(THUMBS_DIR, sample_id, role, primary)


def primary_role(meta):
    return meta.get("primary_view", TRIGGER_CAMERA)


def primary_image_path(meta):
    return image_path(meta["id"], primary_role(meta), primary=True)


def meta_path(sample_id):
    return SETS_DIR / f"{sample_id}.json"


def view_meta_path(sample_id, role):
    return CAPTURES_DIR / role / f"{sample_id}.json"


def migrate_set_files():
    """Older versions kept each set's JSON directly in captures/: move it into captures/.sets/."""
    moved = 0
    for p in CAPTURES_DIR.glob("*.json"):
        if ID_RE.match(p.stem):
            try:
                os.replace(p, SETS_DIR / p.name)
                moved += 1
            except OSError as e:
                print(f"[samples] could not move {p.name} to {SETS_DIR.name}/: {e}")
    if moved:
        print(f"[samples] moved {moved} set file(s) from {CAPTURES_DIR.name}/ to {CAPTURES_DIR.name}/{SETS_DIR.name}/")
