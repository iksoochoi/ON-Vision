"""QR code reading (OpenCV only, no extra packages).

The shoe carries a QR code on its side. Two places read it:
  * capture time: every saved view is decoded, the result goes into the sample JSON
  * live: QrScanner looks at the newest full-size frame of each camera a few times per second,
    so the live page / QR page show the code as soon as it is visible (no shoe passing needed)

QrHub (hub.py) keeps the recent results for the UI; QrScanner (scanner.py) is the live scanner."""

import threading

import cv2

from ..core import config as C

MAX_SIDES = (1600, 2800)   # try the image shrunk to this long side, small first (fast), then larger


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
                return found
    return []


def decode_qr(img):
    """QR codes in a BGR (or gray) image: [{"text", "points": [[x, y] * 4, normalized 0..1]}].

    Tries a downscaled copy first, then a larger one, each with and without contrast stretching,
    because small or slightly blurred codes decode at some scales and not at others."""
    if img is None or img.size == 0:
        return []
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scales = []
    for side in MAX_SIDES:
        s = min(1.0, side / max(h, w))
        if s not in scales:
            scales.append(s)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    for s in scales:
        g = gray if s >= 1.0 else cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        gh, gw = g.shape[:2]
        for candidate in (g, clahe.apply(g)):
            found = _decode_once(candidate)
            if found:
                out, seen = [], set()
                for text, pts in found:
                    if text in seen:
                        continue
                    seen.add(text)
                    out.append({"text": text,
                                "points": [[round(float(x) / gw, 4), round(float(y) / gh, 4)] for x, y in pts]})
                return out
    return []


def qr_roles(roles):
    """`roles` narrowed to the cameras in ON_QR_CAMERAS (all of them when it is empty). A setting that
    names none of the configured cameras (a typo) is ignored, so the scan never switches off by mistake."""
    configured = [r for r, _ in C.CAMERAS]
    wanted = [r for r in C.QR_CAMERAS if r in configured]
    return [r for r in roles if r in wanted] if wanted else list(roles)


def read_qr(images):
    """Decode the views of the QR cameras (ON_QR_CAMERAS, default all): [{"view", "text", "points"}]
    (a code seen by several cameras is listed once per camera) and the distinct texts in reading order."""
    codes = []
    for role in qr_roles(list(images)):
        for code in decode_qr(images[role]):
            codes.append({"view": role, **code})
    texts = list(dict.fromkeys(c["text"] for c in codes))
    return codes, texts
