"""Captured-sample storage: JPEG + thumbnail + sidecar JSON holding detection info and the label.

A sample is a "set": one view per camera taken at the same moment.
    .sets/<id>.json          metadata and label of the set ("primary_view" = the reference camera);
                             this is what the app lists and edits
    <role>/<id>.jpg + .json  one folder per camera, e.g. top/<id>.jpg, back/<id>.jpg; the JSON next
                             to an image is a copy of that camera's part (see _view_meta)
                             (thumbnails mirror this in .thumbs/<role>/<id>.jpg)
<id> is "<QR>_<yyyymmdd_hhmmss_mmm>": the shoe's QR value in front of the capture time (just the time
when no code was read). The shots of one shoe pass are saved together and share the QR value.
Older sets kept <id>.json, <id>.jpg (primary view) and <id>__<role>.jpg (other views) directly in the
captures folder: the JSON is moved into .sets/ at start, the images are still read where they are.
Samples without "views" in their JSON (single-camera captures) are a set with one view.

Encoding and disk writes happen on a worker thread so the camera loops never stall."""

import json
import queue
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from ..core import config as C
from ..core.config import CAPTURES_DIR, JPEG_QUALITY, SETS_DIR, THUMB_WIDTH, THUMBS_DIR, TRIGGER_CAMERA
from ..core.events import broadcast
from ..detect.background import get_background
from ..qr.decode import read_qr
from ..qr.hub import hub as qr_hub
from . import capture_log
from .meta import (LABEL_STATUSES, bbox_from_background, is_valid_category, label_status, make_bbox, save_meta)
from .paths import (image_path, meta_path, migrate_set_files, primary_image_path, primary_role, qr_prefix,
                    thumb_path, time_key, view_meta_path)
from .shoe_id import qr_sizes, resolve_shoe
from .shoes import ShoeIndex

SAVE_WORKERS = 3


