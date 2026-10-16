"""The store's index: a short summary of every set file kept in memory, and everything the web pages ask for
(the shoe list, the counts, the recent sets, the QR statistics) worked out from it.

The pages must stay fast however many sets pile up, so nothing here reads the disk per request:
  * the summaries are built once at start (a background thread; a request waits for it),
  * then the store tells the index about every set it writes, changes or deletes (update / remove),
  * and a slow background pass (RECONCILE_SEC) catches what happened behind the app's back (files deleted or
    edited by hand),
  * every answer is worked out once per change of the index (its `version`) and reused until the next change."""

import os
import threading
import time

from ..core.config import CAPTURES_DIR, SETS_DIR
from .meta import label_status
from .paths import image_path, primary_role, thumb_path, time_key

RECONCILE_SEC = 120.0
READY_TIMEOUT_SEC = 120.0   # a request waits this long for the first scan
NOT_ASKED = object()   # shoes_page(include=...): no shoe was asked for (None is the shoe without a QR value)
SIZE_EDGES = (60, 80, 100, 130, 170)   # QR size classes in pixels: <60, 60-79, 80-99, 100-129, 130-169, 170+
SIZE_LABELS = ["<60", "60-79", "80-99", "100-129", "130-169", "170+"]


def _size_class(px):
    return sum(1 for edge in SIZE_EDGES if px >= edge)


def _day(info):
    """The capture date of a set, "2026-10-15" (from its time stamp; from its id when it has none)."""
    if info["time"][:10]:
        return info["time"][:10]
    tk = info["tk"]
    return f"{tk[:4]}-{tk[4:6]}-{tk[6:8]}" if len(tk) >= 8 and tk[:8].isdigit() else "unknown"


def _file_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return None


