"""Bonding gap of a QC set, measured with the QR label as the ruler: the white band between the end of the sprayed
upper and the top edge of the midsole, along the whole side of the shoe (started next to the QR label).

Made for the QC viewer (train/qc_sheet, 2026-11-13 .. 11-19) and moved here on 2026-11-24, unchanged, so that the
predict worker measures every set too (backend/predict/worker.py). train/qc_sheet/bonding_gap.py is now only this
module under its old name (qc_viewer.py and plate_calib.py import it). The cam plate files are in
backend/models/bonding_gap/. Only two things were added: measure_set / measure_both take the paths of the pictures
(`pics`: the app keeps them as captures/<camera>/<id>.jpg, a qc_export folder as <id>_<camera>.jpg beside the json).

    C:/Users/ISCHOI/anaconda3/envs/on_running/python.exe -m backend.predict.bonding_gap "captures/qc_defect"
    ... -m backend.predict.bonding_gap "captures/qc_defect" --draw <out folder>    # also a picture of each measurement

How (one picture: the camera that read the QR, left for an L shoe, right for an R shoe):
  - scale: the QR side in px (the Mac saves its corners in the set's json: qr[].points) and its real side, 13 mm
    (QR_MM, measured on a label with a ruler)
  - window: from 0.9 to 3.8 QR sides away from the QR centre towards the heel (right camera: +x, left camera: -x),
    from 0.1 to 2.2 QR sides below it; the columns of the window one by one:
      upper edge   the place where it turns from dark (upper) to the white band, dark above and white below for
                   0.1 QR side; the picture is blurred along the edge first (the glitter of the upper averages out)
      midsole edge the brightest line under it (the top edge of the midsole catches the light), then the steepest
                   drop after it (the side wall of the midsole)
    a straight line through each edge (the window is about 40 mm: the edges are nearly straight there), points
    far off the line left out (glitter, the label, a fold); the two lines must be about parallel
  - gap = distance of the two lines at right angles (the vertical distance x cos of the slope), in mm.
  - then the whole side (measure_full): from the window both ways along the shoe, column by column, each column
    searched near where the last points predict: the midsole edge first, then the LOWEST dark-to-white turn above it
    with a band as bright as the midsole in between (the ON RUNNING print is higher and onto the grey upper). Stops
    at the shoe box of the capture (views.<camera>.pick.shoe.box) or after 1.0 (heel) / 2.4 (toe, to cross under the
    label) QR sides without an edge. The midsole edge is smoothed hard (robust local fit, a moulded line: misreadings
    such as a shadow at the toe drop out), the upper edge lightly (a gap shows there); 3 mm at both ends left out.
    Result: the largest gap (running median over 4 mm), the median, the length over the limit, and those parts.
    A defect = over the limit for OVER_MIN_MM along the shoe.
Checked on ON1WH-B-B#10TREQBBAQK1W1B29962R 20261106_174909_618 (right): about 5.4 mm, the edges where a person sees
them. The band is the top face of the midsole seen at an angle: the real gap is the same or larger than measured.
The scale is the QR's: right for the side near it; the toe and the heel turn away from the camera (and the band is
seen more from above there), so a value far from the QR is less exact.
A measurement is 'ok', 'unsure' (few columns found an edge, or the edges are not parallel), 'fail' (with why), or
'none' (not measured, with why).
The cam plate (2026-11-18): the metal plate on top of every last is the same on all sizes; found by template matching
(plate/, made by plate_calib.py) it gives the scale too. With a QR on the shoe: result 'scale' = 'qr', the plate's
scale beside it ('plate_diff_pct'; 'check' when more than 5% apart). Without (no QR position, read by another camera,
or the label on the last: QR centre in the upper part of the picture, MIN_QR_Y, seen on 2026-11-09 evening): the
camera of the label side (L shoe: left), 'scale' = 'plate', the start window searched over the shoe box (the start
with the most agreeing columns), then traced the same way. Checked by measuring the sets with a QR without it: the
same edges in 111 of 122, there the median gap within 0.05 mm (p90 0.16); 11 started on other edges.
2026-11-18, qc_defect_2 (the label on the cam plate from 2026-11-14; the lime ON1GR-L-B): the label is painted over
before the plate match (hide_label), dark pictures get a wider teal range (TEAL_LOW), and a coloured upper is measured
in the blue channel (edge_image, result 'channel'; COLOR_SAT decides). The grey ON1WH measured exactly as before.
2026-11-19: a coloured upper in V - 2*S (HSV) instead of the blue channel: on the right camera the lime upper near the
sole is pale (blue about as light as the white band), so the start was found on the outsole at the toe; the white
band has almost no saturation, the upper does (edge_image, channel 'vs').
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

QR_MM = 13.0          # the side of the printed QR code (not the label)
LIMIT_MM = 5.0        # more than this is a bonding gap defect (until 2026-11-19 4.0; see OVER_MIN_MM)
ALGO = 8              # change when the measurement changes: older cached results are measured again
                      # (7, 2026-11-18: a coloured upper in the blue channel; the label on the plate hidden)
                      # (8, 2026-11-19: a coloured upper in V - 2*S; the cam plate found in three steps; no QR:
                      # the longest of several traced starts; the bent toe / heel left out; limits 5 mm / 30 mm)
WIN_X = (0.9, 3.8)    # window, in QR sides from the QR centre, towards the heel
WIN_Y = (0.1, 2.2)    # below the QR centre
MIN_QR_Y = 0.4        # a QR centre higher in the picture is a label on the last (the jig), not on the shoe
MAX_SLOPE_DIFF = 0.06 # the two edges must be about parallel
MAX_MISS_SIDES = (1.0, 2.4)   # tracing: QR sides without an edge before it stops (towards the heel, towards the toe:
                              # that way it crosses under the label)
MIDSOLE_SMOOTH_MM = 20.0   # the midsole edge: robust local fit, half width
END_TRIM_MM = 3.0     # left out at both ends of the traced line
END_SLOPE = 0.25      # the toe / heel bending away: the midsole line's slope this far off the slope of the whole line
END_ZONE = 0.25       # ... looked for in this part of the line at each end
PROFILE = False       # True: measure_full adds "_prof" (every column) for tuning
MAX_WALL_DROP = 60    # tracing: below the midsole edge the side wall is still light (the bright line minus the mean of
WALL_SIDES = 0.15     # this many QR sides below it, about 2 mm); more: the edge found is the dark outsole at the toe or
                      # heel (spray down to the midsole there, 2026-11-17: 20261109_142131_460 read 8.1 mm at the toe)
SMOOTH_MM = 4.0      # the gap is a running median over this length
OVER_MIN_MM = 30.0   # a defect: the gap over the limit for at least this length along the shoe (2026-11-13: the known
                     # Bonding_gap shoes 29-129 mm, the OK shoes 0-48 mm; few labelled shoes, the viewer can change it)
# 2026-11-19, all 631 sets of qc_1105-1109 + qc_1110-1114, set = either side (ALGO 8):
#   4.0 / 20 mm: grey Bonding_gap 15/23, OK 7/42, other types 116/399, a scale 2% off flips 42 sets
#   5.0 / 30 mm: grey Bonding_gap 15/23, OK 0/42, other types 12/399, flips 8; coloured (ON1GR) 5/5, 0/6, 0/59, 1
#   The normal gap of both uppers is ~2-4.5 mm, so 4 mm cut through the normal shoes. The same limits fit both: no
#   limits per model. The 8 Bonding_gap sets missed measure 3.5-4.9 mm (the defect not in the side pictures?).
#   Only the folders of the QC sheet as labels: other types are not checked to be without a gap.


def read_gray(path):
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None


def qr_of(meta):
    """(view, 4x2 corner points in 0..1) of the QR the Mac read in this set (the largest one), or (None, None)."""
    best = None
    for c in meta.get("qr") or []:
        pts = c.get("points")
        if c.get("view") and pts and len(pts) == 4 and (best is None or (c.get("size_px") or 0) > (best.get("size_px") or 0)):
            best = c
    return (best["view"], np.array(best["points"], float)) if best else (None, None)


def _robust_fit(xs, ys, deg=1, k=2.5, it=5):
    ok = ~np.isnan(ys)
    if ok.sum() < deg + 3:
        return None, ok
    y = np.nan_to_num(ys)
    for _ in range(it):
        p = np.polyfit(xs[ok], y[ok], deg)
        r = y - np.polyval(p, xs)
        mad = np.median(np.abs(r[ok])) * 1.4826 + 1e-6
        ok2 = ok & (np.abs(r) < max(k * mad, 4))
        if ok2.sum() < deg + 3:
            break
        ok = ok2
    return np.polyfit(xs[ok], y[ok], deg), ok


def measure(gray, view, pts_norm, limit=LIMIT_MM):
    """The gap of one picture (grayscale, full size) by the QR at pts_norm (4x2, 0..1). A dict, coordinates in 0..1."""
    H, W = gray.shape[:2]
    if pts_norm[:, 1].mean() < MIN_QR_Y:
        return {"status": "none", "view": view, "why": "the QR label is on the last, not on the shoe"}
    q = pts_norm * [W, H]
    side = float(np.mean([np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]))
    if side < 20:
        return {"status": "fail", "why": f"QR too small ({side:.0f} px)"}
    mmpx = QR_MM / side
    cx, cy = q.mean(0)
    d = 1 if view == "right" else -1          # towards the heel
    xa, xb = sorted((cx + d * WIN_X[0] * side, cx + d * WIN_X[1] * side))
    ya, yb = cy + WIN_Y[0] * side, cy + WIN_Y[1] * side
    pad = int(0.3 * side)
    X0, Y0 = max(0, int(xa) - pad), max(0, int(ya) - pad)
    X1, Y1 = min(W, int(xb) + pad), min(H, int(yb + 1.3 * side) + pad)
    if X1 - X0 < side or Y1 - Y0 < side:
        return {"status": "fail", "why": "the window is outside the picture"}
    crop = gray[Y0:Y1, X0:X1].astype(np.float32)
    g = cv2.GaussianBlur(crop, (0, 0), sigmaX=max(2.0, side * 0.062), sigmaY=max(1.0, side * 0.014))
    dd = np.diff(g, axis=0)
    a, b = max(2, int(side * 0.02)), max(4, int(side * 0.10))   # the dark / white check above and below an edge
    xs = np.arange(int(xa), int(xb), max(2, int(side / 30))).astype(float)
    red, grn = np.full(len(xs), np.nan), np.full(len(xs), np.nan)
    # the upper edge of every column at once: dark above / white below (means of b-a rows), plus the steepest rise
    cols = xs.astype(int) - X0
    G, D = g[:, cols], dd[:, cols]
    cs = np.vstack([np.zeros((1, len(cols)), np.float32), np.cumsum(G, 0)])
    ys = np.arange(max(b, int(ya) - Y0), min(G.shape[0] - b, int(yb) - Y0))
    if len(ys) < 3:
        return {"status": "fail", "why": "the window is outside the picture"}
    above = (cs[ys - a] - cs[ys - b]) / (b - a)
    below = (cs[ys + b] - cs[ys + a]) / (b - a)
    Dp = np.vstack([np.full((2, len(cols)), -np.inf, np.float32), D, np.full((3, len(cols)), -np.inf, np.float32)])
    rise = np.max(np.stack([Dp[ys + k] for k in range(5)]), axis=0)          # max of dd[y-2 .. y+2]
    score = np.where(below - above > 35, rise + 0.5 * (below - above), -np.inf)
    best = np.argmax(score, axis=0)
    for i in range(len(xs)):
        if not np.isfinite(score[best[i], i]):
            continue
        y1 = int(ys[best[i]])
        c, dc = G[:, i], D[:, i]
        seg = c[y1:y1 + int(0.9 * side)]
        if len(seg) < 3:
            continue
        yp = y1 + int(np.argmax(seg))
        dseg = dc[yp:yp + max(3, int(0.28 * side))]
        if len(dseg) < 2:
            continue
        y2 = yp + int(np.argmin(dseg))
        if c[yp] - c[min(len(c) - 1, y2 + a)] < 12:     # no clear side wall after the bright line
            red[i] = y1
            continue
        red[i], grn[i] = y1, y2
    n = len(xs)
    pr, okr = _robust_fit(xs, red)
    pg, okg = _robust_fit(xs, grn)
    out = {"view": view, "size": [W, H], "qr_px": round(side, 1), "mm_per_px": round(mmpx, 4), "columns": n,
           "upper_found": int((~np.isnan(red)).sum()), "midsole_found": int((~np.isnan(grn)).sum()),
           "window": [[xa / W, (ya) / H], [xb / W, (yb) / H]], "limit_mm": limit}
    if pr is None or pg is None:
        out.update(status="fail", why="no edges found in the window")
        return out
    # back to picture coordinates (the fits are in crop rows)
    yr, yg = np.polyval(pr, xs) + Y0, np.polyval(pg, xs) + Y0
    slope = np.gradient((yr + yg) / 2, xs)
    gap = (yg - yr) * np.cos(np.arctan(slope)) * mmpx
    keep = okr & okg
    if keep.sum() < 3:
        out.update(status="fail", why="the two edges do not agree")
        return out
    gk = gap[keep]
    step = max(1, n // 12)
    out.update({
        "gap_mm": round(float(np.median(gk)), 2), "min_mm": round(float(gk.min()), 2), "max_mm": round(float(gk.max()), 2),
        "inliers": int(keep.sum()),
        "upper": [[float(x / W), float(y / H)] for x, y in zip(xs[::step], yr[::step])] + [[float(xs[-1] / W), float(yr[-1] / H)]],
        "midsole": [[float(x / W), float(y / H)] for x, y in zip(xs[::step], yg[::step])] + [[float(xs[-1] / W), float(yg[-1] / H)]],
        "qr": (q / [W, H]).round(5).tolist(),
    })
    if not (0.3 <= out["gap_mm"] <= 20) or (gk < 0).any():
        out.update(status="fail", why=f"not a plausible gap ({out['gap_mm']} mm)")
    elif abs(pr[0] - pg[0]) > MAX_SLOPE_DIFF:
        out.update(status="unsure", why=f"the two edges are not parallel (slopes {pr[0]:.2f} / {pg[0]:.2f})")
    elif keep.sum() < 0.6 * n:
        out.update(status="unsure", why=f"edges found in {keep.sum()} of {n} columns")
    else:
        out["status"] = "ok"
    out["defect"] = out["status"] != "fail" and out["gap_mm"] > limit
    out["_seed"] = (xs[keep], yr[keep], yg[keep])   # for measure_full (not in the result)
    return out


def _trace(gray, side, seed, box, d_heel):
    """Follow the two edges from the measured window (seed: xs, upper ys, midsole ys of its good columns) along the
    whole side of the shoe, both ways, one column at a time: each column is searched only near the place the last
    points predict. Stops at the shoe box (the detection of the capture) or after MAX_MISS_SIDES without an edge
    (the label is crossed that way). [(x, upper y, midsole y, found)] sorted by x; found False = carried over."""
    H, W = gray.shape
    xs0, yr0, yg0 = seed
    y_top = int(max(0, min(yr0.min(), yg0.min()) - 3.0 * side))
    y_bot = int(min(H, max(yr0.max(), yg0.max()) + 5.0 * side))
    band = gray[y_top:y_bot].astype(np.float32)
    G = cv2.GaussianBlur(band, (0, 0), sigmaX=max(2.0, side * 0.062), sigmaY=max(1.0, side * 0.014))
    D = np.diff(G, axis=0)
    CS = np.vstack([np.zeros((1, W), np.float32), np.cumsum(G, 0)])
    a, b = max(2, int(side * 0.02)), max(4, int(side * 0.10))
    step = max(2, int(side / 30))
    tol_r, tol_g = 0.15 * side, 0.20 * side
    bx0, bx1 = (box[0] * W, box[2] * W) if box else (0, W)
    x_lo, x_hi = max(b, bx0 + 0.01 * W), min(W - b, bx1 - 0.01 * W)

    def find(x, pr, pg):
        """The midsole edge first (the brightest line near where it is expected, then the steepest drop), then the
        upper edge: the LOWEST dark-to-white turn above that line with a band as bright as the midsole between them
        (print on the upper, such as the ON RUNNING letters, also turns dark to light, but higher and onto the grey
        upper, not onto the white band)."""
        x = int(x)
        c = G[:, x]
        glo, ghi = int(max(b, pg - 0.35 * side - y_top)), int(min(len(G) - 2, pg + 0.1 * side - y_top))   # the bright line is just above the edge
        if ghi - glo < 2:
            return None
        yp = glo + int(np.argmax(c[glo:ghi]))                       # brightest line: the top of the midsole
        dseg = D[yp:yp + max(3, int(0.28 * side)), x]
        if len(dseg) < 2:
            return None
        y2 = yp + int(np.argmin(dseg))                              # side wall: the midsole edge
        if c[yp] - c[min(len(c) - 1, y2 + a)] < 12:
            return None
        wall = c[min(len(c) - 1, y2 + a):min(len(c), y2 + max(a + 2, int(WALL_SIDES * side)))].mean()
        if c[yp] - wall > MAX_WALL_DROP:                              # not the midsole: the outsole below it
            return None
        lo = int(max(b, yp - 1.2 * side, pr - 0.4 * side - y_top))
        hi = int(min(yp - a, pr + 0.4 * side - y_top))
        if hi - lo < 3:
            return None
        ys = np.arange(lo, hi)
        above = (CS[ys - a, x] - CS[ys - b, x]) / (b - a)
        band = (CS[yp, x] - CS[ys + a, x]) / np.maximum(1, yp - ys - a)   # mean from the edge to the bright line
        rise = np.max(np.stack([D[np.clip(ys + k, 0, len(D) - 1), x] for k in range(-2, 3)]), axis=0)
        valid = (band - above > 35) & (band > c[yp] - 30)
        if not valid.any():
            return None
        low = int(ys[valid].max())                                  # the lowest turn ...
        near = valid & (ys >= low - 0.1 * side)                    # ... the sharpest point of it
        score = np.where(near, rise + 0.5 * (band - above), -np.inf)
        y1 = int(ys[int(np.argmax(score))])
        return y1 + y_top, y2 + y_top

    pts = [(float(x), float(r), float(g), True) for x, r, g in zip(xs0, yr0, yg0)]
    for direction in (-1, 1):
        hist = sorted(pts, key=lambda p: p[0] * direction)[-12:]   # the seed points at this end
        x, miss, new = hist[-1][0] + direction * step, 0, []
        while x_lo <= x <= x_hi:
            hx = np.array([p[0] for p in hist[-12:]])
            pr_fit = np.polyfit(hx, [p[1] for p in hist[-12:]], 1)
            pg_fit = np.polyfit(hx, [p[2] for p in hist[-12:]], 1)
            pr, pg = np.polyval(pr_fit, x), np.polyval(pg_fit, x)
            f = find(x, pr, pg)
            if f and f[1] - f[0] > 0.05 * side:
                p = (float(x), float(f[0]), float(f[1]), True)
                miss = 0
            else:
                p = (float(x), float(pr), float(pg), False)
                miss += 1
                limit_sides = MAX_MISS_SIDES[1] if direction == -d_heel else MAX_MISS_SIDES[0]
                if miss * step > limit_sides * side:   # lost: drop the carried-over points at the end
                    while new and not new[-1][3]:
                        new.pop()
                    break
            new.append(p)
            if p[3]:
                hist.append(p)
            x += direction * step
        while new and not new[-1][3]:
            new.pop()
        pts += new
    return sorted(pts)


def _lowess(x, y, ok, width, it=3):
    """A robust local quadratic fit of y(x) (points with ok False or far off it count little): the value at every x.
    width: the half width of the neighbourhood in x units."""
    out = np.array(y, float)
    w_rob = ok.astype(float)
    idx = np.unique(np.r_[np.arange(0, len(x), 3), len(x) - 1])   # fitted at every 3rd point, the rest in between
    for _ in range(it):
        val = out[idx].copy()
        for j, i in enumerate(idx):
            u = np.abs(x - x[i]) / width
            w = np.where(u < 1, (1 - u ** 3) ** 3, 0) * w_rob
            if (w > 0).sum() < 5:
                continue
            sw = np.sqrt(w)
            A = np.stack([np.ones_like(x), x - x[i], (x - x[i]) ** 2], 1) * sw[:, None]
            val[j] = np.linalg.lstsq(A, y * sw, rcond=None)[0][0]
        out = np.interp(x, x[idx], val)
        res = np.abs(y - out)
        s = np.median(res[ok]) * 6 + 1e-6 if ok.any() else 1.0
        w_rob = np.where(ok, np.clip(1 - (res / s) ** 2, 0, 1) ** 2, 0)
    return out


def _runs(mask):
    """[(start, end)] of the True runs of a boolean array (end exclusive)."""
    out, s = [], None
    for i, m in enumerate(mask):
        if m and s is None:
            s = i
        elif not m and s is not None:
            out.append((s, i))
            s = None
    if s is not None:
        out.append((s, len(mask)))
    return out


def measure_full(gray, view, pts_norm, box=None, limit=LIMIT_MM, local=None, trace=None):
    """measure() next to the QR, then the edges followed along the whole side (the shoe box of the capture as the
    end). gap_mm = the largest gap (a running median over SMOOTH_MM of length); also the median, where it is
    largest and the length over the limit. Falls back to the window result when the window has no good edges.
    local: measure()'s result already made (measure_noqr); trace: its _trace already made."""
    local = dict(local) if local is not None else measure(gray, view, pts_norm, limit)
    if local.get("status") not in ("ok", "unsure") or "_seed" not in local:
        local.pop("_seed", None)
        return local
    side, mmpx = local["qr_px"], local["mm_per_px"]
    H, W = gray.shape
    xs0, yr0, yg0 = local.pop("_seed")
    tr = trace if trace is not None else _trace(gray, side, (xs0, yr0, yg0), box, 1 if view == "right" else -1)
    x = np.array([p[0] for p in tr]); r = np.array([p[1] for p in tr]); g = np.array([p[2] for p in tr])
    found = np.array([p[3] for p in tr])
    # the midsole edge is a smooth moulded line: a robust local fit (points off it are misreadings, such as a
    # shadow at the toe); the upper edge only loses single spikes (running median over ~2 mm): a gap shows there
    colw = max(2, int(side / 30))
    gs = _lowess(x, g, found, MIDSOLE_SMOOTH_MM / mmpx)
    g_ok = np.abs(g - gs) < 0.08 * side
    k = max(3, int(round(2.0 / mmpx / colw)) | 1)
    med = lambda v: np.array([np.median(v[max(0, i - k // 2):i + k // 2 + 1]) for i in range(len(v))])
    rs = med(r)
    trim = int(round(END_TRIM_MM / mmpx / colw))                    # the ends: the shoe turns away there
    ends = np.zeros(len(x), bool)
    ends[:trim] = ends[len(x) - trim:] = True
    # the toe (and heel) where the shoe bends up and turns away: the top of the midsole is seen wider there and the
    # midsole edge found drops onto the outsole - a wide gap that is the view, not the bonding (2026-11-19: the max of
    # ~1 of 10 ON1WH pictures, 8-15 mm at the toe). Left out of the max and the length over: from each end, up to the
    # innermost point within END_ZONE of the line where its slope is off the slope of the whole line by END_SLOPE.
    curved_mm = 0.0
    fit = found & g_ok & ~ends
    if fit.sum() > 5:
        dev = np.abs(np.gradient(gs, x) - np.polyfit(x[fit], gs[fit], 1)[0])
        zone = int(END_ZONE * len(x))
        lo = np.where(dev[:zone] > END_SLOPE)[0]
        hi = np.where(dev[len(x) - zone:] > END_SLOPE)[0]
        cut = np.zeros(len(x), bool)
        if len(lo):
            cut[:lo.max() + 1] = True
        if len(hi):
            cut[len(x) - zone + hi.min():] = True
        curved_mm = float((cut & ~ends).sum() * colw * mmpx)
        ends |= cut
    found = found & g_ok & ~ends
    slope = np.gradient((rs + gs) / 2, x)
    gap = (gs - rs) * np.cos(np.arctan(slope)) * mmpx
    n = max(3, int(round(SMOOTH_MM / mmpx / max(2, int(side / 30)))) | 1)
    gm = np.array([np.median(gap[max(0, i - n // 2):i + n // 2 + 1]) for i in range(len(gap))])
    gm[~found] = np.nan
    if np.isnan(gm).all():
        return local
    i_max = int(np.nanargmax(gm))
    over = np.nan_to_num(gm, nan=0) > limit
    dx = np.gradient(x) * mmpx
    runs = [(s, e) for s, e in _runs(over)]
    span_mm = float((x.max() - x.min()) * mmpx)
    stepn = max(1, len(x) // 120)
    P = lambda ys: [[round(float(xx / W), 5), round(float(yy / H), 5)] for xx, yy in zip(x[::stepn], ys[::stepn])] \
        + [[round(float(x[-1] / W), 5), round(float(ys[-1] / H), 5)]]
    out = dict(local)
    out.update({
        "mode": "full", "local_mm": local.get("gap_mm"),
        "gap_mm": round(float(gm[i_max]), 2), "median_mm": round(float(np.nanmedian(gm)), 2),
        "min_mm": round(float(np.nanmin(gm)), 2), "max_mm": round(float(gm[i_max]), 2),
        "max_at": [round(float(x[i_max] / W), 5), round(float((rs[i_max] + gs[i_max]) / 2 / H), 5)],
        "max_line": [[round(float(x[i_max] / W), 5), round(float(rs[i_max] / H), 5)],
                     [round(float(x[i_max] / W), 5), round(float(gs[i_max] / H), 5)]],
        "span_mm": round(span_mm, 1), "found_pct": round(100 * float(found.mean()), 1), "curved_mm": round(curved_mm, 1),
        "over_mm": round(float(sum(dx[s:e].sum() for s, e in runs)), 1),
        "over": [[P_ for P_ in zip(x[s:e:max(1, (e - s) // 20)].tolist() + [x[e - 1]],
                                   rs[s:e:max(1, (e - s) // 20)].tolist() + [rs[e - 1]],
                                   gs[s:e:max(1, (e - s) // 20)].tolist() + [gs[e - 1]])] for s, e in runs],
        "upper": P(rs), "midsole": P(gs),
    })
    out["over"] = [[[round(xx / W, 5), round(yr_ / H, 5), round(yg_ / H, 5)] for xx, yr_, yg_ in run] for run in out["over"]]
    if out["found_pct"] < 60:
        out.update(status="unsure", why=f"edges found in {out['found_pct']:.0f}% of the traced length")
    if PROFILE:   # for tuning (not in normal results): the lines and flags of every column
        out["_prof"] = {"x": x.tolist(), "rs": rs.tolist(), "gs": gs.tolist(), "found": (np.array([p[3] for p in tr])
                        & g_ok).tolist(), "trim": trim, "colw": colw, "mmpx": mmpx, "gap": gap.tolist()}
    out["over_min_mm"] = OVER_MIN_MM
    out["defect"] = out["status"] != "fail" and out["over_mm"] >= OVER_MIN_MM
    return out


# ---- the cam plate: the metal plate on top of every last (the same on all sizes; 2026-11-18, measured: the layers
# 10 / 10.5 / 9.5 cm). Found by template matching (one template per camera, over scales and a few degrees), its size
# gives the scale; K (plate_calib.json, made by plate_calib.py from the sets with a QR) turns it into mm/px at the side
# of the shoe: mm_per_px = K / s (s = the size of the plate against the template).

PLATE_DIR = Path(__file__).resolve().parents[1] / "models" / "bonding_gap"   # until 2026-11-24 train/qc_sheet/plate
PLATE_CHECK_PCT = 5.0  # QR and plate scale further apart than this: flagged (a label lifted or stuck at an angle?)
_PLATE = {}


def set_plate_dir(path):
    """Use the cam plate calibration (plate_calib.json and its templates) of this folder from now on: the one made on
    the site (the Predict page, backend/storage/gap_calib.py) or the default one above. Added 2026-11-24."""
    global PLATE_DIR
    PLATE_DIR = Path(path)
    _PLATE.clear()


def _plate_calib():
    if "calib" not in _PLATE:
        try:
            _PLATE["calib"] = json.loads((PLATE_DIR / "plate_calib.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _PLATE["calib"] = {}
    return _PLATE["calib"]


def _plate_tpl(view):
    if view not in _PLATE:
        c = _plate_calib().get(view) or {}
        _PLATE[view] = read_gray(PLATE_DIR / c["template"]) if c.get("template") else None
    return _PLATE[view]


def _feat(g, blur=0.0):
    """gradient magnitude (lighting changes it less than the grey levels), blurred: small shifts still match"""
    g = cv2.GaussianBlur(g, (0, 0), 1.5).astype(np.float32)
    m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return cv2.GaussianBlur(m, (0, 0), blur) if blur else m


def _rot(img, ang):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * s + w * c), int(h * c + w * s)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=0)


def _match(roi, tpl, scales, angles, ds, blur=2.0):
    """(score, scale, angle, x, y) of the best match of tpl in roi (both shrunk by ds for the search)."""
    I = _feat(cv2.resize(roi, None, fx=1 / ds, fy=1 / ds, interpolation=cv2.INTER_AREA), blur)
    best = (-1.0, 0.0, 0.0, 0, 0)
    for s in scales:
        t0 = cv2.resize(tpl, None, fx=s / ds, fy=s / ds, interpolation=cv2.INTER_AREA)
        for ang in angles:
            t = _feat(_rot(t0, ang), blur)
            if t.shape[0] >= I.shape[0] or t.shape[1] >= I.shape[1]:
                continue
            _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(I, t, cv2.TM_CCOEFF_NORMED))
            if v > best[0]:
                best = (float(v), float(s), float(ang), loc[0] * ds, loc[1] * ds)
    return best


TEAL_LOW = ((75, 100, 110), (75, 70, 60))   # the teal of the last (HSV low end); the second only when the first finds
# nothing: the dark pictures (2026-11-11 early morning, 9 of 429 sets of qc_defect_2) - tried first, so the other
# pictures keep the same search region as before


def last_top(bgr):
    """(x0, x1, y top) of the last (its teal part) in the upper 60% of the picture, or None."""
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(cv2.GaussianBlur(bgr, (5, 5), 0), cv2.COLOR_BGR2HSV)
    for low in TEAL_LOW:
        teal = cv2.inRange(hsv, low, (100, 255, 255))
        teal[int(H * 0.6):] = 0
        teal = cv2.morphologyEx(teal, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
        n, _, st, _ = cv2.connectedComponentsWithStats(teal)
        if n < 2:
            continue
        k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        x, y, w, _, area = st[k]
        if area > 0.01 * H * W:
            return int(x), int(x + w), int(y)
    return None


LABEL_PAD = (0.25, 0.25, 0.25, 0.8)   # the QR label around the QR code, in QR sides (left, top, right, bottom: the
# printed text under the code)


def hide_label(bgr, qr_px):
    """The picture with the QR label painted over (inpainted from around it): a label stuck on the cam plate
    (2026-11-14 on) covers part of it and lowers the match (qc_defect_2: below 0.6 22 -> 4 of 121 with it hidden,
    the plate size found the same, p90 0.5%). qr_px: the 4 QR corners in px."""
    H, W = bgr.shape[:2]
    q = np.asarray(qr_px, float)
    x0, y0, x1, y1 = q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()
    d = max(x1 - x0, y1 - y0)
    a = (max(0, int(x0 - LABEL_PAD[0] * d)), max(0, int(y0 - LABEL_PAD[1] * d)),
         min(W, int(x1 + LABEL_PAD[2] * d)), min(H, int(y1 + LABEL_PAD[3] * d)))
    if a[2] <= a[0] or a[3] <= a[1]:
        return bgr
    sx0, sy0, sx1, sy1 = max(0, a[0] - 40), max(0, a[1] - 40), min(W, a[2] + 40), min(H, a[3] + 40)
    m = np.zeros((sy1 - sy0, sx1 - sx0), np.uint8)
    m[a[1] - sy0:a[3] - sy0, a[0] - sx0:a[2] - sx0] = 255
    out = bgr.copy()
    out[sy0:sy1, sx0:sx1] = cv2.inpaint(bgr[sy0:sy1, sx0:sx1], m, 8, cv2.INPAINT_TELEA)
    return out


PLATE_S = (0.76, 1.24)   # the plate size against the template searched (2026-11-19, 240 pictures of both folders:
# 0.80-1.16 found; until then 0.66-1.42)
PLATE_ANG = 6.0          # +- degrees (found -4.3 .. 6.0)


def _arange(lo, hi, step):
    return np.arange(lo, hi + step / 2, step)


def _near(roi, b, tpl, extra):
    """the part of roi around a match b (score, s, ang, x, y): the template box at that size, plus a margin for the
    rotation (sin 6 deg ~ 0.1 of the width) and extra px. (part, x0, y0)"""
    th, tw = tpl.shape[:2]
    m = int(0.12 * tw * b[1]) + extra
    x0, y0 = max(0, int(b[3]) - m), max(0, int(b[4]) - m)
    x1, y1 = min(roi.shape[1], int(b[3] + tw * b[1]) + m), min(roi.shape[0], int(b[4] + th * b[1]) + m)
    return roi[y0:y1, x0:x1], x0, y0


def find_plate(bgr, view, tpl, label=None):
    """The cam plate in one picture against a template: {"score", "s", "ang", "box": [x0, y0, x1, y1] in px} or None
    (no last found). Searched just above the top of the last in three steps: sizes PLATE_S by 0.03 and +-PLATE_ANG
    degrees by 2 (shrunk 4x), then around the best by 0.01 / 0.5 deg (4x), then by 0.005 / 0.25 deg (2x).
    2026-11-19: 2.2 s -> 0.65 s a picture (before: the whole search region at every step, 0.66-1.42, 1 deg);
    the size found the same within 2% on 210 of 217 pictures where the plate is found, at most 3.2%.
    label: the QR corners (px) of a label on the last: painted over first (hide_label)."""
    lt = last_top(bgr)
    if lt is None:
        return None
    if label is not None:
        bgr = hide_label(bgr, label)
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    rx0, rx1 = max(0, lt[0] - int(0.25 * W)), min(W, lt[1] + int(0.25 * W))
    ry0, ry1 = max(0, lt[2] - int(0.20 * H)), min(H, lt[2] + int(0.06 * H))
    roi = gray[ry0:ry1, rx0:rx1]
    b = _match(roi, tpl, _arange(*PLATE_S, 0.03), _arange(-PLATE_ANG, PLATE_ANG, 2.0), 4)
    if b[0] < 0:
        return None
    for ds, s_half, s_step, a_half, a_step, extra in ((4, 0.03, 0.01, 1.0, 0.5, 16), (2, 0.01, 0.005, 0.5, 0.25, 24)):
        part, x0, y0 = _near(roi, b, tpl, extra)   # b and the part's corner in roi px
        f = _match(part, tpl, _arange(b[1] - s_half, b[1] + s_half, s_step), _arange(b[2] - a_half, b[2] + a_half, a_step), ds)
        if f[0] >= 0:
            b = (f[0], f[1], f[2], f[3] + x0, f[4] + y0)
    th, tw = tpl.shape[:2]
    return {"score": round(b[0], 3), "s": round(b[1], 4), "ang": round(b[2], 2),
            "box": [b[3] + rx0, b[4] + ry0, b[3] + rx0 + int(tw * b[1]), b[4] + ry0 + int(th * b[1])]}


def plate_scale(bgr, view, label=None):
    """The scale (mm/px at the side of the shoe) from the cam plate: {"mm_per_px", "score", "box" (0..1)} when found
    well enough, else {"why"}. label: the QR corners (0..1) of a label on the last (hidden for the match)."""
    c = _plate_calib().get(view)
    tpl = _plate_tpl(view)
    if not c or tpl is None:
        return {"why": "no cam plate calibration (plate/plate_calib.json: run plate_calib.py)"}
    H, W = bgr.shape[:2]
    f = find_plate(bgr, view, tpl, None if label is None else np.asarray(label, float) * [W, H])
    if f is None:
        return {"why": "the last (its teal part) is not found"}
    out = {"score": f["score"], "box": [round(f["box"][0] / W, 4), round(f["box"][1] / H, 4),
                                        round(f["box"][2] / W, 4), round(f["box"][3] / H, 4)]}
    if f["score"] < c["min_score"]:
        out["why"] = f"the cam plate is not found well (match {f['score']:.2f} < {c['min_score']})"
        return out
    out["mm_per_px"] = round(c["k"] / f["s"], 5)
    return out


def side_of(meta):
    """left / right: the camera on the label side of the shoe (an L shoe: left), by the QR value; None if unknown."""
    sid = str(meta.get("shoe_id") or "").strip().upper()
    return {"L": "left", "R": "right"}.get(sid[-1:]) if sid else None


NOQR_FX = (0.30, 0.45, 0.60)   # no QR: the window started at these parts of the shoe box (along the shoe) ...
NOQR_STEP = 0.4                # ... and every this many (virtual) QR sides from top to bottom of the box
NOQR_TRACES = 4                # different starts followed along the shoe (the longest line wins)
NOQR_MIN_INL = 0.4             # an 'unsure' start is a candidate with edges in this part of its window


def measure_noqr(gray, view, mmpx, box, limit=LIMIT_MM):
    """No QR on the shoe: the scale from the cam plate, the window started at a few places of the shoe box; a virtual
    QR (13 mm at that scale) stands for the label. Every start with edges (ok, or unsure with NOQR_MIN_INL of the
    columns) is a candidate; up to NOQR_TRACES different ones (the most columns first, a start on a line already
    followed is skipped) are followed along the shoe, and the one with the longest found line is measured: the edge
    of the upper on the midsole runs the whole shoe, the outsole at the toe or print on the upper only a part of it.
    (Until 2026-11-19 the start with the most columns in its window was taken: a scale 2% off changed it on ~10 of
    160 pictures.)"""
    H, W = gray.shape[:2]
    side = QR_MM / mmpx
    x0, y0, x1, y1 = (box[0] * W, box[1] * H, box[2] * W, box[3] * H) if box else (0.05 * W, 0.4 * H, 0.95 * W, 0.95 * H)
    d = 1 if view == "right" else -1
    cands = []
    for fx in NOQR_FX:
        # the window lies towards the heel of the virtual QR: put the QR so that the window is at fx of the box
        cx = x0 + fx * (x1 - x0) - d * (WIN_X[0] + WIN_X[1]) / 2 * side
        for cy in np.arange(max(y0, MIN_QR_Y * H), y1 - side, NOQR_STEP * side):
            h = side / 2
            pts = np.array([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]]) / [W, H]
            r = measure(gray, view, pts, limit)
            if r.get("status") == "ok" or (r.get("status") == "unsure" and "_seed" in r
                                           and r["inliers"] >= NOQR_MIN_INL * r["columns"]):
                cands.append((r, pts))
    if not cands:
        return {"status": "fail", "view": view, "why": "no QR on the shoe and no start with clear edges found"}
    cands.sort(key=lambda c: (c[0]["status"] != "ok", -c[0]["inliers"]))
    traced = []   # (found columns, trace, candidate)
    for r, pts in cands:
        xs, yr, _ = r["_seed"]
        if any(np.median(np.abs(np.interp(xs, tx, ty) - yr)) < 0.15 * side for _, (tx, ty), _ in traced):
            continue   # this start is on a line already followed
        tr = _trace(gray, side, r["_seed"], box, d)
        tx = np.array([p[0] for p in tr])
        traced.append((sum(p[3] for p in tr), (tx, np.array([p[1] for p in tr])), (r, pts, tr)))
        if len(traced) >= NOQR_TRACES:
            break
    r, pts, tr = max(traced, key=lambda t: t[0])[2]
    out = measure_full(gray, view, pts, box, limit, local=r, trace=tr)
    out["starts"] = len(traced)
    return out


COLOR_SAT = 50   # the upper is coloured when the median saturation (HSV, 0-255) of its middle is at least this:
# qc_defect_2 ON1WH-B-B (grey upper) 13-33, qc_defect ON1WH 21-38, ON1GR-L-B (lime upper, 2026-11-14) 65-86
VS_K = 2   # a coloured upper: the edges in V - VS_K * S (2026-11-19, ON1GR 60 sets x 2 sides: blue channel ok 105,
# unsure 11, fail 3, known Bonding_gap 3 of 4; V-2S ok 114, unsure 3, fail 2, 4 of 4; V-3S ok 117 but the values moved
# both ways on 30 pictures - not clearly better)


def edge_image(bgr, box):
    """The one-channel picture the edges are found in, and its name. A grey upper: the grey levels (as always). A
    coloured upper: V - VS_K*S ('vs': the white band light, the coloured upper dark whatever its lightness; until
    2026-11-18 the blue channel, which lost the pale lime near the sole on the right camera) - in grey a lime upper is about as light as the white band and the edge to it
    is lost (qc_defect_2 ON1GR: ok 10 of 60 in grey, of those 4 on wrong edges at 10-13 mm; blue 46, the edges where
    a person sees them; min(B,G,R) 42; red - where the lime is darkest by its mean - 22). The white band and the
    midsole are light in every channel. A new colour of upper (blue, red ...): check which channel shows its edge."""
    H, W = bgr.shape[:2]
    x0, x1 = (box[0] * W, box[2] * W) if box else (0.05 * W, 0.95 * W)
    y0 = max(box[1] * H if box else 0.0, 0.45 * H)
    reg = bgr[int(y0):int(0.65 * H), int(x0 + 0.3 * (x1 - x0)):int(x0 + 0.7 * (x1 - x0))]
    if reg.size and np.median(cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)[..., 1]) >= COLOR_SAT:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        vs = hsv[..., 2].astype(np.int16) - VS_K * hsv[..., 1].astype(np.int16)
        return np.clip(vs, 0, 255).astype(np.uint8), "vs"
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), "gray"


SIDES = ("left", "right")


def measure_both(js, limit=LIMIT_MM, pics=None):
    """The gap of both sides of a set: {"left": result, "right": result} (measure_set of each camera). The side with
    the QR on the shoe by the QR, the other (and both when the QR is on the last or not read) by the cam plate.
    The two sides at the same time (OpenCV lets the threads run together: about the time of one side)."""
    with ThreadPoolExecutor(len(SIDES)) as ex:
        return dict(zip(SIDES, ex.map(lambda v: measure_set(js, limit, view=v, pics=pics), SIDES)))


def measure_set(js, limit=LIMIT_MM, use_qr=True, view=None, pics=None):
    """The gap of one set (its json path). With a QR on the shoe: the picture of the camera that read it, the QR as
    the ruler (the cam plate scale beside it, as a check). Without (no QR position, read by another camera, or the
    label on the last): the camera on the label side (L shoe: left), the cam plate as the ruler.
    view: this camera (left / right) instead - by its QR when the QR on the shoe was read by it, else by the cam plate
    (no QR value needed then). use_qr=False: as if there were no QR (to check the cam plate way on sets with a QR).
    pics: {camera: picture path} (the app's captures); None: <id>_<camera>.jpg beside the json (a qc_export folder)."""
    try:
        meta = json.loads(Path(js).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"status": "fail", "view": view, "why": f"can not read the json: {e}"}
    want = view
    view, pts = qr_of(meta)
    on_shoe = use_qr and view in SIDES and pts[:, 1].mean() >= MIN_QR_Y and (want is None or view == want)
    no_qr_why = ("no QR position in the json" if view is None
                 else f"the QR was read by the {view} camera" if view not in SIDES
                 else "the QR label is on the last, not on the shoe" if pts[:, 1].mean() < MIN_QR_Y
                 else "the QR is on the other side" if want is not None and view != want
                 else "the QR left out (check)")
    if not on_shoe:
        view = want or side_of(meta)
        if view is None:
            return {"status": "none", "why": f"{no_qr_why}; the shoe side (L/R) is not known"}
    jpg = Path(pics[view]) if pics and pics.get(view) else Path(js).with_name(f"{Path(js).stem}_{view}.jpg")
    try:
        bgr = cv2.imdecode(np.fromfile(str(jpg), np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        bgr = None
    if bgr is None:
        return {"status": "fail", "view": view, "why": f"no {view} picture"}
    box = (((meta.get("views") or {}).get(view) or {}).get("pick") or {}).get("shoe", {}).get("box")
    gray, channel = edge_image(bgr, box)
    qv, qpts = qr_of(meta)   # a label on the last, seen by this camera: hidden for the plate match
    on_last = qv == view and qpts is not None and qpts[:, 1].mean() < MIN_QR_Y
    plate = plate_scale(bgr, view, qpts if on_last else None)
    if on_shoe:
        r = measure_full(gray, view, pts, box, limit)
        r["scale"] = "qr"
        if plate.get("mm_per_px") and r.get("mm_per_px"):
            diff = 100 * (plate["mm_per_px"] / r["mm_per_px"] - 1)
            r.update(plate_mm_per_px=plate["mm_per_px"], plate_diff_pct=round(diff, 1))
            if abs(diff) > PLATE_CHECK_PCT:
                r["check"] = f"the QR and the cam plate scale differ by {diff:+.0f}% (label lifted or at an angle?)"
    else:
        if not plate.get("mm_per_px"):
            return {"status": "none", "view": view, "why": f"{no_qr_why}; {plate['why']}", "plate": plate}
        r = measure_noqr(gray, view, plate["mm_per_px"], box, limit)
        r.update(scale="plate", no_qr=no_qr_why)
        r.pop("qr", None)
    r["plate"] = plate
    r["channel"] = channel
    return r


def draw(js, res, out_dir):
    img = cv2.imdecode(np.fromfile(str(Path(js).with_name(f"{Path(js).stem}_{res['view']}.jpg")), np.uint8), 1)
    H, W = img.shape[:2]
    P = lambda pts: (np.array(pts) * [W, H]).astype(np.int32)
    (xa, ya), (xb, yb) = P(res["window"])
    cv2.rectangle(img, (int(xa), int(ya)), (int(xb), int(yb)), (255, 0, 255), 2)
    for run in res.get("over", []):
        poly = [(x, yr) for x, yr, _ in run] + [(x, yg) for x, _, yg in reversed(run)]
        ov = img.copy()
        cv2.fillPoly(ov, [P(poly)], (0, 0, 255))
        img = cv2.addWeighted(ov, 0.45, img, 0.55, 0)
    if "max_line" in res:
        cv2.line(img, *[tuple(int(v) for v in pt) for pt in P(res["max_line"])], (0, 255, 255), 4)
    if "upper" in res:
        cv2.polylines(img, [P(res["upper"])], False, (0, 0, 255), 3)
        cv2.polylines(img, [P(res["midsole"])], False, (0, 255, 0), 3)
        if res.get("qr"):
            cv2.polylines(img, [P(res["qr"])], True, (255, 200, 0), 2)
    if res.get("mode") == "full":
        ys = [v[1] for v in res["upper"] + res["midsole"]]
        y0, y1 = max(0, int(min(ys) * H) - 500), min(H, int(max(ys) * H) + 400)
        crop = img[y0:y1]
    else:
        x0, y0 = max(0, int(min(xa, xb)) - 400), max(0, int(ya) - 400)
        crop = img[y0:int(yb) + 400, x0:int(max(xa, xb)) + 400]
    txt = f"max {res.get('gap_mm', '-')} mm, median {res.get('median_mm', '-')}, over {res.get('over_mm', 0)} mm, {res['status']}"
    cv2.putText(crop, txt, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 4)
    out = Path(out_dir) / f"{Path(js).stem}.jpg"
    cv2.imencode(".jpg", crop)[1].tofile(str(out))


def set_jsons(root, skip=("PoC",)):
    """[(json path, group, shoe, set)] of an unzipped qc_export folder, in name order: <group>/<shoe>/<set>/<id>.json,
    or <group>/<shoe>/<id>.json (exported without the pass folder: the set is the time part of the id)."""
    out = []
    for js in Path(root).glob("*/*/*/*.json"):
        if js.parts[-4] not in skip:
            out.append((js, js.parts[-4], js.parts[-3], js.parts[-2]))
    for js in Path(root).glob("*/*/*.json"):
        if js.parts[-3] not in skip:
            out.append((js, js.parts[-3], js.parts[-2], js.stem[-19:]))
    return sorted(out, key=lambda t: str(t[0]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="a qc_export folder (unzipped): <group>/<shoe>/<set>/<id>.json or <group>/<shoe>/<id>.json")
    ap.add_argument("--limit", type=float, default=LIMIT_MM, help=f"gap above this is a defect (default {LIMIT_MM} mm)")
    ap.add_argument("--draw", help="write a picture of each measurement to this folder")
    args = ap.parse_args()
    root = Path(args.folder)
    if args.draw:
        Path(args.draw).mkdir(parents=True, exist_ok=True)
    rows = []
    for js, group, _, _ in set_jsons(root):
        r = measure_set(js, args.limit)
        rows.append((group, js.stem, r))
        gap = (f"max {r['gap_mm']:5.2f} median {r['median_mm']:5.2f} over {r['over_mm']:5.1f} mm" if "over_mm" in r
               else f"{r['gap_mm']:5.2f} (window)" if "gap_mm" in r else "-")
        print(f"{group[:18]:18} {js.stem[-19:]} {r.get('view', '-'):5} {r['status']:6} {'DEFECT' if r.get('defect') else '':6} {gap:42} {r.get('why', '')}",
              flush=True)
        if args.draw and r.get("view") in ("left", "right") and "window" in r:
            draw(js, r, args.draw)
    n = {}
    for _, _, r in rows:
        n[r["status"]] = n.get(r["status"], 0) + 1
    print(f"{len(rows)} sets: {n}")


if __name__ == "__main__":
    sys.exit(main())
