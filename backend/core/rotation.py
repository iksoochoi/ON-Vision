"""Per-camera view rotation in 90-degree steps (clockwise), applied on the host.

The device always streams in sensor orientation; the rotation is applied where frames become
images (preview, saved views, QR scan), so changing it needs no pipeline rebuild. Everything
downstream (detections, bbox, background reference) lives in the rotated view. Only the
autofocus region goes back to sensor coordinates (unrotate_point).

No depthai import: frames are duck-typed, so this runs and is tested without hardware."""

from dataclasses import replace

import cv2

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


class RotatedFrame:
    """Wraps a device frame so getCvFrame() returns the rotated image. Everything else
    (getWidth, getExposureTime, ...) is the original frame's, i.e. sensor orientation."""

    __slots__ = ("_frame", "rotation")

    def __init__(self, frame, rotation):
        self._frame = frame
        self.rotation = rotation

    def getCvFrame(self):
        return rotate_image(self._frame.getCvFrame(), self.rotation)

    def __getattr__(self, name):
        return getattr(self._frame, name)


def wrap_frame(frame, rotation):
    return frame if rotation % 360 == 0 else RotatedFrame(frame, rotation)