class ShoeIndex:
    def __init__(self, get, on_external_change=None):
        self._get = get   # SampleStore.get: a set's JSON by id
        self._lock = threading.RLock()
        self._sets = {}        # set file stem -> summary
        self._mtime = {}       # stem -> modification time of the set file when it was summarised
        self.version = 0       # +1 whenever anything changed
        self._cache = {}       # name -> value, valid for the current version
        self._ready = threading.Event()
        self._on_external_change = on_external_change   # called after the background pass found changes
        threading.Thread(target=self._run, name="shoe-index", daemon=True).start()

    # ---- building the summaries -------------------------------------------------------------------------------

    @staticmethod
    def _summary(meta):
        burst = meta.get("burst") or {}
        sid = meta["id"]
        primary = primary_role(meta)
        roles = list(meta.get("views") or [primary])
        present, size = [], 0
        for role in roles:   # which pictures exist and how big the set is on the disk (pictures + thumbnails)
            is_primary = role == primary
            s = _file_size(image_path(sid, role, primary=is_primary))
            if s is None:
                continue
            present.append(role)
            size += s + (_file_size(thumb_path(sid, role, primary=is_primary)) or 0)
        read = [c["size_px"] for c in meta.get("qr") or [] if c.get("size_px")]
        best = max((c for c in meta.get("qr") or [] if c.get("size_px")), key=lambda c: c["size_px"], default=None)
        seen = [v["qr_seen_px"] for v in (meta.get("views") or {}).values()
                if isinstance(v, dict) and v.get("qr_seen_px")]
        bbox = meta.get("bbox")
        return {"id": sid, "tk": time_key(sid), "shoe_id": meta.get("shoe_id") or None,
                "time": meta.get("timestamp") or "", "burst": burst.get("id"), "kind": burst.get("kind"),
                "primary": primary, "views": roles, "present": present, "bytes": size,
                "status": label_status(meta), "bbox": 1 if bbox else 0,
                "reviewed": 1 if bbox and bbox.get("reviewed") else 0,
                "source": meta.get("shoe_id_source"),   # where the QR value came from (capture, live, ...)
                "method": (best or {}).get("method"),   # how that code was read (decode.py / enhance.py)
                "read_px": max(read) if read else None,   # size of the biggest code that was read in a picture
                "seen_px": max(seen) if seen else None}   # size of a code that was located but not read

    def _load(self, stem):
        """Summarise one set file. Returns (summary, mtime) or (None, None) when it is not a usable set."""
        try:
            mtime = (SETS_DIR / f"{stem}.json").stat().st_mtime_ns
        except OSError:
            return None, None
        meta = self._get(stem)
        if meta is None or "id" not in meta:
            return None, None
        try:
            return self._summary(meta), mtime
        except Exception as e:  # one odd file must not stop the index
            print(f"[index] could not read {stem}: {e}")
            return None, None

    def _bump(self):
        self.version += 1
        self._cache.clear()

    def update(self, stem):
        """The set `stem` was written or changed: summarise it again."""
        summary, mtime = self._load(stem)
        with self._lock:
            if summary is None:
                changed = self._sets.pop(stem, None) is not None
                self._mtime.pop(stem, None)
            else:
                self._sets[stem], self._mtime[stem], changed = summary, mtime, True
            if changed:
                self._bump()

    def remove(self, stems):
        """The sets were deleted."""
        with self._lock:
            gone = [s for s in stems if self._sets.pop(s, None) is not None]
            for s in gone:
                self._mtime.pop(s, None)
            if gone:
                self._bump()

    # ---- the background thread ---------------------------------------------------------------------------------

    def _run(self):
        try:
            self._scan()
        except Exception as e:
            print(f"[index] first scan failed: {e}")
        finally:
            self._ready.set()
        while True:
            time.sleep(RECONCILE_SEC)
            try:
                if self._scan() and self._on_external_change:
                    self._on_external_change()
            except Exception as e:
                print(f"[index] check failed: {e}")

    def _scan(self):
        """Bring the summaries in line with the disk: sets added, changed (by their file time) or gone, and pictures
        that were deleted. Returns True when something was different."""
        try:
            with os.scandir(SETS_DIR) as it:
                files = {e.name[:-5]: e.stat().st_mtime_ns for e in it if e.name.endswith(".json")}
        except OSError:
            files = {}
        with self._lock:
            known = dict(self._mtime)
            summaries = dict(self._sets)
        gone = [s for s in known if s not in files]
        stale = [s for s, m in files.items() if known.get(s) != m]
        listings = {}   # role -> names of the pictures in its folder
        for role in {r for info in summaries.values() for r in info["views"]}:
            try:
                with os.scandir(CAPTURES_DIR / role) as it:
                    listings[role] = {e.name for e in it}
            except OSError:
                listings[role] = set()
        stale_set = set(stale)
        for stem, info in summaries.items():   # a picture deleted by hand (the old layout has no folder: stat it)
            if stem not in files or stem in stale_set:
                continue
            for role in info["views"]:
                here = f"{stem}.jpg" in listings[role] or image_path(stem, role, primary=role == info["primary"]).exists()
                if here != (role in info["present"]):
                    stale.append(stem)
                    break
        changed = bool(gone or stale)
        for stem in stale:
            self.update(stem)
        if gone:
            self.remove(gone)
        return changed

    def wait_ready(self):
        self._ready.wait(READY_TIMEOUT_SEC)

    # ---- helpers for the answers --------------------------------------------------------------------------------

    def _cached(self, name, build):
        """build() runs once per version of the index; the lock is held, so the summaries do not change meanwhile."""
        self.wait_ready()
        with self._lock:
            if name not in self._cache:
                self._cache[name] = build()
            return self._cache[name]

    @staticmethod
    def _listed(info):
        """Is the set shown at all? (Its pictures were deleted: only the set file is left over.)"""
        return info["primary"] in info["present"]

    def summary(self, sample_id):
        self.wait_ready()
        with self._lock:
            info = self._sets.get(sample_id)
            return dict(info) if info else None

    def _order(self):
        """The stems of the listed sets, newest first."""
        def build():
            stems = [s for s, i in self._sets.items() if self._listed(i)]
            stems.sort(key=lambda s: (self._sets[s]["tk"], s), reverse=True)
            return stems
        return self._cached("order", build)

    # ---- the answers ---------------------------------------------------------------------------------------------

    def counts(self):
        """What is saved now: {"all" (one per cut), "passes", "unlabeled", "ok", "ng", "reject", "with_bbox",
        "bbox_reviewed"}."""
        def build():
            counts = {"all": 0, "passes": 0, "unlabeled": 0, "ok": 0, "ng": 0, "reject": 0,
                      "with_bbox": 0, "bbox_reviewed": 0}
            passes = set()
            for info in self._sets.values():
                if not self._listed(info):
                    continue
                passes.add(info["burst"] or info["id"])
                counts["all"] += 1
                counts[info["status"]] = counts.get(info["status"], 0) + 1
                counts["with_bbox"] += info["bbox"]
                counts["bbox_reviewed"] += info["reviewed"]
            counts["passes"] = len(passes)
            return counts
        return dict(self._cached("counts", build))

    def totals(self, wait=True):
        """What is saved now: {"sets", "cuts", "images", "shoes", "no_id_sets"}. A set is one shoe pass, a cut
        one moment of it, shoes = different QR values, no_id_sets = the sets without a QR value.
        wait=False (the capture log): zeros while the first scan is still running, a saving never waits for it."""
        if not wait and not self._ready.is_set():
            return {"sets": 0, "cuts": 0, "images": 0, "shoes": 0, "no_id_sets": 0}
        def build():
            shoes = self.shoes()
            return {"sets": sum(s["sets"] for s in shoes), "cuts": sum(s["cuts"] for s in shoes),
                    "images": sum(s["images"] for s in shoes),
                    "shoes": sum(1 for s in shoes if s["shoe_id"] is not None),
                    "no_id_sets": sum(s["sets"] for s in shoes if s["shoe_id"] is None)}
        return dict(self._cached("totals", build))

    def shoes(self, date=None):
        """Every shoe (QR value) that has sets, most recently captured first; the pictures without a QR
        value are listed last as shoe_id None. A "set" is one shoe pass (before / center / after of every
        camera), a "cut" is one moment of it:
        [{"shoe_id", "sets", "cuts", "images", "bytes", "first", "last", "thumb": {"id", "view"}}].
        date ("2026-10-15"): only what was captured that day (the numbers count that day only)."""
        def build():
            groups = {}
            for info in self._sets.values():
                if not self._listed(info) or (date and _day(info) != date):
                    continue
                g = groups.setdefault(info["shoe_id"], {"shoe_id": info["shoe_id"], "passes": set(), "cuts": 0,
                                                         "images": 0, "bytes": 0, "first": info["time"],
                                                         "last": info["time"], "centre": None, "newest": None})
                g["passes"].add(info["burst"] or info["id"])
                g["cuts"] += 1
                g["images"] += len(info["present"])
                g["bytes"] += info["bytes"]
                g["first"], g["last"] = min(g["first"], info["time"]), max(g["last"], info["time"])
                if g["newest"] is None or info["time"] > g["newest"]["time"]:
                    g["newest"] = info
                if info["kind"] == "center" and (g["centre"] is None or info["time"] > g["centre"]["time"]):
                    g["centre"] = info
            out = []
            for g in groups.values():
                pick = g["centre"] or g["newest"]
                out.append({"shoe_id": g["shoe_id"], "sets": len(g["passes"]), "cuts": g["cuts"],
                            "images": g["images"], "bytes": g["bytes"], "first": g["first"], "last": g["last"],
                            "thumb": {"id": pick["id"], "view": pick["primary"]}})
            out.sort(key=lambda s: s["last"], reverse=True)
            out.sort(key=lambda s: s["shoe_id"] is None)   # stable: the pictures without a QR value come last
            return out
        return list(self._cached(f"shoes:{date or ''}", build))

    def dates(self):
        """The days that have sets, newest first, for the browse page's date list:
        [{"date", "sets" (shoe passes), "shoes" (different QR values), "images", "bytes"}]."""
        def build():
            days = {}
            for info in self._sets.values():
                if not self._listed(info):
                    continue
                d = days.setdefault(_day(info), {"date": _day(info), "passes": set(), "shoes": set(), "images": 0, "bytes": 0})
                d["passes"].add(info["burst"] or info["id"])
                if info["shoe_id"] is not None:
                    d["shoes"].add(info["shoe_id"])
                d["images"] += len(info["present"])
                d["bytes"] += info["bytes"]
            return [{"date": d["date"], "sets": len(d["passes"]), "shoes": len(d["shoes"]), "images": d["images"],
                     "bytes": d["bytes"]} for d in sorted(days.values(), key=lambda d: d["date"], reverse=True)]
        return list(self._cached("dates", build))

    def shoes_page(self, limit=0, query="", include=NOT_ASKED, date=None):
        """The shoe list for the browse page without sending all of it: the shoes whose QR value contains `query`,
        the first `limit` of them (0 = all), plus the shoe `include` (a QR value, None = the sets without one) when it
        exists: the one on screen. date: only that day's.
        Returns (shoes, totals) with totals = {"shoes", "sets", "images", "bytes", "matches"} of the WHOLE list."""
        every = self.shoes(date)
        needle = (query or "").strip().lower()

        def name(s):
            return "no id (qr not read)" if s["shoe_id"] is None else s["shoe_id"].lower()
        wanted = [s for s in every if not needle or needle in name(s)]
        page = wanted[:limit] if limit else wanted
        if include is not NOT_ASKED and all(s["shoe_id"] != include for s in page):
            page = page + [s for s in every if s["shoe_id"] == include][:1]
        totals = {"shoes": sum(1 for s in every if s["shoe_id"] is not None),
                  "sets": sum(s["sets"] for s in every), "images": sum(s["images"] for s in every),
                  "bytes": sum(s["bytes"] for s in every), "matches": len(wanted)}
        return page, totals

    def by_shoe(self, shoe, date=None):
        """The sets (one per cut) of one shoe, newest first, in full. shoe None = no QR value was read.
        date: only the sets of that day."""
        self.wait_ready()
        with self._lock:
            infos = [dict(i) for i in self._sets.values()
                     if i["shoe_id"] == shoe and self._listed(i) and (not date or _day(i) == date)]
        infos.sort(key=lambda i: (i["tk"], i["id"]), reverse=True)
        out = []
        for info in infos:
            meta = self._get(info["id"])
            if meta is None:
                continue
            if meta.get("views"):
                meta["views"] = {r: v for r, v in meta["views"].items() if r in info["present"]}
            out.append(meta)
        return out

    def recent(self, status="all", limit=0, offset=0):
        """The sets' JSON, newest first. status: all | unlabeled | ok | ng | reject. Only the sets that are
        returned are read from the disk."""
        stems = self._order()
        with self._lock:
            wanted = [s for s in stems if s in self._sets and (status == "all" or self._sets[s]["status"] == status)]
        if offset:
            wanted = wanted[offset:]
        out = []
        for stem in wanted:
            meta = self._get(stem)
            if meta is None or "id" not in meta:
                continue
            out.append(meta)
            if limit and len(out) >= limit:
                break
        return out

    def burst_ids(self, sample_id):
        """Ids of the sets of the same shoe pass (before / center / after), the sample itself included."""
        self.wait_ready()
        with self._lock:
            info = self._sets.get(sample_id)
            if info is None:
                return []
            if not info["burst"]:
                return [sample_id]
            return [s for s, i in self._sets.items() if i["burst"] == info["burst"] and self._listed(i)]

    def daily(self):
        """QR reading per day, counted in sets (one shoe pass each):
        {"days": [{"date": "2026-10-15", "sets": 40, "ok": 36, "fail": 4, "seen": 3, "size": bytes,
                   "hours": [{"hour": 9, "sets": .., "ok": .., "fail": .., "seen": .., "size": ..}]}],  newest day first
         "sizes": {"labels": [...], "read": [n per size class], "unread": [...], "read_no_size": n},
         "sources": {"capture": n, "live": n, ...},
         "methods": {"cv": n, "rect+zxing": n, ...},   how the codes in the saved pictures were read
         "size": bytes of everything}
        ok = the set got a QR value, fail = it did not; seen = of the failed sets, those where a QR code was located
        in a picture but could not be read (fail - seen = no code was seen at all). size = what the set's pictures
        and thumbnails take on the disk. The day and the hour (0-23, oldest first) are those of the set's first cut.
        sizes: how big the code was in the picture (px, the biggest per set): of the sets where it was read, and of
        those where it was located but not readable."""
        def build():
            passes = {}   # pass id -> {"time", "ok", "source", "method", "read_px", "seen_px", "size"}
            for info in self._sets.values():
                if not self._listed(info):
                    continue
                p = passes.setdefault(info["burst"] or info["id"], {"time": info["time"], "ok": False, "source": None,
                                                                    "method": None, "read_px": None, "seen_px": None,
                                                                    "size": 0})
                p["time"] = min(p["time"], info["time"])
                p["size"] += info["bytes"]
                if info["shoe_id"] is not None:
                    p["ok"], p["source"] = True, p["source"] or info["source"]
                if info.get("method") and (not p["read_px"] or (info.get("read_px") or 0) > p["read_px"]):
                    p["method"] = info["method"]
                for key in ("read_px", "seen_px"):
                    if info.get(key):
                        p[key] = max(p[key] or 0, info[key])
            days, sources, methods = {}, {}, {}
            sizes = {"labels": SIZE_LABELS, "read": [0] * len(SIZE_LABELS), "unread": [0] * len(SIZE_LABELS),
                     "read_no_size": 0}
            total = 0
            for p in passes.values():
                date = p["time"][:10] or "unknown"
                d = days.setdefault(date, {"date": date, "sets": 0, "ok": 0, "fail": 0, "seen": 0, "size": 0, "hours": {}})
                hour = int(p["time"][11:13]) if p["time"][11:13].isdigit() else -1
                h = d["hours"].setdefault(hour, {"hour": hour, "sets": 0, "ok": 0, "fail": 0, "seen": 0, "size": 0})
                seen = not p["ok"] and p["seen_px"] is not None
                total += p["size"]
                for bucket in (d, h):
                    bucket["sets"] += 1
                    bucket["ok" if p["ok"] else "fail"] += 1
                    bucket["seen"] += 1 if seen else 0
                    bucket["size"] += p["size"]
                if p["ok"]:
                    source = p["source"] or "unknown"
                    sources[source] = sources.get(source, 0) + 1
                    if p["method"]:
                        methods[p["method"]] = methods.get(p["method"], 0) + 1
                    if p["read_px"]:
                        sizes["read"][_size_class(p["read_px"])] += 1
                    else:
                        sizes["read_no_size"] += 1
                elif seen:
                    sizes["unread"][_size_class(p["seen_px"])] += 1
            for d in days.values():
                d["hours"] = sorted(d["hours"].values(), key=lambda h: h["hour"])
            return {"days": sorted(days.values(), key=lambda d: d["date"], reverse=True), "sizes": sizes,
                    "sources": sources, "methods": methods, "size": total}
        return self._cached("daily", build)
