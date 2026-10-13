"""Zoom of a camera view: a crop of the full-size frame (see backend/core/rotation.py).

The OAK-1 Lite has a fixed lens, so the zoom is a crop on the host. It is given as a factor
(1 = the whole view) and the center of the crop in the rotated view (0..1, 0.5 = middle); the crop
box is kept inside the view. No depthai import: runs and is tested without hardware."""

ZOOM_MIN = 1.0
ZOOM_MAX = 4.0
ZOOM_STEP = 0.1


def clamp_zoom(zoom):
    """The factor limited to ZOOM_MIN..ZOOM_MAX, in ZOOM_STEP steps."""
    zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(zoom)))
    return round(round(zoom / ZOOM_STEP) * ZOOM_STEP, 2)


def clamp_center(c):
    return max(0.0, min(1.0, float(c)))


def crop_box(zoom, cx=0.5, cy=0.5):
    """(x0, y0, x1, y1) normalized box of the rotated view that is shown at this zoom, or None when
    the whole view is shown (zoom 1)."""
    if zoom <= ZOOM_MIN + 1e-9:
        return None
    size = 1.0 / zoom
    x0 = max(0.0, min(1.0 - size, cx - size / 2))
    y0 = max(0.0, min(1.0 - size, cy - size / 2))
    return (x0, y0, x0 + size, y0 + size)
