"""The browse page's view of the store: every shoe (QR value) with its sets, from a short summary of each set
file that is kept in memory and refreshed from the files' modification times."""

import os
import threading

from ..core.config import SETS_DIR
from .paths import image_path, primary_role, time_key


SIZE_EDGES = (60, 80, 100, 130, 170)   # QR size classes in pixels: <60, 60-79, 80-99, 100-129, 130-169, 170+
SIZE_LABELS = ["<60", "60-79", "80-99", "100-129", "130-169", "170+"]


def _size_class(px):
    return sum(1 for edge in SIZE_EDGES if px >= edge)


class ShoeIndex:
    def __init__(self, get):
        self._get = get   # SampleStore.get: a set's JSON by id
        self._index_lock = threading.Lock()
        self._idx = {}   # set file name -> (mtime, summary): see _index()

    @staticmethod
    def _summary(meta):
        burst = meta.get("burst") or {}
        read = [c["size_px"] for c in meta.get("qr") or [] if c.get("size_px")]
        best = max((c for c in meta.get("qr") or [] if c.get("size_px")), key=lambda c: c["size_px"], default=None)
        seen = [v["qr_seen_px"] for v in (meta.get("views") or {}).values()
                if isinstance(v, dict) and v.get("qr_seen_px")]
        return {"id": meta["id"], "shoe_id": meta.get("shoe_id") or None, "time": meta.get("timestamp") or "",
                "burst": burst.get("id"), "kind": burst.get("kind"), "primary": primary_role(meta),
                "views": list(meta.get("views") or [primary_role(meta)]),
                "source": meta.get("shoe_id_source"),   # where the QR value came from (capture, live, ...)
                "method": (best or {}).get("method"),   # how that code was read (decode.py / enhance.py)
                "read_px": max(read) if read else None,   # size of the biggest code that was read in a picture
                "seen_px": max(seen) if seen else None}   # size of a code that was located but not read

    def _index(self):
        """A short summary of every set file, {file stem: summary}. Kept in memory and refreshed from the
        files' modification times, so asking for the shoe list does not read every file again."""
        with self._index_lock:
            try:
                entries = [e for e in os.scandir(SETS_DIR) if e.name.endswith(".json")]
            except OSError:
                entries = []
            seen = set()
            for e in entries:
                stem = e.name[:-5]
                seen.add(stem)
                try:
                    mtime = e.stat().st_mtime_ns
                except OSError:
                    continue
                cached = self._idx.get(stem)
                if cached and cached[0] == mtime:
                    continue
                meta = self._get(stem)
                if meta is None or "id" not in meta:
                    self._idx.pop(stem, None)
                    continue
                self._idx[stem] = (mtime, self._summary(meta))
            for stem in [s for s in self._idx if s not in seen]:
                del self._idx[stem]
            return {stem: summary for stem, (_, summary) in self._idx.items()}

    @staticmethod
    def _present(info):
        """The views of a set (by its summary) whose picture file still exists."""
        return [r for r in info["views"] if image_path(info["id"], r, primary=r == info["primary"]).exists()]

    def totals(self):
        """What is saved now: {"sets", "cuts", "images", "shoes", "no_id_sets"}. A set is one shoe pass, a cut
        one moment of it, shoes = different QR values, no_id_sets = the sets without a QR value."""
        shoes = self.shoes()
        return {"sets": sum(s["sets"] for s in shoes), "cuts": sum(s["cuts"] for s in shoes),
                "images": sum(s["images"] for s in shoes),
                "shoes": sum(1 for s in shoes if s["shoe_id"] is not None),
                "no_id_sets": sum(s["sets"] for s in shoes if s["shoe_id"] is None)}

    def daily(self):
        """QR reading per day, counted in sets (one shoe pass each):
        {"days": [{"date": "2026-10-15", "sets": 40, "ok": 36, "fail": 4, "seen": 3,
                   "hours": [{"hour": 9, "sets": .., "ok": .., "fail": .., "seen": ..}]}],   newest day first
         "sizes": {"labels": [...], "read": [n per size class], "unread": [...], "read_no_size": n},
         "sources": {"capture": n, "live": n, ...},
         "methods": {"cv": n, "rect+zxing": n, ...}}   how the codes in the saved pictures were read
        ok = the set got a QR value, fail = it did not; seen = of the failed sets, those where a QR code was located
        in a picture but could not be read (fail - seen = no code was seen at all). The day and the hour (0-23,
        oldest first) are those of the set's first cut. sizes: how big the code was in the picture (px, the biggest
        per set): of the sets where it was read, and of those where it was located but not readable."""
        passes = {}   # pass id -> {"time", "ok", "source", "method", "read_px", "seen_px"}
        for info in self._index().values():
            if info["primary"] not in self._present(info):
                continue   # its pictures were deleted: only the set file is left over
            p = passes.setdefault(info["burst"] or info["id"], {"time": info["time"], "ok": False, "source": None,
                                                                "method": None, "read_px": None, "seen_px": None})
            p["time"] = min(p["time"], info["time"])
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
        for p in passes.values():
            date = p["time"][:10] or "unknown"
            d = days.setdefault(date, {"date": date, "sets": 0, "ok": 0, "fail": 0, "seen": 0, "hours": {}})
            hour = int(p["time"][11:13]) if p["time"][11:13].isdigit() else -1
            h = d["hours"].setdefault(hour, {"hour": hour, "sets": 0, "ok": 0, "fail": 0, "seen": 0})
            seen = not p["ok"] and p["seen_px"] is not None
            for bucket in (d, h):
                bucket["sets"] += 1
                bucket["ok" if p["ok"] else "fail"] += 1
                bucket["seen"] += 1 if seen else 0
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
        return {"days": sorted(days.values(), key=lambda d: d["date"], reverse=True), "sizes": sizes, "sources": sources,
                "methods": methods}

    def shoes(self):
        """Every shoe (QR value) that has sets, most recently captured first; the pictures without a QR
        value are listed last as shoe_id None. A "set" is one shoe pass (before / center / after of every
        camera), a "cut" is one moment of it:
        [{"shoe_id", "sets", "cuts", "images", "first", "last", "thumb": {"id", "view"}}]."""
        groups = {}
        for info in self._index().values():
            present = self._present(info)
            if info["primary"] not in present:
                continue   # its pictures were deleted: only the set file is left over
            g = groups.setdefault(info["shoe_id"], {"shoe_id": info["shoe_id"], "passes": set(), "cuts": 0,
                                                     "images": 0, "first": info["time"], "last": info["time"],
                                                     "centre": None, "newest": None})
            g["passes"].add(info["burst"] or info["id"])
            g["cuts"] += 1
            g["images"] += len(present)
            g["first"], g["last"] = min(g["first"], info["time"]), max(g["last"], info["time"])
            if g["newest"] is None or info["time"] > g["newest"]["time"]:
                g["newest"] = info
            if info["kind"] == "center" and (g["centre"] is None or info["time"] > g["centre"]["time"]):
                g["centre"] = info
        out = []
        for g in groups.values():
            pick = g["centre"] or g["newest"]
            out.append({"shoe_id": g["shoe_id"], "sets": len(g["passes"]), "cuts": g["cuts"], "images": g["images"],
                        "first": g["first"], "last": g["last"], "thumb": {"id": pick["id"], "view": pick["primary"]}})
        out.sort(key=lambda s: s["last"], reverse=True)
        out.sort(key=lambda s: s["shoe_id"] is None)   # stable: the pictures without a QR value come last
        return out

    def by_shoe(self, shoe):
        """The sets (one per cut) of one shoe, newest first, in full. shoe None = no QR value was read."""
        infos = [i for i in self._index().values() if i["shoe_id"] == shoe]
        infos.sort(key=lambda i: (time_key(i["id"]), i["id"]), reverse=True)
        out = []
        for info in infos:
            meta = self._get(info["id"])
            if meta is None:
                continue
            present = self._present(info)
            if info["primary"] not in present:
                continue
            if meta.get("views"):
                meta["views"] = {r: v for r, v in meta["views"].items() if r in present}
            out.append(meta)
        return out
