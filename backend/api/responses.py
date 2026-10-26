"""Low-level replies shared by every route: JSON, bytes, JSON errors and the static frontend files."""

import gzip
import json
import threading

from ..core.config import FRONTEND_DIR

GZIP_MIN_BYTES = 1400          # smaller answers are not worth compressing
_static_cache = {}             # file name -> (mtime_ns, size, data, gzipped data, etag)
_static_lock = threading.Lock()

STATIC_FILES = {
    "/":                 ("page/live.html",    "text/html; charset=utf-8"),
    "/label":            ("page/label.html",   "text/html; charset=utf-8"),
    "/qr":               ("page/qr.html",      "text/html; charset=utf-8"),
    "/browse":           ("page/browse.html",  "text/html; charset=utf-8"),
    "/predict":          ("page/predict.html", "text/html; charset=utf-8"),
    "/imagelab":         ("page/imagelab.html", "text/html; charset=utf-8"),
    "/css/common.css":   ("css/common.css",    "text/css; charset=utf-8"),
    "/css/live.css":     ("css/live.css",      "text/css; charset=utf-8"),
    "/css/browse.css":   ("css/browse.css",    "text/css; charset=utf-8"),
    "/css/label.css":    ("css/label.css",     "text/css; charset=utf-8"),
    "/css/qr.css":       ("css/qr.css",        "text/css; charset=utf-8"),
    "/css/predict.css":  ("css/predict.css",   "text/css; charset=utf-8"),
    "/css/imagelab.css": ("css/imagelab.css",  "text/css; charset=utf-8"),
    "/js/live.js":       ("js/live.js",        "application/javascript; charset=utf-8"),
    "/js/label.js":      ("js/label.js",       "application/javascript; charset=utf-8"),
    "/js/qr.js":         ("js/qr.js",          "application/javascript; charset=utf-8"),
    "/js/browse.js":     ("js/browse.js",      "application/javascript; charset=utf-8"),
    "/js/predict.js":    ("js/predict.js",     "application/javascript; charset=utf-8"),
    "/js/imagelab.js":   ("js/imagelab.js",    "application/javascript; charset=utf-8"),
    "/js/welcome.js":    ("js/welcome.js",     "application/javascript; charset=utf-8"),
}


class ResponsesMixin:
    def send_error(self, code, message=None, explain=None):
        """API errors as JSON ({"error": ...}) so the frontend can show the message."""
        if not (self.path.startswith("/api/") or self.path.startswith("/exports/")):
            super().send_error(code, message, explain)
            return
        text = message or self.responses.get(code, ("error",))[0]
        body = json.dumps({"error": text}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _accepts_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "")

    def _send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        packed = len(data) >= GZIP_MIN_BYTES and self._accepts_gzip()
        if packed:   # the lists of sets compress to a tenth: the page loads faster, also over the network
            data = gzip.compress(data, compresslevel=4)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if packed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args, **_kwargs):
        pass

    def _send_json_or_error(self, obj, err):
        if err == "not found":
            self.send_error(404)
        elif err:
            self.send_error(400, err)
        else:
            self._send_json(obj)

    def _static_file(self, name):
        """(data, gzipped data, etag) of a frontend file, kept in memory and read again when the file changes."""
        path = FRONTEND_DIR / name
        st = path.stat()
        with _static_lock:
            cached = _static_cache.get(name)
            if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                return cached[2], cached[3], cached[4]
        data = path.read_bytes()
        packed = gzip.compress(data, compresslevel=6)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        with _static_lock:
            _static_cache[name] = (st.st_mtime_ns, st.st_size, data, packed, etag)
        return data, packed, etag

    def _serve_static(self, spec):
        """A page, style or script. The browser keeps it and asks again with the ETag: when nothing changed the
        answer is a 304 without a body; the text is sent compressed when the browser accepts it."""
        name, ctype = spec
        try:
            data, packed, etag = self._static_file(name)
        except OSError:
            self.send_error(404)
            return
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        use_gzip = self._accepts_gzip()
        body = packed if use_gzip else data
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)
