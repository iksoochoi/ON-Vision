"""Keep ONE picture per camera of every shoe pass of a day, delete the other two (a stop-gap for days that were saved
with three cuts per camera: ON_PICK_MODE=classic). Meant for a few days of data, not for every day.

    ./bin/thin_captures.sh --date 2026-10-17 --dry-run     what would be kept and deleted (changes nothing)
    ./bin/thin_captures.sh --date 2026-10-17               do it (asks first; --yes skips the question)
    ./bin/thin_captures.sh --date 2026-10-17 --mode center keep the whole center cut, delete before / after
    ./bin/thin_captures.sh --date 2026-10-17 --captures /path/to/unzipped/captures

A shoe pass was saved as three sets (cuts: before / center / after), each with one picture per camera. Per camera
the picture to keep is chosen like this (--mode best, the default):
  1. a cut in which that camera's picture gave a QR code beats one that did not,
  2. else the sharpest (the sharpness measured when it was saved), but a cut that is not the center cut must be
     3% sharper than the center cut to be taken instead of it,
  3. else the center cut.
--mode center keeps every camera's picture of the center cut (nothing is mixed).
The kept pictures end up in ONE set per pass, under the id of the center cut (the id has the shoe's QR value in front
of it): a picture of another cut is moved to that id and the set's JSON files are rewritten (burst count 1, which cut
each picture came from, its offset to the center moment). The two other sets are deleted with all their files, and the
defect detection results of the pass (captures/.predict/) too: they are not valid any more (the predict worker
makes new ones).

What it never touches: other days, manual captures, passes that are not complete (all cuts there), passes that are
already thin (burst count 1), sets saved in the last 60 s. It checks that a backup zip of the day exists
(capture_<date>*.zip in ON_BACKUP_DIR; --no-zip-check skips that) because the deleted pictures cannot be restored.
The files of a pass are changed one pass at a time: if it stops, the passes done are done and the others untouched."""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SETTLE_SEC = 60
MIN_GAIN = 0.03   # a cut that is not the center cut must be this much sharper than the center cut to be kept instead of it
ROLES_ORDER = ("top", "back", "left", "front", "right")


# ---- the layout of a captures folder (as backend/storage/paths.py) ------------------------------------------------------

class Layout:
    def __init__(self, root):
        self.root = Path(root)

    def set_json(self, sid):
        return self.root / ".sets" / f"{sid}.json"

    def predict(self, sid):
        return self.root / ".predict" / f"{sid}.json"

    def image(self, role, sid):
        return self.root / role / f"{sid}.jpg"

    def view_json(self, role, sid):
        return self.root / role / f"{sid}.json"

    def thumb(self, role, sid):
        return self.root / ".thumbs" / role / f"{sid}.jpg"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def view_meta(meta, role):
    """The copy of a set's JSON stored next to one camera's image (as backend/storage/meta.py view_meta)."""
    primary = meta.get("primary_view", "top")
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


def size_of(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def fmt_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    v, i = float(n), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.0f} {units[i]}" if v >= 100 or i == 0 else f"{v:.1f} {units[i]}"


def log(message):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


# ---- finding the passes -------------------------------------------------------------------------------------------------

def load_cuts(lay, day, now=None):
    """The sets of the day that belong to a burst: {burst id: [cut dict]}, and a list of (what, why) that are skipped."""
    now = now or time.time()
    bursts, skipped = defaultdict(list), []
    sets_dir = lay.root / ".sets"
    for p in sorted(sets_dir.glob("*.json")) if sets_dir.is_dir() else []:
        try:
            meta = read_json(p)
        except (OSError, ValueError):
            skipped.append((p.stem, "set file cannot be read"))
            continue
        if (meta.get("timestamp") or "")[:10] != day:
            continue
        burst = meta.get("burst")
        if not burst or not burst.get("id"):
            skipped.append((p.stem, "no burst (a manual capture)"))
            continue
        if now - p.stat().st_mtime < SETTLE_SEC:
            skipped.append((p.stem, "saved in the last 60 s"))
            continue
        bursts[burst["id"]].append({"id": meta["id"], "meta": meta, "burst": burst})
    return bursts, skipped


def present_roles(lay, cut):
    return [r for r in (cut["meta"].get("views") or {}) if lay.image(r, cut["id"]).exists()]


def qr_in(cut, role):
    return any(c.get("view") == role for c in cut["meta"].get("qr") or [])


