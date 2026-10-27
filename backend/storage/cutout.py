"""The picture with the background removed: the shoe as the segmentation model outlined it, everything else flat grey.

Only OpenCV and numpy (no torch): the predict worker makes the picture the defect model looks at with it, and the web app
makes the same picture for the Predict page from the outline stored in the result (captures/.predict/<id>.json, views.<camera>.seg),
so the page shows what the model saw without a copy of the picture on the disk.

The outline is a polygon in fractions of the picture (0..1), so it fits the saved picture, its thumbnail and any other size."""

import cv2
import numpy as np

FILL = 114            # the grey (the same as the padding YOLO puts around a picture)
MAX_POINTS = 400      # the stored outline is simplified to about this many points


def polygon_from_pixels(points, width, height, max_points=MAX_POINTS):
    """The outline `points` (N x 2, pixels of a width x height picture) as fractions, simplified; None if it is not an area."""
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(pts) < 3:
        return None
    long_side = float(max(width, height))
    eps = 0.0005 * long_side
    contour = pts.reshape(-1, 1, 2)
    approx = cv2.approxPolyDP(contour, eps, True)
    while len(approx) > max_points:
        eps *= 1.5
        approx = cv2.approxPolyDP(contour, eps, True)
    approx = approx.reshape(-1, 2)
    if len(approx) < 3:
        return None
    return [[round(float(x) / width, 4), round(float(y) / height, 4)] for x, y in approx]


def mask_from_polygon(polygon, width, height, grow=0.0):
    """uint8 mask (height x width, 1 = shoe) of the outline; `grow`: widen it by this part of the long side."""
    pts = np.round(np.asarray(polygon, dtype=np.float32).reshape(-1, 2) * [width, height]).astype(np.int32)
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [pts], 1)
    px = int(round(grow * max(width, height)))
    if px > 0:
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1)))
    return mask


def cutout(image, polygon, grow=0.0):
    """`image` (BGR, any size) with everything outside the outline grey."""
    h, w = image.shape[:2]
    mask = mask_from_polygon(polygon, w, h, grow).astype(bool)
    out = np.full_like(image, FILL)
    out[mask] = image[mask]
    return out


def box_of(polygon):
    """(x0, y0, x1, y1) of the outline, fractions."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]


def area_of(polygon):
    """The part of the picture the outline covers (shoelace formula), 0..1."""
    pts = np.asarray(polygon, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)
