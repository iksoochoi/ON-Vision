"""QR code reading (OpenCV only, no extra packages).

The shoe carries a QR code on its side. Two places read it:
  * capture time: every saved view is decoded, the result goes into the sample JSON
  * live: QrScanner looks at the newest full-size frame of each camera a few times per second,
    so the live page / QR page show the code as soon as it is visible (no shoe passing needed)

QrHub (hub.py) keeps the recent results for the UI; QrScanner (scanner.py) is the live scanner.

Every code found carries size_px, its side in pixels of the picture it was found in: a code needs about
4 pixels per module (a 25..29 module code ~100..120 px) to be read. A code that could be located but not
read is reported through `seen` (sizes in px), so a log or the QR page can say "found, but only 58 px"."""

import threading

import cv2

from ..core import config as C

MAX_SIDES = (1600, 2800)   # try the image shrunk to this long side, small first (fast), then larger
ROI_SCALES = (1.0, 2.0)    # the search area is tried as it is, then enlarged (small codes)
ROI_MAX_SIDE = 4200        # an enlarged search area is not made longer than this
LIVE_ENHANCE_SEC = 0.25    # the most a live scan spends on the extra attempts (enhance.py)


_local = threading.local()  # cv2 detectors are not thread-safe: one set per thread


def _detectors():
    if not hasattr(_local, "dets"):
        dets = []
        aruco = getattr(cv2, "QRCodeDetectorAruco", None)  # more robust, OpenCV >= 4.8
        if aruco is not None:
            dets.append(aruco())
        dets.append(cv2.QRCodeDetector())
        _local.dets = dets
    return _local.dets


def _decode_once(gray):
    """[(text, 4x2 points in pixels)] for one grayscale image."""
    for det in _detectors():
        try:
            res = det.detectAndDecodeMulti(gray)
        except cv2.error:
            continue
        ok, texts, points = res[0], res[1], res[2]
        if not ok or points is None:
            continue
        found = [(t, p) for t, p in zip(texts, points) if t]
        if found:
            return found
    return []


def _size_px(pts):
    """Mean side length of a QR quad (4x2 points)."""
    return float(sum(((pts[i][0] - pts[(i + 1) % 4][0]) ** 2 + (pts[i][1] - pts[(i + 1) % 4][1]) ** 2) ** 0.5
                     for i in range(4)) / 4)


def _locate_quads(gray, scales):
    """Corner points (4x2, in `gray` pixels) of the QR codes the detector can place in the picture but that
    were not read. Only the position is searched, so this is cheap next to a failed read. [] if none."""
    for s in scales:
        g = gray if s == 1.0 else cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        for det in _detectors():
            try:
                ok, pts = det.detectMulti(g)[:2]
            except cv2.error:
                continue
            if ok and pts is not None and len(pts):
                return [p / s for p in pts]
    return []


def _locate(gray, scales):
    """Sizes (px, in `gray` pixels) of the codes _locate_quads places."""
    return [_size_px(p) for p in _locate_quads(gray, scales)]


