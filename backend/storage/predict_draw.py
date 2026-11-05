"""Pictures with the boxes of a defect detection drawn on them (shared by predict_export.py and cli/predict_report.py;
it does not import the sample store, so a script can use it without starting the app's index)."""

import cv2
import numpy as np

RED, GREEN, GREY = (40, 40, 255), (90, 200, 60), (170, 170, 170)


def read_picture(path):
    """The picture as an array (also from a path with non-ASCII characters), or None."""
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None


def draw(image, detections, corrected, ok_classes, max_side):
    """The picture with the boxes drawn (a copy), scaled down to max_side (0 = as it is). detections: [{"class",
    "conf", "box": fractions, "ctx": {"counted", "reasons"}}]."""
    out = image.copy()
    h, w = out.shape[:2]
    thick = max(2, round(w / 700))
    scale = max(0.7, w / 1800)
    for d in detections:
        x0, y0, x1, y1 = (int(d["box"][0] * w), int(d["box"][1] * h), int(d["box"][2] * w), int(d["box"][3] * h))
        ctx = d.get("ctx") or {}
        left = corrected and ctx.get("counted") is False
        color = GREY if left else (GREEN if d["class"] in ok_classes else RED)
        t = max(1, thick // 3) if left else thick
        label = f"{d['class']} {d['conf']:.2f}" + (" - " + ", ".join(ctx.get("reasons") or []) if left and ctx.get("reasons") else "")
        cv2.rectangle(out, (x0, y0), (x1, y1), color, t)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, t - 1))
        top = max(y0 - th - base - 4, 0)
        cv2.rectangle(out, (x0, top), (x0 + tw + 8, top + th + base + 4), color, -1)
        cv2.putText(out, label, (x0 + 4, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), max(1, t - 1), cv2.LINE_AA)
    if max_side and max(h, w) > max_side:
        k = max_side / max(h, w)
        out = cv2.resize(out, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
    return out


def write_jpg(path, image):
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError(f"encode failed: {path}")
    path.write_bytes(buf.tobytes())
