"""Routes of the Image Lab page (see backend/imagelab.py): the techniques, an uploaded picture, and a technique applied to
a saved picture or an uploaded one. Nothing is saved: the pictures are only read."""

import base64
import secrets
import threading
from collections import OrderedDict

import cv2
import numpy as np

from .. import imagelab
from ..storage.paths import ID_RE
from ..storage.store import store

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 60_000_000
MAX_SIDE_LIMIT = 6000
KEEP_FILES, KEEP_UPLOADS = 3, 4

_lock = threading.Lock()
_files = OrderedDict()     # (path, mtime) -> decoded saved picture (the next change of a setting needs it again)
_uploads = OrderedDict()   # token -> decoded uploaded picture


def _remember(cache, key, value, keep):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > keep:
            cache.popitem(last=False)


def _decode(data):
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("this is not a picture the lab can read")
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise ValueError("the picture is too big")
    return img


def _saved_picture(sample_id, view):
    path = store.view_file(sample_id, view or "")
    if path is None:
        raise LookupError("no such picture")
    try:
        stamp = path.stat().st_mtime_ns
        key = (str(path), stamp)
        with _lock:
            img = _files.get(key)
            if img is not None:
                _files.move_to_end(key)
                return img
        img = _decode(np.fromfile(str(path), dtype=np.uint8).tobytes())
    except OSError:
        raise LookupError("the picture file cannot be read") from None
    _remember(_files, key, img, KEEP_FILES)
    return img


def _source(data):
    """(picture, camera) of the request: a saved picture {"id", "view"} or an uploaded one {"token"}."""
    token = data.get("token")
    if token:
        with _lock:
            img = _uploads.get(token)
            if img is not None:
                _uploads.move_to_end(token)
        if img is None:
            raise LookupError("the uploaded picture is gone: upload it again")
        return img, data.get("camera")
    sample_id = data.get("id")
    if not isinstance(sample_id, str) or not ID_RE.match(sample_id):
        raise ValueError("id: a set id, or token: an uploaded picture")
    view = data.get("view") or ""
    return _saved_picture(sample_id, view), view or None


class ImageLabRoutes:
    def _imagelab_methods(self):
        self._send_json({"methods": imagelab.methods_for_page(), "qr_roi": list(imagelab.C.QR_ROI) if imagelab.C.QR_ROI else None})

    def _imagelab_upload(self, data):
        """POST /api/imagelab/upload {"data": "data:image/...;base64,..."} -> {"token", "width", "height"}"""
        text = data.get("data")
        if not isinstance(text, str) or "," not in text:
            self.send_error(400, "data: a picture as a data URL")
            return
        try:
            raw = base64.b64decode(text.split(",", 1)[1], validate=False)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError("the picture is bigger than 40 MB")
            img = _decode(raw)
        except (ValueError, TypeError) as e:
            self.send_error(400, str(e))
            return
        token = secrets.token_hex(8)
        _remember(_uploads, token, img, KEEP_UPLOADS)
        self._send_json({"token": token, "width": int(img.shape[1]), "height": int(img.shape[0])})

    def _imagelab_process(self, data):
        """POST /api/imagelab/process {"id", "view"} | {"token"}, "method", "params", "max_side" (0 = the saved size),
        "crop" [x, y, w, h as fractions] -> {"image" (a data URL), "width", "height", "source_size", "info", "ms"}"""
        method = data.get("method", "original")
        if method not in imagelab.BY_ID:
            self.send_error(400, f"unknown method: {method}")
            return
        try:
            img, role = _source(data)
            try:
                max_side = int(data.get("max_side", 1600))
            except (TypeError, ValueError):
                max_side = 1600
            max_side = 0 if max_side <= 0 else min(max_side, MAX_SIDE_LIMIT)
            crop = data.get("crop")
            if crop is not None and not (isinstance(crop, list) and len(crop) == 4):
                raise ValueError("crop: [x, y, width, height]")
            out, info, size, ms = imagelab.process(img, method, data.get("params") or {}, max_side, crop, role)
        except LookupError as e:
            self.send_error(404, str(e))
            return
        except (ValueError, TypeError, cv2.error) as e:
            self.send_error(400, str(e))
            return
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            self.send_error(500, "could not encode the result")
            return
        self._send_json({"image": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii"),
                         "width": int(out.shape[1]), "height": int(out.shape[0]), "source_size": list(size),
                         "info": info, "ms": ms})
