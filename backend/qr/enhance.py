"""Extra attempts for a QR code that the plain search (decode.py) could not read. Switch: ON_QR_ENHANCE
(0 = this module is not used at all and the reading is exactly what it was before it existed).

Three steps, each only tried when the one before found nothing, all within a time budget (ON_QR_ENHANCE_SEC):
  1. the code was LOCATED but not read (the usual case: blur, glare, a tilted label). Its place is known, so the
     picture is cut around it, the quad is straightened (perspective) and enlarged, and the result is read
     again in several versions: as it is, sharpened, deblurred along the belt (a horizontal Wiener filter at a
     few blur lengths), contrast stretched, binarised, brighter / darker (glare).
  2. every version is read by zxing-cpp as well as by OpenCV (ON_QR_ZXING, needs the zxing-cpp package; it copes
     with blur and tilt better than OpenCV).
  3. saved pictures only (deep=True): when nothing could even be located, zxing-cpp searches the picture itself.

Every code carries "method": which step read it (for example "crop+zxing:wiener6"), so a log or the QR page can
tell what the extra work pays."""

import time

import cv2
import numpy as np

from ..core import config as C

try:
    import zxingcpp
    _ZX_FORMAT = zxingcpp.BarcodeFormat.QRCode
except Exception:   # not installed: everything else still works
    zxingcpp = None
    _ZX_FORMAT = None

_warned = set()
MARGIN = 0.3            # the cut around a located code: this part of its side on every side
WIENER_BLUR_PX = (6, 9, 4, 12)   # blur lengths along x tried for a located code, in pixels of the saved picture
BLIND_BLUR_PX = (7, 8, 5, 9)   # blur lengths tried on a whole search area (each must be about right: 6 did not read a 7 px blur)
WIENER_K = 0.02         # noise term of the Wiener filter: higher = gentler


def zxing_ready():
    """True when zxing-cpp can be used (installed and switched on). Says why not, once."""
    if not C.QR_ZXING:
        return False
    if zxingcpp is None:
        if "zxing" not in _warned:
            _warned.add("zxing")
            print("[qr] zxing-cpp is not installed (.venv/bin/python -m pip install zxing-cpp): reading without it")
        return False
    return True


# ---- decoders -------------------------------------------------------------------------------------------------

def _zx_read(gray):
    """[(text, 4x2 corner points or None)] of zxing-cpp for one grayscale image."""
    out = []
    for binarizer in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
        try:
            results = zxingcpp.read_barcodes(gray, formats=_ZX_FORMAT, binarizer=binarizer)
        except Exception:
            continue
        for r in results:
            if not getattr(r, "valid", True) or not r.text:
                continue
            pos = getattr(r, "position", None)
            try:
                pts = np.array([[p.x, p.y] for p in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)],
                               dtype=np.float32)
            except Exception:
                pts = None
            out.append((r.text, pts))
        if out:
            return out
    return out


def _cv_read(gray):
    from .decode import _decode_once   # decode.py imports this module lazily: no cycle at import time
    return _decode_once(gray)


# ---- picture versions -------------------------------------------------------------------------------------------

def wiener_x(gray, length, k=WIENER_K):
    """Undo a horizontal motion blur of about `length` pixels (the belt moves the shoe sideways). One 1-D
    transform per row (fast even for a wide band), the length must be about right (+-1 px)."""
    length = max(2, int(round(length)))
    pad = length * 2
    w0 = gray.shape[1] + 2 * pad
    w = cv2.getOptimalDFTSize(w0)   # a size the transform is fast for
    g = cv2.copyMakeBorder(gray, 0, 0, pad, pad + (w - w0), cv2.BORDER_REPLICATE).astype(np.float32) / 255.0
    psf = np.zeros(w, np.float32)
    psf[:length] = 1.0 / length
    psf = np.roll(psf, -(length // 2))   # centred: the result does not shift
    H = np.fft.rfft(psf)
    out = np.fft.irfft(np.conj(H) / (np.abs(H) ** 2 + k) * np.fft.rfft(g, axis=1), n=w, axis=1)
    out = np.clip(out[:, pad:pad + gray.shape[1]], 0.0, 1.0)
    return (out * 255).astype(np.uint8)


def _gamma(gray, gamma):
    lut = (np.linspace(0, 1, 256) ** gamma * 255).astype(np.uint8)
    return cv2.LUT(gray, lut)


def _versions(base, upscale):
    """Lazily: (name, picture) versions of one picture, the likeliest to help first. `upscale`: how much the
    picture was enlarged against the saved one (blur lengths are given for the saved one)."""
    yield "plain", base
    yield "unsharp", cv2.addWeighted(base, 2.0, cv2.GaussianBlur(base, (0, 0), max(1.0, upscale * 1.2)), -1.0, 0)
    for blur in WIENER_BLUR_PX[:2]:
        yield f"wiener{blur}", wiener_x(base, blur * upscale)
    yield "clahe", cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4)).apply(base)
    yield "otsu", cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    yield "adaptive", cv2.adaptiveThreshold(base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8)
    for blur in WIENER_BLUR_PX[2:]:
        yield f"wiener{blur}", wiener_x(base, blur * upscale)
    yield "dark", _gamma(base, 1.7)
    yield "bright", _gamma(base, 0.6)


