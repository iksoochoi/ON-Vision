"""Sharpness of a captured frame, used to pick the best of a few frames around the capture moment.

Every frame that arrives gets a small luma copy (luma_small: the NV12 brightness plane shrunk 4x,
turned into the view orientation), kept next to it in the camera's frame ring. That is cheap enough
to do for every frame (no colour conversion of the full-size picture), and shrinking averages out
sensor noise, which would otherwise pass for sharpness. score() measures one such copy.

No depthai import: frames are duck-typed (getFrame / getWidth / getHeight), so this runs and is
tested without hardware."""

import cv2

from ..core.rotation import rotate_image

SHRINK = 4          # luma_small is 1/SHRINK of the frame in each direction
SCORE_SCALE = 1000  # only makes the numbers easy to read


def luma_small(frame, rotation=0):
    """Brightness plane of an NV12 frame, shrunk SHRINK times and rotated like the saved picture.
    None when the frame does not offer it (the frame is then simply not a pick candidate)."""
    try:
        h, w = frame.getHeight(), frame.getWidth()
        luma = frame.getFrame()[:h]   # NV12: the first h rows are the brightness plane
        small = cv2.resize(luma, (w // SHRINK, h // SHRINK), interpolation=cv2.INTER_AREA)
    except (AttributeError, TypeError, ValueError, cv2.error):
        return None
    return rotate_image(small, rotation)


def roi_from_box(xmin, ymin, xmax, ymax, pad=0.15):
    """A normalized box grown by `pad` of its size on every side (the shoe moves a little between
    the frames that are compared), clamped to the picture."""
    pw, ph = (xmax - xmin) * pad, (ymax - ymin) * pad
    return (max(0.0, xmin - pw), max(0.0, ymin - ph), min(1.0, xmax + pw), min(1.0, ymax + ph))


def score(gray, roi=None):
    """Relative sharpness of a luma image, or of roi=(xmin, ymin, xmax, ymax) in normalized
    coordinates: variance of the Laplacian over the squared mean brightness, so a frame that is
    simply brighter does not win. Higher = sharper. Only meaningful between frames of one camera
    at nearly the same moment."""
    if gray is None or gray.size == 0:
        return 0.0
    if roi is not None:
        h, w = gray.shape[:2]
        x0, y0, x1, y1 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            gray = gray[y0:y1, x0:x1]
    mean = float(gray.mean())
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    return float(lap.var()) / (mean * mean + 1.0) * SCORE_SCALE
