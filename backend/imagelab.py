"""Image Lab: OpenCV techniques applied to one picture, to look at what they do (the page /imagelab).

The first part is the CV Playground of train/CV_test (edges, blurs, thresholds, CLAHE, morphology, lines), the rest
are tools for this project: unsharp mask, horizontal deblur (the one the QR reading uses), small specks (top-hat /
black-hat), a motion blur check, a QR read test with the real reader, and the learned shoe zone of a camera.

METHODS describes every technique and its parameters (the page builds its controls from it); process() applies one.
Nothing here saves anything or touches the saved pictures: it only reads a picture and returns a new array."""

import time

import cv2
import numpy as np

from . import imagelab_about
from .core import config as C


def _num(key, label, lo, hi, default, step=1, kind="int"):
    return {"key": key, "label": label, "type": kind, "min": lo, "max": hi, "step": step, "default": default}


def _choice(key, label, options, default):
    return {"key": key, "label": label, "type": "select", "options": options, "default": default}


CAMERAS = ["top", "back", "left", "front", "right"]

METHODS = [
    {"id": "original", "label": "Original", "group": "Basic", "params": [],
     "help": "The picture as it is (after the crop and the size)."},
    {"id": "grayscale", "label": "Grayscale", "group": "Basic", "params": []},
    {"id": "invert", "label": "Invert", "group": "Basic", "params": []},
    {"id": "hsv", "label": "HSV (hue / saturation / value as colors)", "group": "Basic", "params": []},
    {"id": "histogram_equalization", "label": "Histogram equalization", "group": "Contrast", "params": []},
    {"id": "clahe", "label": "CLAHE (local contrast)", "group": "Contrast",
     "params": [_num("clipLimit", "Clip limit", 1, 10, 2.0, 0.5, "float"), _num("tileGridSize", "Tile grid", 2, 32, 8)]},
    {"id": "gaussian", "label": "Gaussian blur", "group": "Blur / denoise",
     "params": [_num("ksize", "Kernel (odd)", 1, 51, 7), _num("sigma", "Sigma (0 = from kernel)", 0, 20, 0, 0.5, "float")]},
    {"id": "median", "label": "Median blur", "group": "Blur / denoise", "params": [_num("ksize", "Kernel (odd)", 1, 51, 5)],
     "help": "Removes salt-and-pepper noise (high ISO) and keeps edges."},
    {"id": "bilateral", "label": "Bilateral filter", "group": "Blur / denoise",
     "params": [_num("d", "Diameter", 1, 25, 9), _num("sigmaColor", "Sigma color", 1, 250, 75), _num("sigmaSpace", "Sigma space", 1, 250, 75)],
     "help": "Smooths noise but keeps strong edges. Slow on big pictures: crop or scale down first."},
    {"id": "canny", "label": "Canny edges", "group": "Edges",
     "params": [_num("threshold1", "Threshold 1", 0, 500, 100), _num("threshold2", "Threshold 2", 0, 500, 200)]},
    {"id": "sobel", "label": "Sobel (gradient size)", "group": "Edges", "params": [_num("ksize", "Kernel (odd)", 1, 31, 3)]},
    {"id": "vertical_edges", "label": "Vertical edges (Sobel x)", "group": "Edges", "params": [_num("ksize", "Kernel (odd)", 1, 31, 3)],
     "help": "Edges that run up and down. A horizontal motion blur removes them first."},
    {"id": "horizontal_edges", "label": "Horizontal edges (Sobel y)", "group": "Edges", "params": [_num("ksize", "Kernel (odd)", 1, 31, 3)]},
    {"id": "laplacian", "label": "Laplacian (sharpness)", "group": "Edges",
     "params": [_num("ksize", "Kernel (odd)", 1, 31, 3), _num("gain", "Gain", 1, 20, 4)],
     "help": "Bright where the picture is sharp. The variance of this is the sharpness number of the app."},
    {"id": "contours", "label": "Contours (of Canny edges)", "group": "Edges",
     "params": [_num("threshold1", "Threshold 1", 0, 500, 100), _num("threshold2", "Threshold 2", 0, 500, 200)]},
    {"id": "hough_lines", "label": "Hough lines", "group": "Lines",
     "params": [_num("threshold1", "Canny 1", 0, 500, 50), _num("threshold2", "Canny 2", 0, 500, 150), _num("threshold", "Votes", 1, 500, 80),
                _num("minLineLength", "Min length", 1, 2000, 50), _num("maxLineGap", "Max gap", 0, 200, 10),
                _choice("orientation", "Direction", ["all", "horizontal", "vertical"], "all"), _num("angleTol", "Angle tolerance", 1, 45, 10, 1, "float")]},
    {"id": "morph_lines", "label": "Line extraction (morphology)", "group": "Lines",
     "params": [_num("length", "Length", 3, 500, 40), _choice("orientation", "Direction", ["horizontal", "vertical", "both"], "horizontal")]},
    {"id": "threshold", "label": "Threshold (fixed)", "group": "Threshold", "params": [_num("thresh", "Threshold", 0, 255, 127)]},
    {"id": "adaptive_threshold", "label": "Adaptive threshold", "group": "Threshold",
     "params": [_num("blockSize", "Block (odd)", 3, 151, 11), _num("C", "C", -30, 30, 2)]},
    {"id": "otsu", "label": "Otsu threshold", "group": "Threshold", "params": []},
    {"id": "morph_open", "label": "Morphology: open (after Otsu)", "group": "Threshold", "params": [_num("ksize", "Kernel", 1, 51, 5)]},
    {"id": "morph_close", "label": "Morphology: close (after Otsu)", "group": "Threshold", "params": [_num("ksize", "Kernel", 1, 51, 5)]},
    # ---- for this project ------------------------------------------------------------------------------------------
    {"id": "unsharp", "label": "Unsharp mask", "group": "Project tools",
     "params": [_num("radius", "Radius (px)", 0.5, 20, 3.0, 0.5, "float"), _num("amount", "Amount", 0.1, 5, 1.5, 0.1, "float")],
     "help": "One of the versions the QR reading tries on a code it could not read."},
    {"id": "deblur_x", "label": "Deblur along the belt (Wiener, horizontal)", "group": "Project tools",
     "params": [_num("length", "Blur length (px of the saved picture)", 2, 40, 7), _num("k", "Noise term", 0.001, 0.2, 0.02, 0.001, "float")],
     "full": True,
     "help": "What the QR reading does with 7, 8, 5 and 9 px. Works at the saved size: crop to the label first (a whole 12 MP picture is slow)."},
    {"id": "specks", "label": "Small specks (top-hat / black-hat)", "group": "Project tools",
     "params": [_num("size", "Speck size (px)", 3, 61, 15), _num("gain", "Gain", 1, 20, 5), _choice("kind", "Look for", ["both", "dark", "bright"], "both")],
     "help": "Bright where something small and dark or bright sits on a smooth surface (a candidate for dirt, a hole, a bubble). Logos, text and edges answer too."},
    {"id": "blur_check", "label": "Motion blur check (edge energy x / y)", "group": "Project tools", "params": [], "full": True,
     "help": "Red = vertical edges, green = horizontal edges. A motion blur along the belt erases the red first: x / y well below 1 means a horizontal blur."},
    {"id": "qr_read", "label": "QR read test (the app's reader)", "group": "Project tools", "params": [
        _choice("mode", "Reading", ["saved picture (all attempts)", "live scan (quick)", "plain (OpenCV only)"], "saved picture (all attempts)"),
        _choice("area", "Search area", ["ON_QR_ROI of the app", "the whole picture"], "ON_QR_ROI of the app")], "full": True,
     "help": "Reads the picture at its saved size with the same code as the app and says how (method, size in px). Shows the located codes."},
    {"id": "shoe_zone", "label": "Learned shoe zone of a camera", "group": "Project tools",
     "params": [_choice("camera", "Camera", CAMERAS, "left")],
     "help": "The zone the Predict page uses (see Predict > Show shoe zone). It exists after the Predict page made it."},
]
imagelab_about.attach(METHODS)   # what each technique does, how to read it, what it is for here (shown above the pictures)
BY_ID = {m["id"]: m for m in METHODS}


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _bgr(img):
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def _odd(v, lo=1, hi=151):
    v = int(round(v))
    v += 1 - v % 2
    return max(lo, min(hi, v))