def _search(gray, scales, origin, full_wh, seen=None, deep=False):
    """Decode `gray` (a part of a picture whose top-left is `origin`, the whole picture being
    `full_wh` = (w, h) pixels) at each scale, each with and without contrast stretching.
    Returns [{"text", "points" (0..1 of the whole picture), "size_px", "method"}] or []. Nothing read: the sizes
    of the codes that could only be located are added to `seen` (a list), when given.
    With ON_QR_ENHANCE the codes that were located but not read get the extra attempts of enhance.py; `deep`
    (a saved picture) also lets zxing-cpp search a picture in which nothing could be located."""
    ox, oy = origin
    fw, fh = full_wh
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    for s in scales:
        g = gray if s == 1.0 else cv2.resize(gray, None, fx=s, fy=s,
                                             interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        for candidate in (g, clahe.apply(g)):
            found = _decode_once(candidate)
            if found:
                out, texts = [], set()
                for text, pts in found:
                    if text in texts:
                        continue
                    texts.add(text)
                    out.append({"text": text,
                                "points": [[round((ox + float(x) / s) / fw, 4), round((oy + float(y) / s) / fh, 4)]
                                           for x, y in pts],
                                "size_px": round(_size_px(pts) / s, 1), "method": "cv"})
                return out
    quads = _locate_quads(gray, scales) if (seen is not None or deep) else []
    if C.QR_ENHANCE and (quads or deep):
        from . import enhance
        budget = C.QR_ENHANCE_SEC if deep else min(C.QR_ENHANCE_SEC, LIVE_ENHANCE_SEC)
        found = enhance.read(gray, quads, origin, full_wh, deep, budget)
        if found:
            return found
    if seen is not None:
        seen.extend(_size_px(p) for p in quads)
    return []


def _roi_pixels(shape, roi):
    h, w = shape[:2]
    x0, y0 = int(roi[0] * w), int(roi[1] * h)
    return x0, y0, min(w, x0 + max(16, int(roi[2] * w))), min(h, y0 + max(16, int(roi[3] * h)))


def decode_qr_tiles(img, grid=3, overlap=0.3, upscale=2):
    """Second chance for codes the whole-picture search misses (small ones): the picture is cut into
    overlapping tiles, each enlarged, and searched. Same result as decode_qr, the points are relative
    to the whole picture. Slower (a few tenths of a second), so only used when the first search failed."""
    if img is None or img.size == 0:
        return []
    h, w = img.shape[:2]
    th, tw = int(h / grid * (1 + overlap)), int(w / grid * (1 + overlap))
    for gy in range(grid):
        for gx in range(grid):
            y0 = min(h - th, int(gy * (h - th) / max(1, grid - 1)))
            x0 = min(w - tw, int(gx * (w - tw) / max(1, grid - 1)))
            tile = cv2.resize(img[y0:y0 + th, x0:x0 + tw], None, fx=upscale, fy=upscale,
                              interpolation=cv2.INTER_CUBIC)
            found = decode_qr(tile)
            if found:
                for code in found:   # tile-relative (0..1) -> whole picture (0..1)
                    code["points"] = [[round((x0 + px * tw) / w, 4), round((y0 + py * th) / h, 4)]
                                      for px, py in code["points"]]
                    code["size_px"] = round(code["size_px"] / upscale, 1)   # the tile was enlarged
                return found
    return []


def decode_qr(img, roi=None, roi_only=False, seen=None, deep=False):
    """QR codes in a BGR (or gray) image: [{"text", "points": [[x, y] * 4, normalized 0..1], "size_px"}].

    roi = (x, y, w, h) fractions of the picture: that area is searched first, as it is and then
    enlarged (small codes), and, unless roi_only, the whole picture after it. Without roi the whole
    picture is tried shrunk to a long side of MAX_SIDES, small first, then larger, each with and
    without contrast stretching, because small or slightly blurred codes decode at some scales and
    not at others. `seen`, `deep`: see _search."""
    if img is None or img.size == 0:
        return []
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    roi_seen = None
    if roi is not None:
        x0, y0, x1, y1 = _roi_pixels(gray.shape, roi)
        part = gray[y0:y1, x0:x1]
        scales = [s for s in ROI_SCALES if s == 1.0 or max(part.shape[:2]) * s <= ROI_MAX_SIDE] or [1.0]
        roi_seen = [] if seen is not None else None
        found = _search(part, scales, (x0, y0), (w, h), roi_seen, deep)
        if found:
            return found
        if roi_only:
            if seen is not None:
                seen.extend(roi_seen)
            return []
    scales = []
    for side in MAX_SIDES:
        s = min(1.0, side / max(h, w))
        if s not in scales:
            scales.append(s)
    whole_seen = [] if seen is not None else None
    found = _search(gray, scales, (0, 0), (w, h), whole_seen, deep and roi is None)   # with a search area the extra work stays there
    if not found and seen is not None:
        seen.extend(roi_seen or whole_seen)   # a code placed in the search area says the most
    return found


def qr_roles(roles):
    """`roles` narrowed to the cameras in ON_QR_CAMERAS (all of them when it is empty). A setting that
    names none of the configured cameras (a typo) is ignored, so the scan never switches off by mistake."""
    configured = [r for r, _ in C.CAMERAS]
    wanted = [r for r in C.QR_CAMERAS if r in configured]
    return [r for r in roles if r in wanted] if wanted else list(roles)


def read_qr(images, seen=None, deep=True):
    """Decode the views of the QR cameras (ON_QR_CAMERAS, default all): [{"view", "text", "points",
    "size_px"}] (a code seen by several cameras is listed once per camera) and the distinct texts in
    reading order. The search area is ON_QR_ROI first (when set), then the whole picture. deep: the extra
    attempts of enhance.py (ON_QR_ENHANCE) are on; the pictures of a saved shoe pass use it until one of them
    gave a code.
    `seen` (a dict), when given, gets {role: [size_px, ...]} of the cameras where a code could be
    located but not read."""
    codes = []
    for role in qr_roles(list(images)):
        located = [] if seen is not None else None
        for code in decode_qr(images[role], roi=C.QR_ROI, seen=located, deep=deep):
            codes.append({"view": role, **code})
        if located:
            seen[role] = located
    texts = list(dict.fromkeys(c["text"] for c in codes))
    return codes, texts
