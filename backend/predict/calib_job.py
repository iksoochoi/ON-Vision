"""The cam plate calibration asked for on the Predict page, done by the predict worker a picture at a time (only while
no new set waits, like a period). How and why: backend/storage/gap_calib.py. Only an ESTIMATED K (2026-11-24): the
new template is cut from a picture of the period, K_new = K_old / s of that picture against the template in use.

Two rounds over the left and right pictures of up to CALIB_SETS sets of the period:
  "old": the plate found with the calibration in use (score, size s, angle, where)
  "new": after the new templates are cut, found again with them: how many are found and how far the new scale
         (K_new / s_new) is from the old one (K_old / s_old) on the same pictures; with a QR label on the shoe in a
         picture (rare since 2026-11-14) also against the QR's scale (the true one)."""

import json
import time

import cv2
import numpy as np

from backend.core import config as C
from backend.predict import bonding_gap as bg
from backend.storage import gap_calib, predictions
from backend.storage.paths import image_path, primary_role

REF_SCORE_SHARE = 0.95   # the reference picture: of those within this share of the best score, the least rotated


def _pct(values, q):
    return round(float(np.percentile(values, q)), 2) if len(values) else None


class CalibJob:
    def __init__(self, req):
        self.req = req
        ids = predictions.period_ids(req["from"], req["to"])
        step = max(1, len(ids) // gap_calib.CALIB_SETS)
        self.ids = ids[::step][:gap_calib.CALIB_SETS]
        self.base_dir = gap_calib.plate_dir()
        self.base = gap_calib.read_calib(self.base_dir)
        self.tpl = {v: bg.read_gray(self.base_dir / self.base[v]["template"]) if (self.base.get(v) or {}).get("template") else None
                    for v in gap_calib.SIDES}
        self.phase = "old"
        self.todo = [(sid, v) for v in gap_calib.SIDES for sid in self.ids]
        self.rows = {}    # (sid, view) -> {"old": match, "new": match, "qr": mm/px of a QR on the shoe}
        self.new = {}     # view -> {"calib": ..., "tpl": gray} after the old round
        self.summary = {"id": req["id"], "from": req["from"], "to": req["to"], "sets": len(self.ids), "state": "running",
                        "phase": "old", "done": 0, "total": 2 * len(self.todo), "started": time.time()}

    def _picture(self, sid, view):
        """(BGR picture, label corners px or None (a QR label on the last / plate seen by this camera), QR mm/px or
        None (a QR on the shoe seen by this camera)) or None."""
        try:
            meta = json.loads((C.SETS_DIR / f"{sid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        path = image_path(sid, view, primary=view == primary_role(meta))
        bgr = cv2.imread(str(path)) if path.exists() else None
        if bgr is None:
            return None
        H, W = bgr.shape[:2]
        qv, pts = bg.qr_of(meta)
        label = qr = None
        if qv == view and pts is not None:
            q = pts * [W, H]
            if pts[:, 1].mean() < bg.MIN_QR_Y:
                label = q
            else:
                qr = bg.QR_MM / float(np.mean([np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]))
        return bgr, label, qr

    def step(self):
        """One picture (about a second). False when all is done."""
        if not self.todo:
            if self.phase == "old":
                self._cut()
                self.phase = self.summary["phase"] = "new"
                self.todo = [(sid, v) for v in gap_calib.SIDES for sid in self.ids if v in self.new]
                self.summary["total"] = self.summary["done"] + len(self.todo)
                return True
            return False
        sid, view = self.todo.pop(0)
        self.summary["done"] += 1
        tpl = self.tpl.get(view) if self.phase == "old" else self.new[view]["tpl"]
        got = self._picture(sid, view) if tpl is not None else None
        if got is None:
            return True
        bgr, label, qr = got
        f = bg.find_plate(bgr, view, tpl, label)
        row = self.rows.setdefault((sid, view), {})
        row[self.phase] = f
        if qr:
            row["qr"] = qr
        return True

    def _cut(self):
        """After the old round: per camera the reference picture, its new template and the estimated K."""
        for v in gap_calib.SIDES:
            c = self.base.get(v) or {}
            if not c.get("k") or self.tpl.get(v) is None:
                continue
            good = [(sid, r["old"]) for (sid, vv), r in self.rows.items()
                    if vv == v and r.get("old") and r["old"]["score"] >= c["min_score"]]
            if len(good) < gap_calib.MIN_GOOD:
                self.summary.setdefault("errors", {})[v] = (
                    f"the cam plate was found well in {len(good)} of {sum(1 for (_, vv) in self.rows if vv == v)} pictures "
                    f"(at least {gap_calib.MIN_GOOD} needed): the camera moved too far for the template in use")
                continue
            best = max(f["score"] for _, f in good)
            sid, f = min(((s, f) for s, f in good if f["score"] >= REF_SCORE_SHARE * best), key=lambda t: abs(t[1]["ang"]))
            bgr, label, _ = self._picture(sid, v)
            if label is not None:
                bgr = bg.hide_label(bgr, label)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            H, W = gray.shape
            x0, y0, x1, y1 = max(0, f["box"][0]), max(0, f["box"][1]), min(W, f["box"][2]), min(H, f["box"][3])
            tpl = gray[y0:y1, x0:x1].copy()
            calib = {"template": f"plate_{v}.png", "ref": sid, "ref_box": [int(x0), int(y0), int(x1), int(y1)],
                     "min_score": c["min_score"], "k": round(c["k"] / f["s"], 6), "estimated": True,
                     "from_k": c["k"], "s_ref": f["s"], "ang_ref": f["ang"], "ref_score": f["score"]}
            self.new[v] = {"calib": calib, "tpl": tpl}

    def finish(self):
        """After the new round: the candidate written (when a camera got a new calibration) and the request removed.
        Returns the summary."""
        calib = {"made_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": self.req["from"], "to": self.req["to"],
                 "estimated": True, "base": "site" if self.base_dir == gap_calib.ACTIVE_DIR else "default"}
        templates = {}
        for v in gap_calib.SIDES:
            if v not in self.new:   # this camera keeps the calibration in use
                if self.base.get(v):
                    calib[v] = dict(self.base[v], kept=(self.summary.get("errors") or {}).get(v, "not calibrated"))
                    name = self.base[v].get("template")
                    if name and (self.base_dir / name).is_file():
                        templates[name] = (self.base_dir / name).read_bytes()
                continue
            c = dict(self.new[v]["calib"])
            k_old = self.base[v]["k"]
            n = sum(1 for (_, vv) in self.rows if vv == v)
            rows = [r for (_, vv), r in self.rows.items() if vv == v]
            # K so that the new template gives the scale of the old one, over all pictures where both found the plate
            # (the median): one reference picture alone carries its own size error into K (2026-11-24, 30 sets of
            # 2026-11-14: 1.5% / 2.8% off the old scale with K_old / s of that picture)
            fit = [k_old * r["new"]["s"] / r["old"]["s"] for r in rows if r.get("new") and r.get("old")
                   and r["new"]["score"] >= c["min_score"] and r["old"]["score"] >= c["min_score"]]
            if len(fit) >= gap_calib.MIN_GOOD:
                c["k_ref"] = c["k"]
                c["k"] = round(float(np.median(fit)), 6)
            k_new = c["k"]
            found = [r["new"] for r in rows if r.get("new") and r["new"]["score"] >= c["min_score"]]
            both = [100 * ((k_new / r["new"]["s"]) / (k_old / r["old"]["s"]) - 1) for r in rows
                    if r.get("new") and r.get("old") and r["new"]["score"] >= c["min_score"] and r["old"]["score"] >= c["min_score"]]
            qr = [100 * ((k_new / r["new"]["s"]) / r["qr"] - 1) for r in rows
                  if r.get("qr") and r.get("new") and r["new"]["score"] >= c["min_score"]]
            c.update(sets=n, used=len(found),
                     check={"found_old": sum(1 for r in rows if r.get("old") and r["old"]["score"] >= c["min_score"]),
                            "found_new": len(found), "pictures": n,
                            "score_old": _pct([r["old"]["score"] for r in rows if r.get("old")], 50),
                            "score_new": _pct([r["new"]["score"] for r in rows if r.get("new")], 50),
                            "vs_old_pct_median": _pct(np.abs(both), 50), "vs_old_pct_p90": _pct(np.abs(both), 90),
                            "vs_qr_pct_median": _pct(np.abs(qr), 50), "qr_pictures": len(qr)})
            ok, png = cv2.imencode(".png", self.new[v]["tpl"])
            if ok:
                templates[c["template"]] = png.tobytes()
            calib[v] = c
        if self.new:
            gap_calib.write_candidate(calib, templates)
            self.summary["state"] = "done"
        else:
            self.summary["state"] = "failed"
        self.summary["finished"] = time.time()
        gap_calib.clear_request(self.req["id"])
        return self.summary