def _rectify(gray, quad, size):
    """The quad (4x2, the corners of the code) straightened to a square picture of `size` pixels; a quiet zone
    is taken from the real surroundings (the label's white edge)."""
    margin = size * 0.18
    inner = size - 2 * margin
    dst = np.array([[margin, margin], [margin + inner, margin], [margin + inner, margin + inner],
                    [margin, margin + inner]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), dst)
    return cv2.warpPerspective(gray, matrix, (size, size), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _bases(gray, quad):
    """The pictures made from one located code: straightened, and the plain cut, both enlarged. Lazily.
    Returns (name, picture, upscale)."""
    quad = np.asarray(quad, dtype=np.float32)
    side = max(8.0, float(np.mean([np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)])))
    size = int(min(720, max(240, side * 3.2)))
    yield "rect", _rectify(gray, quad, size), size * (1 - 0.36) / side
    h, w = gray.shape[:2]
    x0, y0 = np.floor(quad.min(axis=0) - side * MARGIN).astype(int)
    x1, y1 = np.ceil(quad.max(axis=0) + side * MARGIN).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 >= 8 and y1 - y0 >= 8:
        scale = min(4.0, max(2.0, 280.0 / side))
        yield "crop", cv2.resize(gray[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC), scale


# ---- the search -------------------------------------------------------------------------------------------------

def _code(text, quad, size, method, origin, full_wh):
    ox, oy = origin
    fw, fh = full_wh
    return {"text": text, "points": [[round((ox + float(x)) / fw, 4), round((oy + float(y)) / fh, 4)] for x, y in quad],
            "size_px": round(float(size), 1), "method": method}


def _size(quad):
    quad = np.asarray(quad, dtype=np.float32)
    return float(np.mean([np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)]))


def read_located(gray, quads, origin, full_wh, deadline):
    """Read the codes that were located (`quads`: 4x2 corner points in `gray` pixels, biggest first) in the
    versions above. Returns [code dicts] or [] when none could be read before the deadline."""
    use_zx = zxing_ready()
    for quad in sorted(quads, key=_size, reverse=True):
        size = _size(quad)
        for base_name, base, upscale in _bases(gray, quad):
            for version, img in _versions(base, upscale):
                if time.monotonic() > deadline:
                    return []
                tag = base_name if version == "plain" else f"{base_name}:{version}"
                readers = (("zxing", _zx_read), ("cv", _cv_read)) if use_zx else (("cv", _cv_read),)
                for reader_name, reader in readers:
                    found = reader(img)
                    if found:
                        return [_code(found[0][0], quad, size, f"{tag}+{reader_name}" if reader_name == "zxing" else tag,
                                      origin, full_wh)]
    return []


def read_blind(gray, origin, full_wh, deadline):
    """Nothing could be located (a code blurred along the belt is not even found by the detector): zxing-cpp looks
    for a code in the picture itself, then in the picture deblurred at a few blur lengths. Needs zxing-cpp (the
    OpenCV reader is too slow on a whole search area)."""
    if not zxing_ready():
        return []
    found = _blind_zxing(gray, origin, full_wh, deadline)
    for blur in BLIND_BLUR_PX:
        if found or time.monotonic() > deadline:
            break
        found = _blind_zxing(wiener_x(gray, blur), origin, full_wh, deadline, f"wiener{blur}+zxing", scales=(1.0,))
    return found


def _blind_zxing(gray, origin, full_wh, deadline, label="zxing", scales=(1.0, 2.0)):
    for scale in scales:
        if time.monotonic() > deadline or (scale > 1.0 and max(gray.shape[:2]) > 2500):   # not a 50 MP picture
            return []
        img = gray if scale == 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        found = _zx_read(img)
        if found:
            text, pts = found[0]
            if pts is None:
                pts = np.array([[0, 0], [img.shape[1], 0], [img.shape[1], img.shape[0]], [0, img.shape[0]]], np.float32)
            quad = pts / scale
            return [_code(text, quad, _size(quad), label if scale == 1.0 else label + "2x", origin, full_wh)]
    return []


def read(gray, quads, origin, full_wh, deep, budget_sec):
    """The extra attempts for one picture part. `quads`: the located codes (may be empty). deep: a saved picture
    (zxing may also search blind); live scans only retry what was located. Returns [code dicts] or []."""
    deadline = time.monotonic() + budget_sec
    found = read_located(gray, quads, origin, full_wh, deadline) if quads else []
    if not found and deep:
        found = read_blind(gray, origin, full_wh, deadline)
    return found
