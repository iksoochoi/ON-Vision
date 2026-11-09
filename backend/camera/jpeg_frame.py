"""A frame of the capture stream that the camera sent as JPEG (ON_CAPTURE_MJPEG=1, the camera's MJPEG encoder).

It looks like the NV12 frames to the rest of the app (getCvFrame, getWidth, getExposureTime, ... of the device
message), but keeps only the JPEG bytes and decodes on demand:
    luma_reduced(4)  a 1/4-size grey copy, decoded at that size by libjpeg (DCT scaling: cheap, for every frame)
    getGray()        the full-size grey picture (QR scan, sharpness of the QR band)
    getCvFrame()     the full-size colour picture (saving, zoomed preview)
A decoded picture is kept with the frame, so the picker and the writer do not decode the same frame twice.
There is no NV12 buffer (getFrame): the NV12 shortcuts of backend/core/rotation.py fall back to these.

No depthai import: the device message is duck-typed, so this runs and is tested without hardware."""

import cv2
import numpy as np

_REDUCED = {1: cv2.IMREAD_GRAYSCALE, 2: cv2.IMREAD_REDUCED_GRAYSCALE_2, 4: cv2.IMREAD_REDUCED_GRAYSCALE_4,
            8: cv2.IMREAD_REDUCED_GRAYSCALE_8}


class JpegFrame:
    __slots__ = ("_msg", "_data", "_size", "_bgr", "_gray")

    def __init__(self, msg):
        self._msg = msg
        self._data = np.array(msg.getData(), dtype=np.uint8, copy=True).ravel()   # its own copy: the message buffer may be reused
        try:
            w, h = int(msg.getWidth()), int(msg.getHeight())
        except (AttributeError, TypeError, ValueError):
            w = h = 0
        self._size = (w, h) if w > 0 and h > 0 else None
        self._bgr = None
        self._gray = None

    def _decode(self, flag):
        img = cv2.imdecode(self._data, flag)
        if img is None:
            raise RuntimeError("the camera's JPEG could not be decoded")
        return img

    def getCvFrame(self):
        if self._bgr is None:
            self._bgr = self._decode(cv2.IMREAD_COLOR)
            self._size = (self._bgr.shape[1], self._bgr.shape[0])
        return self._bgr

    def getGray(self):
        if self._gray is None:
            self._gray = (cv2.cvtColor(self._bgr, cv2.COLOR_BGR2GRAY) if self._bgr is not None
                          else self._decode(cv2.IMREAD_GRAYSCALE))
        return self._gray

    def luma_reduced(self, shrink):
        """Grey copy at 1/shrink of the size (shrink 1, 2, 4 or 8), or None when the JPEG is broken."""
        img = cv2.imdecode(self._data, _REDUCED[shrink])
        return img

    def jpeg(self):
        """The JPEG bytes as the camera sent them."""
        return self._data

    def getWidth(self):
        if self._size is None:
            self.getGray()
            self._size = (self._gray.shape[1], self._gray.shape[0])
        return self._size[0]

    def getHeight(self):
        self.getWidth()
        return self._size[1]

    def getFrame(self):
        raise AttributeError("a JPEG frame has no NV12 buffer")

    def __getattr__(self, name):   # getTimestamp, getExposureTime, getSensitivity, getLensPosition, ...
        return getattr(self._msg, name)