def _val(m, p, key):
    """The value of one parameter, within its limits (the page cannot be trusted with the numbers)."""
    spec = next(s for s in m["params"] if s["key"] == key)
    raw = p.get(key, spec["default"])
    if spec["type"] == "select":
        return raw if raw in spec["options"] else spec["default"]
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(spec["default"])
    v = max(spec["min"], min(spec["max"], v))
    return int(round(v)) if spec["type"] == "int" else v


def apply(img, method, params, role=None):
    """(result picture BGR, info text) of one technique on a BGR picture."""
    m = BY_ID.get(method)
    if m is None:
        raise ValueError(f"unknown method: {method}")
    p = params if isinstance(params, dict) else {}
    v = lambda key: _val(m, p, key)
    info = ""
    if method == "original":
        out = img
    elif method == "grayscale":
        out = _bgr(_gray(img))
    elif method == "invert":
        out = cv2.bitwise_not(img)
    elif method == "hsv":
        out = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    elif method == "histogram_equalization":
        ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        ycc[:, :, 0] = cv2.equalizeHist(ycc[:, :, 0])
        out = cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR)
    elif method == "clahe":
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=v("clipLimit"), tileGridSize=(v("tileGridSize"),) * 2).apply(lab[:, :, 0])
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    elif method == "gaussian":
        out = cv2.GaussianBlur(img, (_odd(v("ksize")),) * 2, v("sigma"))
    elif method == "median":
        out = cv2.medianBlur(img, _odd(v("ksize"), 1, 51))
    elif method == "bilateral":
        out = cv2.bilateralFilter(img, v("d"), v("sigmaColor"), v("sigmaSpace"))
    elif method == "canny":
        out = _bgr(cv2.Canny(_gray(img), v("threshold1"), v("threshold2")))
    elif method in ("sobel", "vertical_edges", "horizontal_edges"):
        g, k = _gray(img), _odd(v("ksize"), 1, 31)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=k)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=k)
        mag = cv2.magnitude(gx, gy) if method == "sobel" else np.abs(gx if method == "vertical_edges" else gy)
        out = _bgr(np.clip(mag / (float(mag.max()) + 1e-9) * 255, 0, 255).astype(np.uint8))
    elif method == "laplacian":
        lap = cv2.Laplacian(_gray(img), cv2.CV_32F, ksize=_odd(v("ksize"), 1, 31))
        out = _bgr(np.clip(np.abs(lap) * v("gain"), 0, 255).astype(np.uint8))
    elif method == "contours":
        edges = cv2.Canny(_gray(img), v("threshold1"), v("threshold2"))
        found, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = img.copy()
        cv2.drawContours(out, found, -1, (0, 255, 0), 2)
        info = f"{len(found)} contours"
    elif method == "hough_lines":
        edges = cv2.Canny(_gray(img), v("threshold1"), v("threshold2"))
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, v("threshold"), minLineLength=v("minLineLength"), maxLineGap=v("maxLineGap"))
        out, n = img.copy(), 0
        orient, tol = v("orientation"), v("angleTol")
        for x1, y1, x2, y2 in (lines.reshape(-1, 4) if lines is not None else []):   # (N, 1, 4) before OpenCV 5, (N, 4) after
            ang = (np.degrees(np.arctan2(y2 - y1, x2 - x1)) + 180) % 180
            if orient == "horizontal" and not (ang <= tol or ang >= 180 - tol):
                continue
            if orient == "vertical" and not abs(ang - 90) <= tol:
                continue
            color = {"horizontal": (0, 255, 255), "vertical": (255, 0, 255)}.get(orient, (0, 255, 0))
            cv2.line(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            n += 1
        info = f"{n} lines"
    elif method == "morph_lines":
        g = _gray(img)
        bw = cv2.adaptiveThreshold(cv2.bitwise_not(g), 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
        n, orient = v("length"), v("orientation")
        pick = lambda kernel: cv2.dilate(cv2.erode(bw, kernel), kernel)
        h = pick(cv2.getStructuringElement(cv2.MORPH_RECT, (n, 1))) if orient in ("horizontal", "both") else None
        vl = pick(cv2.getStructuringElement(cv2.MORPH_RECT, (1, n))) if orient in ("vertical", "both") else None
        out = _bgr(cv2.bitwise_or(h, vl) if orient == "both" else (h if orient == "horizontal" else vl))
    elif method == "threshold":
        out = _bgr(cv2.threshold(_gray(img), v("thresh"), 255, cv2.THRESH_BINARY)[1])
    elif method == "adaptive_threshold":
        out = _bgr(cv2.adaptiveThreshold(_gray(img), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, _odd(v("blockSize"), 3, 151), v("C")))
    elif method == "otsu":
        thr, bw = cv2.threshold(_gray(img), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out, info = _bgr(bw), f"Otsu threshold {thr:.0f}"
    elif method in ("morph_open", "morph_close"):
        bw = cv2.threshold(_gray(img), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        op = cv2.MORPH_OPEN if method == "morph_open" else cv2.MORPH_CLOSE
        out = _bgr(cv2.morphologyEx(bw, op, np.ones((v("ksize"),) * 2, np.uint8)))
    elif method == "unsharp":
        blurred = cv2.GaussianBlur(img, (0, 0), v("radius"))
        out = cv2.addWeighted(img, 1 + v("amount"), blurred, -v("amount"), 0)
    elif method == "deblur_x":
        from .qr import enhance
        gray = _gray(img)
        out = _bgr(enhance.wiener_x(gray, v("length"), v("k")))
        info = f"horizontal blur of {v('length')} px removed (noise term {v('k')})"
        if max(gray.shape) > 3000:
            info += " (a big picture: crop to the label for speed)"
    elif method == "specks":
        g = _gray(img)
        size = _odd(v("size"), 3, 61)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        kind = v("kind")
        parts = []
        if kind in ("both", "dark"):
            parts.append(cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, kernel))
        if kind in ("both", "bright"):
            parts.append(cv2.morphologyEx(g, cv2.MORPH_TOPHAT, kernel))
        heat = np.clip(np.maximum.reduce(parts).astype(np.int32) * v("gain"), 0, 255).astype(np.uint8)
        out = cv2.applyColorMap(heat, cv2.COLORMAP_INFERNO)
    elif method == "blur_check":
        g = _gray(img).astype(np.float32)
        dx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        dy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        ratio = float(dx.mean()) / (float(dy.mean()) + 1e-9)
        scale = 255.0 / (float(np.percentile(np.maximum(dx, dy), 99.5)) + 1e-9)
        out = np.dstack([np.zeros_like(dx), np.clip(dy * scale, 0, 255), np.clip(dx * scale, 0, 255)]).astype(np.uint8)   # BGR: red = x, green = y
        verdict = "a horizontal blur is likely" if ratio < 0.8 else ("a vertical blur is likely" if ratio > 1.25 else "no clear direction")
        info = f"mean |dx| {dx.mean():.2f}, mean |dy| {dy.mean():.2f}, x / y = {ratio:.2f}: {verdict}"
    elif method == "qr_read":
        out, info = _qr_read(img, v("mode"), v("area"))
    elif method == "shoe_zone":
        out, info = _shoe_zone(img, v("camera") if role is None else role)
    else:
        raise ValueError(f"unknown method: {method}")
    return out, info


def _qr_read(img, mode, area):
    from .qr import decode
    roi = C.QR_ROI if area.startswith("ON_QR_ROI") else None
    started = time.time()
    kwargs = {"saved picture (all attempts)": {"seen": [], "deep": True}, "live scan (quick)": {"seen": []}}.get(mode, {})
    seen = kwargs.get("seen")
    codes = decode.decode_qr(img, roi=roi, **kwargs)
    took = time.time() - started
    out = img.copy() if img.ndim == 3 else _bgr(img)
    h, w = out.shape[:2]
    thick = max(2, w // 400)
    if roi:   # the search area
        x0, y0, x1, y1 = decode._roi_pixels(img.shape, roi)
        cv2.rectangle(out, (x0, y0), (x1, y1), (255, 200, 0), thick)
    for c in codes:
        pts = (np.array(c["points"]) * [w, h]).astype(np.int32)
        cv2.polylines(out, [pts], True, (0, 255, 0), thick)
    if codes:
        info = "; ".join(f"{c['text']} [{c.get('method')}, {c.get('size_px')} px]" for c in codes) + f" · {took:.2f} s"
    elif seen:
        info = f"not read; a code was located but could not be read (size {', '.join(str(round(s)) for s in seen)} px) · {took:.2f} s"
    else:
        info = f"not read, no code located · {took:.2f} s"
    return out, info


def _shoe_zone(img, camera):
    from .storage.zones import zones
    status = zones.status()["cameras"].get(camera)
    out = img.copy() if img.ndim == 3 else _bgr(img)
    if not status:
        return out, f"no shoe zone for {camera} yet (the Predict page makes it from the saved sets)"
    h, w = out.shape[:2]
    overlay = out.copy()
    for poly in status["polygons"]:
        pts = (np.array(poly) * [w, h]).astype(np.int32)
        cv2.fillPoly(overlay, [pts], (255, 200, 0))
        cv2.polylines(out, [pts], True, (255, 220, 0), max(2, w // 400))
    out = cv2.addWeighted(overlay, 0.18, out, 0.82, 0)
    return out, f"zone of {camera}: {round(status['area'] * 100)}% of the picture, made from {status['sets']} sets"


def process(img, method, params=None, max_side=1600, crop=None, role=None):
    """The whole job for the page: crop (fractions x, y, w, h of the picture), technique, scale down for the screen.
    Returns (picture BGR, info, (width, height) of the picture after the crop, ms). A technique marked "full" works at the
    saved resolution (a QR code or a blur is measured in saved pixels); the others work at the size shown."""
    started = time.time()
    m = BY_ID.get(method)
    if m is None:
        raise ValueError(f"unknown method: {method}")
    if crop:
        x, y, cw, ch = (float(c) for c in crop)
        if not (0 <= x < 1 and 0 <= y < 1 and cw > 0 and ch > 0):
            raise ValueError("crop: x, y, width, height as fractions of the picture")
        h, w = img.shape[:2]
        x0, y0 = int(x * w), int(y * h)
        img = img[y0:min(h, y0 + max(8, int(ch * h))), x0:min(w, x0 + max(8, int(cw * w)))]
    size = (img.shape[1], img.shape[0])
    work = img
    if not m.get("full") and max_side and max(size) > max_side:
        k = max_side / max(size)
        work = cv2.resize(img, (round(size[0] * k), round(size[1] * k)), interpolation=cv2.INTER_AREA)
    out, info = apply(work, method, params, role)
    if max_side and max(out.shape[:2]) > max_side:
        k = max_side / max(out.shape[:2])
        out = cv2.resize(out, (round(out.shape[1] * k), round(out.shape[0] * k)), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_NEAREST)
    return out, info, size, round((time.time() - started) * 1000)


def methods_for_page():
    return [{k: v for k, v in m.items() if k != "full"} for m in METHODS]