def choose(lay, cuts, center, mode):
    """{role: the cut whose picture of that camera is kept}."""
    roles = sorted({r for c in cuts for r in present_roles(lay, c)},
                   key=lambda r: ROLES_ORDER.index(r) if r in ROLES_ORDER else 99)
    out = {}
    for role in roles:
        have = [c for c in cuts if role in present_roles(lay, c)]
        if mode == "center" or not have:
            out[role] = center if center in have else (have[0] if have else center)
            continue

        def sharp(c):
            return ((c["meta"].get("views") or {}).get(role) or {}).get("sharpness") or 0
        read = [c for c in have if qr_in(c, role)]
        pool = read or have   # 1. a cut whose picture of this camera gave a QR code beats the others
        best = max(pool, key=lambda c: (sharp(c), -abs(c["burst"].get("index", 0) - center["burst"].get("index", 0))))
        # 2. the sharpest, but a cut further from the center moment must be clearly sharper than the center cut
        out[role] = center if (center in pool and sharp(best) < sharp(center) * (1 + MIN_GAIN)) else best
    return out


def plan(lay, day, mode, now=None):
    """The work: [{"burst", "cuts", "center", "keep": {role: cut}, "delete_cuts": [cut]}] and the skipped ones."""
    bursts, skipped = load_cuts(lay, day, now)
    work = []
    for bid, cuts in sorted(bursts.items()):
        cuts.sort(key=lambda c: c["burst"].get("index", 0))
        count = max(c["burst"].get("count", len(cuts)) for c in cuts)
        if count <= 1:
            skipped.append((bid, "already one cut per pass"))
            continue
        if len(cuts) != count:
            skipped.append((bid, f"incomplete: {len(cuts)} of {count} cuts are there"))
            continue
        center = next((c for c in cuts if c["burst"].get("kind") == "center"), cuts[len(cuts) // 2])
        keep = choose(lay, cuts, center, mode)
        work.append({"burst": bid, "cuts": cuts, "center": center, "keep": keep,
                     "delete_cuts": [c for c in cuts if c is not center]})
    return work, skipped


def files_of_cut(lay, cut):
    """Every file that belongs to a cut: pictures, thumbnails, per-camera JSON, defect result, set JSON (last)."""
    sid = cut["id"]
    roles = list(cut["meta"].get("views") or [])
    out = []
    for role in roles:
        out += [lay.image(role, sid), lay.thumb(role, sid), lay.view_json(role, sid)]
    out += [lay.predict(sid), lay.set_json(sid)]
    return [p for p in out if p.exists()]


def summary(lay, work):
    """(pictures kept, pictures deleted, bytes freed, moved pictures) of the plan."""
    kept = deleted = moved = freed = 0
    for w in work:
        center = w["center"]
        kept += len(w["keep"])
        for role, cut in w["keep"].items():
            if cut is not center:
                moved += 1
        # what goes: every file of the other cuts, except the pictures that are moved to the center
        keep_files = set()
        for role, cut in w["keep"].items():
            if cut is not center:
                keep_files |= {lay.image(role, cut["id"]), lay.thumb(role, cut["id"])}
        for cut in w["delete_cuts"]:
            for p in files_of_cut(lay, cut):
                if p in keep_files:
                    continue
                freed += size_of(p)
                deleted += 1 if p.suffix == ".jpg" and ".thumbs" not in p.parts else 0
        for role, cut in w["keep"].items():   # the center's own picture that a moved one replaces
            if cut is not center and lay.image(role, center["id"]).exists():
                freed += size_of(lay.image(role, center["id"])) + size_of(lay.thumb(role, center["id"]))
                deleted += 1
        freed += size_of(lay.predict(center["id"]))
    return kept, deleted, freed, moved


# ---- doing it -------------------------------------------------------------------------------------------------------------

def apply_pass(lay, w):
    """Change one pass: the kept pictures into the center set, then the other cuts away."""
    center = w["center"]
    cid = center["id"]
    meta = center["meta"]
    meta.setdefault("views", {})
    picked_from = {}
    # 1. the pictures of other cuts move to the center's name (this replaces the center's own picture of that camera)
    for role, cut in w["keep"].items():
        if cut is center:
            continue
        for src, dst in ((lay.image(role, cut["id"]), lay.image(role, cid)), (lay.thumb(role, cut["id"]), lay.thumb(role, cid))):
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
        src_view = dict((cut["meta"].get("views") or {}).get(role) or {})
        # the moment of that picture to the center moment: its own offset to its cut plus the shift of the cut
        shift = (cut["burst"].get("adjacent") or {}).get("offset_ms") or 0
        if "offset_ms" in src_view:
            src_view["offset_ms"] = round(src_view["offset_ms"] + shift, 1)
        meta["views"][role] = src_view
        (meta.get("missing_views") or {}).pop(role, None)
        picked_from[role] = cut["burst"].get("kind") or f"cut {cut['burst'].get('index')}"
    # 2. the set follows its pictures: QR codes of the kept pictures, the reference camera's data, the burst
    primary = meta.get("primary_view", "top")
    codes = []
    for role, cut in w["keep"].items():
        codes += [c for c in cut["meta"].get("qr") or [] if c.get("view") == role]
    meta["qr"] = codes
    if not meta.get("shoe_id"):
        donor = next((c for c in w["cuts"] if c["meta"].get("shoe_id")), None)
        if donor:
            meta["shoe_id"], meta["shoe_id_source"] = donor["meta"]["shoe_id"], donor["meta"].get("shoe_id_source")
    if w["keep"].get(primary) is not None and w["keep"][primary] is not center:
        src = w["keep"][primary]["meta"]
        for key in ("detection", "bbox", "image"):   # these belong to the reference camera's picture
            if key in src:
                meta[key] = src[key]
        for key in ("camera", "ae", "sensor_resolution"):
            if key in meta["views"].get(primary, {}):
                meta[key] = meta["views"][primary][key]
    meta["burst"] = {"id": w["burst"], "kind": "center", "index": 0, "count": 1}
    meta["thinned"] = {"from": picked_from, "mode": w["mode"], "at": datetime.now().isoformat(timespec="seconds")}
    for role in list(meta["views"]):
        if lay.image(role, cid).exists():
            write_json(lay.view_json(role, cid), view_meta(meta, role))
    write_json(lay.set_json(cid), meta)   # the set file last: a listed set is complete
    # 3. the defect results of the pass are not valid any more
    lay.predict(cid).unlink(missing_ok=True)
    # 4. the other cuts go: the set file first (so no set is listed without its pictures), then the rest
    for cut in w["delete_cuts"]:
        lay.set_json(cut["id"]).unlink(missing_ok=True)
        for p in files_of_cut(lay, cut):
            try:
                p.unlink()
            except OSError as e:
                log(f"  could not delete {p}: {e}")


def find_zip(day, backup_dir):
    return sorted(Path(backup_dir).glob(f"capture_{day}*.zip")) if Path(backup_dir).is_dir() else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="the day, 2026-10-17")
    ap.add_argument("--captures", help="the captures folder (default: ON_CAPTURES_DIR of the project, e.g. an unzipped backup)")
    ap.add_argument("--mode", choices=("best", "center"), default="best", help="best: per camera the best of its cuts; center: the center cut")
    ap.add_argument("--dry-run", action="store_true", help="only say what would be done")
    ap.add_argument("--yes", action="store_true", help="do not ask first")
    ap.add_argument("--no-zip-check", action="store_true", help="do not require a backup zip of the day")
    ap.add_argument("--backup-dir", help="where the backup zip is looked for (default: ON_BACKUP_DIR)")
    args = ap.parse_args(argv)

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        sys.exit("--date must look like 2026-10-17")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.core import config as C
    root = Path(args.captures) if args.captures else C.CAPTURES_DIR
    lay = Layout(root)
    if not (root / ".sets").is_dir():
        sys.exit(f"{root} has no .sets folder: not a captures folder")

    work, skipped = plan(lay, args.date, args.mode)
    for w in work:
        w["mode"] = args.mode
    kept, deleted, freed, moved = summary(lay, work)
    log(f"{root}: {args.date}, mode {args.mode}")
    log(f"{len(work)} passes to thin: {kept} pictures stay ({moved} of them are moved from another cut), "
        f"{deleted} pictures go, {fmt_bytes(freed)} freed")
    for w in work[:3]:
        origin = ", ".join(f"{r}:{(c['burst'].get('kind') or c['burst'].get('index'))}" for r, c in w["keep"].items())
        log(f"  e.g. {w['center']['id']}: keeps {origin}")
    if skipped:
        reasons = defaultdict(int)
        for _, why in skipped:
            reasons[why.split(":")[0]] += 1
        log("left alone: " + "; ".join(f"{n} x {why}" for why, n in reasons.items()))
    if not work:
        log("nothing to do")
        return 0
    zips = find_zip(args.date, args.backup_dir or C.BACKUP_DIR)
    if zips:
        log(f"backup zip found: {zips[-1]} ({fmt_bytes(size_of(zips[-1]))})")
    elif not args.no_zip_check:
        log(f"STOP: no backup zip capture_{args.date}*.zip in {args.backup_dir or C.BACKUP_DIR}: make one first "
            f"(./bin/capture_backup.sh --date {args.date} --keep) or use --no-zip-check")
        return 2
    if args.dry_run:
        log("dry run: nothing was changed")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            log("no terminal to ask on: add --yes")
            return 4
        if input("This deletes pictures that cannot be restored (the backup zip can). Type 'thin' to go on: ").strip() != "thin":
            log("cancelled, nothing was changed")
            return 4
    for n, w in enumerate(work, 1):
        apply_pass(lay, w)
        if n % 50 == 0:
            log(f"  {n}/{len(work)} passes done")
    log(f"done: {len(work)} passes, {fmt_bytes(freed)} freed. The app shows it within two minutes; the predict worker "
        f"makes new results for these sets (only sets younger than ON_PREDICT_MAX_AGE_HOURS, else run it with --backfill).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
