"""Per-camera view rotation (90-degree steps, clockwise) and zoom crop, applied on the host.

The device always streams in sensor orientation; the rotation and the zoom crop are applied where
frames become images (preview, saved views, QR scan), so changing them needs no pipeline rebuild.
Everything downstream (detections, bbox, background reference) lives in the rotated view. Only the
autofocus region goes back to sensor coordinates (unrotate_point).

The zoom is a crop of the full-size frame, given as a normalized box (x0, y0, x1, y1) of the rotated
view. It keeps the sensor's pixels (nothing is interpolated), so a zoomed picture shows more detail
only as far as the capture size has more pixels than the picture is shown with.

No depthai import: frames are duck-typed, so this runs and is tested without hardware."""

from dataclasses import replace

import cv2
import numpy as np

ROTATIONS = (0, 90, 180, 270)
_CV_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate_image(img, rotation):
    code = _CV_ROTATE.get(rotation % 360)
    return img if code is None else cv2.rotate(img, code)


def rotate_box(xmin, ymin, xmax, ymax, rotation):
    """Normalized sensor-oriented box -> the same box in the rotated view."""
    r = rotation % 360
    if r == 90:
        return 1 - ymax, xmin, 1 - ymin, xmax
    if r == 180:
        return 1 - xmax, 1 - ymax, 1 - xmin, 1 - ymin
    if r == 270:
        return ymin, 1 - xmax, ymax, 1 - xmin
    return xmin, ymin, xmax, ymax


def rotate_dets(dets, rotation):
    """Det objects from the device (sensor orientation) -> rotated view."""
    if rotation % 360 == 0:
        return dets
    out = []
    for d in dets:
        xmin, ymin, xmax, ymax = rotate_box(d.xmin, d.ymin, d.xmax, d.ymax, rotation)
        out.append(replace(d, xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax))
    return out


def unrotate_point(x, y, rotation):
    """Normalized point clicked on the rotated view -> sensor orientation."""
    r = rotation % 360
    if r == 90:
        return y, 1 - x
    if r == 180:
        return 1 - x, 1 - y
    if r == 270:
        return 1 - y, x
    return x, y


def unrotate_box(box, rotation):
    """Normalized box of the rotated view -> the same area in sensor orientation."""
    x0, y0 = unrotate_point(box[0], box[1], rotation)
    x1, y1 = unrotate_point(box[2], box[3], rotation)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def crop_image(img, box):
    """The part of an image inside a normalized box (a view, not a copy)."""
    h, w = img.shape[:2]
    return img[int(round(box[1] * h)):int(round(box[3] * h)), int(round(box[0] * w)):int(round(box[2] * w))]


def _nv12_window(frame, box):
    """(NV12 array, x0, y0, x1, y1) of the pixels of a normalized sensor-orientation box, with even
    edges (the colour plane has half the rows, two bytes per pixel pair), or None when the frame
    is not a plain NV12 buffer."""
    try:
        h, w = int(frame.getHeight()), int(frame.getWidth())
        data = frame.getFrame()
    except (AttributeError, TypeError, ValueError):
        return None
    if getattr(data, "shape", None) != (h * 3 // 2, w):
        return None
    x0, y0 = int(round(box[0] * w)) & ~1, int(round(box[1] * h)) & ~1
    x1, y1 = min(w, (int(round(box[2] * w)) + 1) & ~1), min(h, (int(round(box[3] * h)) + 1) & ~1)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return data, h, x0, y0, x1, y1


def crop_bgr(frame, box):
    """BGR image of a normalized sensor-orientation box of an NV12 frame, converting only that part
    (a 2x zoom converts a quarter of the pixels). None when the frame offers no NV12 buffer."""
    win = _nv12_window(frame, box)
    if win is None:
        return None
    data, h, x0, y0, x1, y1 = win
    part = np.vstack((data[y0:y1, x0:x1], data[h + y0 // 2:h + y1 // 2, x0:x1]))
    return cv2.cvtColor(part, cv2.COLOR_YUV2BGR_NV12)


def crop_gray(frame, box):
    """Brightness image of a normalized sensor-orientation box of an NV12 frame, or None."""
    win = _nv12_window(frame, box)
    if win is None:
        return None
    data, _, x0, y0, x1, y1 = win
    return data[y0:y1, x0:x1]


class RotatedFrame:
    """Wraps a device frame so getCvFrame() returns the rotated (and zoom-cropped) image. Everything
    else (getWidth, getExposureTime, ...) is the original frame's, i.e. sensor orientation.
    crop = normalized box of the rotated view, or None for the whole view."""

    __slots__ = ("_frame", "rotation", "crop")

    def __init__(self, frame, rotation, crop=None):
        self._frame = frame
        self.rotation = rotation
        self.crop = crop

    def getCvFrame(self):
        if self.crop is not None:
            img = crop_bgr(self._frame, unrotate_box(self.crop, self.rotation))
            if img is not None:
                return rotate_image(img, self.rotation)
            return crop_image(rotate_image(self._frame.getCvFrame(), self.rotation), self.crop)
        return rotate_image(self._frame.getCvFrame(), self.rotation)

    def getGray(self):
        """Brightness image of the same view: no colour conversion when the frame is NV12."""
        box = unrotate_box(self.crop, self.rotation) if self.crop is not None else (0.0, 0.0, 1.0, 1.0)
        gray = crop_gray(self._frame, box)
        if gray is not None:
            return rotate_image(gray, self.rotation)
        return cv2.cvtColor(self.getCvFrame(), cv2.COLOR_BGR2GRAY)

    def __getattr__(self, name):
        return getattr(self._frame, name)


def wrap_frame(frame, rotation, crop=None):
    return frame if rotation % 360 == 0 and crop is None else RotatedFrame(frame, rotation, crop)


def view_gray(frame):
    """Brightness image of a frame object (a wrapped one, a device frame or a ready image); no
    colour conversion when the frame has an NV12 buffer."""
    if isinstance(frame, np.ndarray):
        return frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if hasattr(frame, "getGray"):
        return frame.getGray()
    gray = crop_gray(frame, (0.0, 0.0, 1.0, 1.0))
    return gray if gray is not None else cv2.cvtColor(frame.getCvFrame(), cv2.COLOR_BGR2GRAY)
