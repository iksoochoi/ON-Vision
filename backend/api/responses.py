"""Low-level replies shared by every route: JSON, bytes, JSON errors and the static frontend files."""

import json

from ..core.config import FRONTEND_DIR

STATIC_FILES = {
    "/":                 ("page/live.html",    "text/html; charset=utf-8"),
    "/label":            ("page/label.html",   "text/html; charset=utf-8"),
    "/qr":               ("page/qr.html",      "text/html; charset=utf-8"),
    "/browse":           ("page/browse.html",  "text/html; charset=utf-8"),
    "/css/common.css":   ("css/common.css",    "text/css; charset=utf-8"),
    "/css/live.css":     ("css/live.css",      "text/css; charset=utf-8"),
    "/css/browse.css":   ("css/browse.css",    "text/css; charset=utf-8"),
    "/css/label.css":    ("css/label.css",     "text/css; charset=utf-8"),
    "/css/qr.css":       ("css/qr.css",        "text/css; charset=utf-8"),
    "/js/live.js":       ("js/live.js",        "application/javascript; charset=utf-8"),
    "/js/label.js":      ("js/label.js",       "application/javascript; charset=utf-8"),
    "/js/qr.js":         ("js/qr.js",          "application/javascript; charset=utf-8"),
    "/js/browse.js":     ("js/browse.js",      "application/javascript; charset=utf-8"),
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

    def _send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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

    def _serve_static(self, spec):
        name, ctype = spec
        try:
            data = (FRONTEND_DIR / name).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)