class SampleStore:
    def __init__(self):
        migrate_set_files()
        # one item per capture (the sets of a shoe pass together), each may hold many full-size frames
        self._queue = queue.Queue(maxsize=4)
        self._id_lock = threading.Lock()
        self._label_lock = threading.Lock()
        self._reserved = set()
        self._shoes = ShoeIndex(self.get)
        # several writers: one that waits for a late QR read must not hold up the next shoe
        for n in range(SAVE_WORKERS):
            threading.Thread(target=self._worker, name=f"sample-writer-{n}", daemon=True).start()

    # ---- saving -------------------------------------------------------------

    def _new_id(self):
        """Unique base id (the capture time); ids still waiting in the write queue count as taken,
        and so do saved ones with a QR value in front of them."""
        with self._id_lock:
            base = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            sample_id, n = base, 1
            while (sample_id in self._reserved or meta_path(sample_id).exists()
                   or any(SETS_DIR.glob(f"*_{sample_id}.json"))):
                sample_id = f"{base}-{n}"
                n += 1
            self._reserved.add(sample_id)
            return sample_id

    def enqueue(self, views, meta):
        """Queue one set for saving. Returns its base id (the capture time), or None if the writer is
        backed up. The saved id has the shoe's QR value in front when one was read (see enqueue_burst)."""
        ids = self.enqueue_burst([(views, meta)])
        return ids[0] if ids else None

    def enqueue_burst(self, sets, qr_window=None):
        """Queue the sets of one capture, [(views, meta), ...], to be saved together: the shoe's QR value
        is looked for in ALL their pictures first and put in front of every file name, so the shots of a
        shoe pass are named alike even when the code is visible in only one of them.
        qr_window = (since, until) in epoch seconds: live QR reads in it count for this capture, and the
        writer waits (at most until `until`) for one if nothing else gave a code. Default: the last
        ON_QR_LIVE_WINDOW_SEC seconds, no waiting (manual captures).
        Returns the base ids, or [] if the writer is backed up (nothing is saved then).

        views: one BGR image, or {role: image or frame object with getCvFrame()}. The first entry
        is the primary view (trigger camera). Conversion and encoding happen on the writer thread.
        meta["views"] may carry per-view info (offset_ms, camera, ...); size/file are added here."""
        items = []
        for views, meta in sets:
            if not isinstance(views, dict):
                views = {TRIGGER_CAMERA: views}
            if not views:
                continue
            sample_id = self._new_id()
            meta = dict(meta)
            meta["id"] = sample_id
            meta["timestamp"] = datetime.now().isoformat(timespec="milliseconds")
            meta["primary_view"] = next(iter(views))
            meta["label"] = None
            items.append((sample_id, dict(views), meta))
        if not items:
            return []
        now = time.time()
        window = qr_window or (now - C.QR_LIVE_WINDOW_SEC, now)
        try:
            self._queue.put_nowait((items, now, window))
        except queue.Full:
            print("Sample writer backed up - capture dropped")
            capture_log.event("DROPPED", items[0][2].get("source") or "?",
                              f"cuts={len(items)} reason=\"the writer is backed up\"")
            for sample_id, _, _ in items:
                self._reserved.discard(sample_id)
            return []
        return [sample_id for sample_id, _, _ in items]

    @staticmethod
    def _write_view(sample_id, role, img):
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        for root in (CAPTURES_DIR, THUMBS_DIR):
            (root / role).mkdir(parents=True, exist_ok=True)
        (CAPTURES_DIR / role / f"{sample_id}.jpg").write_bytes(buf.tobytes())
        h, w = img.shape[:2]
        thumb = cv2.resize(img, (THUMB_WIDTH, max(1, int(h * THUMB_WIDTH / w))),
                           interpolation=cv2.INTER_AREA)
        ok, tbuf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            (THUMBS_DIR / role / f"{sample_id}.jpg").write_bytes(tbuf.tobytes())

    def _worker(self):
        while True:
            sets, enqueued, window = self._queue.get()
            try:
                self._save_job(sets, enqueued, window)
            except Exception as e:   # the writer must survive whatever one capture does
                print(f"Sample save failed: {e}")
            finally:
                for sample_id, _, _ in sets:
                    self._reserved.discard(sample_id)

    def _save_job(self, sets, enqueued, window):
        """Save the sets of one capture: pictures and QR codes first, then the shoe's QR value is
        found for the whole capture (see _resolve_shoe), then every set is written under a name that
        starts with it."""
        prepared, results = [], []
        for sample_id, views, meta in sets:
            try:
                prepared.append(self._prepare(sample_id, views, meta))
            except Exception as e:
                print(f"Sample save failed ({sample_id}): {e}")
                results.append({"id": sample_id, "saved": [], "missing": {}, "error": str(e)})
        shoe = resolve_shoe(prepared, enqueued, window)
        for base_id, meta, images, codes in prepared:
            results.append(self._write_set(base_id, meta, images, codes, shoe))
        try:   # the capture log: what was saved, per camera, and how much there is now
            first = sets[0][2]
            capture_log.job(first.get("source") or "?", results, shoe, [r for r, _ in C.CAMERAS],
                            time.time() - enqueued, self.totals(), burst=(first.get("burst") or {}).get("id"))
        except Exception as e:  # logging must never get in the way of saving
            print(f"[capture log] failed: {e}")

    @staticmethod
    def _prepare(sample_id, views, meta):
        """Pictures of one set as images, and the QR codes in them. Returns (id, meta, images, codes)."""
        primary = meta["primary_view"]
        info = meta.get("views") or {}
        saved, missing, images = {}, dict(meta.get("missing_views") or {}), {}
        for role, item in views.items():
            try:
                img = item if isinstance(item, np.ndarray) else item.getCvFrame()
            except Exception as e:
                if role == primary:
                    raise
                missing[role] = f"save failed: {e}"
                continue
            saved[role] = {**info.get(role, {}), "width": img.shape[1], "height": img.shape[0]}
            images[role] = img
        meta["views"] = saved
        if missing:
            meta["missing_views"] = missing
        primary_img = images[primary]
        meta["image"] = {"width": primary_img.shape[1], "height": primary_img.shape[0]}
        codes = []
        if C.QR_ENABLED:
            try:
                seen = {}
                codes, _ = read_qr(images, seen)
                for role, sizes in seen.items():   # located but not read: how big it was in the picture
                    saved[role]["qr_seen_px"] = round(max(sizes))
            except Exception as e:  # QR is best effort: never lose the images for it
                print(f"QR decode failed ({sample_id}): {e}")
        return sample_id, meta, images, codes

    def _write_set(self, base_id, meta, images, codes, shoe):
        """Write one prepared set under its final id: the shoe's QR value + the capture time."""
        shoe_id, source, others = shoe
        prefix = qr_prefix(shoe_id)
        sample_id = f"{prefix}_{base_id}" if prefix else base_id
        meta["id"] = sample_id
        meta["shoe_id"], meta["shoe_id_source"] = shoe_id, source
        if others:
            meta["shoe_id_other"] = others
        primary = meta["primary_view"]
        result = {"id": sample_id, "saved": [], "missing": {}, "error": None}
        try:
            for role in list(images):
                try:
                    self._write_view(sample_id, role, images[role])
                except Exception as e:
                    if role == primary:
                        raise
                    meta["views"].pop(role, None)
                    meta.setdefault("missing_views", {})[role] = f"save failed: {e}"
                    del images[role]
            if meta.get("bbox") is None and get_background(primary).exists:
                meta["bbox"] = bbox_from_background(images[primary], primary)
            if C.QR_ENABLED:
                meta["qr"] = [c for c in codes if c["view"] in images]
                for text in dict.fromkeys(c["text"] for c in meta["qr"]):
                    first = next(c["view"] for c in meta["qr"] if c["text"] == text)
                    qr_hub.add(first, text, "capture", sample_id)
            save_meta(meta)
            print(f"Saved sample {sample_id} ({meta.get('source')}, {len(meta['views'])} view(s)"
                  + (f", shoe {shoe_id!r} [{source}]" if shoe_id else "") + ")")
            broadcast({"key": "samples_changed", "new": sample_id})
            result["saved"] = list(meta["views"])
            result["missing"] = dict(meta.get("missing_views") or {})
            result["qr_px"] = qr_sizes(meta)
        except Exception as e:
            print(f"Sample save failed ({sample_id}): {e}")
            self._remove_files(sample_id, list(images))
            result["error"] = str(e)
        return result

    @staticmethod
    def _remove_files(sample_id, roles):
        paths = [meta_path(sample_id), *(view_meta_path(sample_id, role) for role in roles)]
        for root in (CAPTURES_DIR, THUMBS_DIR):
            paths += [root / role / f"{sample_id}.jpg" for role in roles]
            paths += [root / f"{sample_id}.jpg", *root.glob(f"{sample_id}__*.jpg")]   # old layout
        for p in paths:
            try:
                p.unlink()
            except OSError:
                pass

    # ---- reading ------------------------------------------------------------

    def get(self, sample_id):
        try:
            return json.loads(meta_path(sample_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def view_file(self, sample_id, view, thumb=False):
        """Path of a requested view's image (empty view = the primary view), or None if unknown."""
        meta = self.get(sample_id)
        if meta is None:
            return None
        primary = primary_role(meta)
        role = view or primary
        if role != primary and role not in (meta.get("views") or {}):
            return None
        return (thumb_path if thumb else image_path)(sample_id, role, primary=role == primary)

    def list(self, status="all", limit=0):
        """Newest first. status: all | unlabeled | ok | ng | reject. limit > 0 stops after that many
        (the live page only needs the latest few and must not read every sample file)."""
        items = []
        for p in sorted(SETS_DIR.glob("*.json"), key=lambda p: (time_key(p.stem), p.stem), reverse=True):
            meta = self.get(p.stem)
            if meta is None or "id" not in meta or not primary_image_path(meta).exists():
                continue
            if status != "all" and label_status(meta) != status:
                continue
            items.append(meta)
            if limit and len(items) >= limit:
                break
        return items

    # ---- by shoe (the browse page) -------------------------------------------

    def totals(self):
        return self._shoes.totals()

    def shoes(self):
        return self._shoes.shoes()

    def by_shoe(self, shoe):
        return self._shoes.by_shoe(shoe)

    def counts(self):
        counts = {"all": 0, "passes": 0, "unlabeled": 0, "ok": 0, "ng": 0, "reject": 0,
                  "with_bbox": 0, "bbox_reviewed": 0}
        passes = set()
        for meta in self.list():
            passes.add((meta.get("burst") or {}).get("id") or meta["id"])
            counts["all"] += 1
            counts[label_status(meta)] += 1
            bbox = meta.get("bbox")
            if bbox:
                counts["with_bbox"] += 1
                counts["bbox_reviewed"] += bool(bbox.get("reviewed"))
        counts["passes"] = len(passes)
        return counts

    # ---- mutating -----------------------------------------------------------

    def burst_ids(self, sample_id):
        """Ids of the samples taken for the same shoe pass (before/center/after), including this one."""
        meta = self.get(sample_id)
        burst = (meta or {}).get("burst") or {}
        if not burst.get("id"):
            return [sample_id] if meta else []
        return [m["id"] for m in self.list() if (m.get("burst") or {}).get("id") == burst["id"]]

    def set_label(self, sample_id, status, defects, note, views=None, apply_burst=False):
        """Validate and store a label. Returns (meta, error_message).
        views: for NG, the cameras in which the defect is visible (optional).
        apply_burst: also label the other shots of the same shoe pass."""
        if status not in LABEL_STATUSES:
            return None, "invalid status"
        defects = [d for d in (defects or []) if is_valid_category(d)] if status == "ng" else []
        if status == "ng" and not defects:
            return None, "defect type required for NG"
        targets = self.burst_ids(sample_id) if apply_burst else [sample_id]
        if sample_id not in targets:
            targets = [sample_id]
        result = None
        with self._label_lock:
            for target in targets:
                meta = self.get(target)
                if meta is None:
                    if target == sample_id:
                        return None, "not found"
                    continue
                known = set(meta.get("views") or [meta.get("primary_view", TRIGGER_CAMERA)])
                seen_in = [v for v in dict.fromkeys(views or []) if v in known] if status == "ng" else []
                meta["label"] = {
                    "status": status,
                    "defects": defects,
                    "views": seen_in,
                    "note": str(note or "")[:500],
                    "labeled_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_meta(meta)
                if target == sample_id:
                    result = meta
        broadcast({"key": "samples_changed"})
        return result, None

    def _update_bbox(self, sample_id, fn):
        """Apply fn(meta) -> error message or None, then persist. Returns (meta, error)."""
        with self._label_lock:
            meta = self.get(sample_id)
            if meta is None:
                return None, "not found"
            err = fn(meta)
            if err:
                return None, err
            save_meta(meta)
        broadcast({"key": "samples_changed"})
        return meta, None

    def set_bbox(self, sample_id, x, y, w, h):
        """Human-drawn bbox (normalized). Counts as reviewed."""
        try:
            x0, y0 = max(0.0, float(x)), max(0.0, float(y))
            x1, y1 = min(1.0, x0 + float(w)), min(1.0, y0 + float(h))
        except (TypeError, ValueError):
            return None, "invalid bbox"
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            return None, "bbox too small"

        def apply(meta):
            meta["bbox"] = make_bbox(x0, y0, x1, y1, source="manual", reviewed=True)
        return self._update_bbox(sample_id, apply)

    def confirm_bbox(self, sample_id):
        def apply(meta):
            if not meta.get("bbox"):
                return "no bbox"
            meta["bbox"]["reviewed"] = True
        return self._update_bbox(sample_id, apply)

    def clear_bbox(self, sample_id):
        def apply(meta):
            meta["bbox"] = None
        return self._update_bbox(sample_id, apply)

    @staticmethod
    def _read_image(meta):
        try:
            return cv2.imdecode(np.fromfile(str(primary_image_path(meta)), dtype=np.uint8),
                                cv2.IMREAD_COLOR)
        except OSError:
            return None

    def auto_bbox(self, sample_id):
        """(Re)generate the bbox draft from the background reference of the sample's primary camera.
        Returns (meta, error)."""
        meta = self.get(sample_id)
        if meta is None:
            return None, "not found"
        role = primary_role(meta)
        if not get_background(role).exists:
            return None, "no background"
        img = self._read_image(meta)
        if img is None:
            return None, "not found"
        bbox = bbox_from_background(img, role)

        def apply(m):
            m["bbox"] = bbox
        return self._update_bbox(sample_id, apply)

    def auto_bbox_all(self, scope="unreviewed"):
        """Regenerate drafts for every sample whose bbox a human has not reviewed."""
        done = found = no_background = 0
        for meta in self.list():
            bbox = meta.get("bbox")
            if scope == "unreviewed" and bbox and bbox.get("reviewed"):
                continue
            result, err = self.auto_bbox(meta["id"])
            if err == "no background":
                no_background += 1
            elif result is not None:
                done += 1
                found += bool(result.get("bbox"))
        if done == 0 and no_background:
            return None, "no background"
        return {"processed": done, "with_bbox": found, "skipped_no_background": no_background}, None

    def redecode_qr(self, sample_id):
        """Read the QR code again from the saved images (all views). Returns (meta, error)."""
        meta = self.get(sample_id)
        if meta is None:
            return None, "not found"
        images = {}
        primary = primary_role(meta)
        for role in (meta.get("views") or {primary: {}}):
            try:
                path = image_path(sample_id, role, primary=role == primary)
                img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            except OSError:
                img = None
            if img is not None:
                images[role] = img
        codes, texts = read_qr(images)

        def apply(m):
            m["qr"] = codes
        result, err = self._update_bbox(sample_id, apply)
        for text in texts:
            first = next(c["view"] for c in codes if c["text"] == text)
            qr_hub.add(first, text, "capture", sample_id)
        return result, err

    def clear_label(self, sample_id):
        with self._label_lock:
            meta = self.get(sample_id)
            if meta is None:
                return False
            meta["label"] = None
            save_meta(meta)
        broadcast({"key": "samples_changed"})
        return True

    def delete(self, sample_id):
        meta = self.get(sample_id)
        if meta is None:
            return False
        self._remove_files(sample_id, list(meta.get("views") or [primary_role(meta)]))
        broadcast({"key": "samples_changed"})
        return True


store = SampleStore()
