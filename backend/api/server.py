"""HTTP transport: static frontend + JSON REST endpoints + MJPEG preview + SSE events.

The routes live in the *_routes.py mixins; ControlHandler dispatches the requests to them."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ..camera.registry import registry
from ..core.config import CATEGORIES, CONTROL_PORT, HOST
from ..core.state import state, state_lock
from ..detect.background import get_background
from ..qr.hub import hub as qr_hub
from ..storage.paths import ID_RE
from ..storage.store import store
from . import feeds
from .export_routes import ExportRoutes
from .imagelab_routes import ImageLabRoutes
from .predict_routes import PredictRoutes
from .responses import STATIC_FILES, ResponsesMixin
from .sample_routes import SampleRoutes
from .state_routes import StateRoutes
from .stream_routes import StreamRoutes


class ControlHandler(ResponsesMixin, StateRoutes, SampleRoutes, ExportRoutes, PredictRoutes, ImageLabRoutes, StreamRoutes,
                     BaseHTTPRequestHandler):
    def _route(self):
        parts = urlsplit(self.path)
        return parts.path, parse_qs(parts.query)

    def _query_with_blanks(self):
        """The query like _route() gives it, but an empty value (?shoe=) is kept."""
        return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

    # ---- dispatch -----------------------------------------------------------

    def do_GET(self):
        path, query = self._route()
        if path in STATIC_FILES:
            self._serve_static(STATIC_FILES[path])
        elif path in ("/mjpeg", "/snapshot"):
            slot = registry.get(query.get("cam", [registry.reference_role])[0])
            if slot is None:
                self.send_error(404, "unknown camera")
            elif path == "/mjpeg":
                self._stream_mjpeg(slot)
            else:
                self._send_snapshot(slot, query)
        elif path == "/events":
            topics = [t for t in query.get("topics", [""])[0].split(",") if t]
            self._stream_events(topics or None)
        elif path == "/categories":
            self._send_json([{"group": g, "items": items} for g, items in CATEGORIES.items()])
        elif path == "/api/state":
            # ?light=1 skips the sample counts (they read every sample file): for frequent polling
            self._send_json(self._state_payload(light=query.get("light", ["0"])[0] == "1"))
        elif path == "/api/shoes":
            # ?limit=&q=&include=<shoe>: one page of the list + the totals of the whole list ({"shoes", "totals"});
            # without them, the whole list (older callers)
            args = self._query_with_blanks()
            if any(k in args for k in ("limit", "q", "include", "date")):
                try:
                    limit = max(0, int(args.get("limit", ["0"])[0]))
                except ValueError:
                    limit = 0
                extra = {"include": args["include"][0] or None} if "include" in args else {}   # "" = no QR value
                date = args.get("date", [""])[0] or None
                shoes, totals = store.shoes_page(limit, args.get("q", [""])[0], date=date, **extra)
                self._send_json({"shoes": shoes, "totals": totals, "dates": store.dates()})
            else:
                self._send_json(store.shoes())
        elif path == "/api/samples" and "shoe" in self._query_with_blanks():
            # ?shoe=<QR value>: the sets of one shoe, newest first; an empty value = no QR value was read
            args = self._query_with_blanks()
            self._send_json(store.by_shoe(args["shoe"][0] or None, args.get("date", [""])[0] or None))
        elif path == "/api/samples":
            status = query.get("status", ["all"])[0]
            try:
                limit = max(0, int(query.get("limit", ["0"])[0]))
                offset = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                limit = offset = 0
            self._send_json(store.list(status, limit, offset))
        elif path == "/api/background/image":
            role = query.get("role", [registry.reference_role])[0]
            jpg = get_background(role).image_jpg() if registry.get(role) else None
            if jpg is None:
                self.send_error(404)
            else:
                self._send_bytes(jpg, "image/jpeg")
        elif path == "/api/qr":
            self._send_json(qr_hub.snapshot())
        elif path == "/api/qr/daily":
            self._send_json(store.daily_qr())
        elif path == "/api/imagelab/methods":
            self._imagelab_methods()
        elif path == "/api/predict" or path.startswith("/api/predict/"):
            self._get_predict(path[len("/api/predict/"):], query)
        elif path.startswith("/exports/"):
            self._download_export(path[len("/exports/"):])
        elif path.startswith("/api/samples/"):
            self._get_sample(path[len("/api/samples/"):], query)
        else:
            self.send_error(404)

    def do_POST(self):
        path, _ = self._route()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            self.send_error(400, "invalid json")
            return
        if not isinstance(data, dict):
            self.send_error(400, "json object expected")
            return
        if path == "/api/auto":
            self._set_auto(data)
        elif path == "/api/capture_now":
            with state_lock:
                state["manual_capture"] = True
            self._send_json({"ok": True})
        elif path == "/api/focus":
            self._set_focus(data)
        elif path == "/api/cam":
            self._set_cam(data)
        elif path == "/api/background":
            role = data.get("role") or registry.reference_role
            if registry.get(role) is None:
                self.send_error(400, "unknown camera")
                return
            with state_lock:
                state["bg_capture_request"] = role
            self._send_json({"ok": True, "role": role})
        elif path == "/api/reference":
            restarted, err = registry.set_reference(data.get("role"))
            if err:
                self.send_error(400, err)
            else:
                self._send_json({"reference": registry.reference_role, "restarting": restarted})
        elif path == "/api/camera_config":
            restarted, err = registry.configure(data.get("role") or "all",
                                                data.get("preview"), data.get("capture"))
            if err:
                self.send_error(400, err)
            else:
                self._send_json({"restarting": restarted})
        elif path == "/api/stream":
            err = registry.set_stream(data.get("role") or "all", data.get("on", True))
            if err:
                self.send_error(400, err)
            else:
                self._send_json({"ok": True})
        elif path == "/api/rotation":
            err = registry.set_rotation(data.get("role") or "all", data.get("rotation"))
            if err:
                self.send_error(400, err)
            else:
                self._send_json({"ok": True})
        elif path == "/api/zoom":
            err = registry.set_zoom(data.get("role") or "all", data.get("zoom", 1.0), data.get("x"), data.get("y"))
            if err:
                self.send_error(400, err)
            else:
                self._send_json({"ok": True})
        elif path == "/api/bbox/auto":
            result, err = store.auto_bbox_all(data.get("scope", "unreviewed"))
            self._send_json_or_error(result, err)
        elif path == "/api/export":
            self._export(data)
        elif path == "/api/predict/export":
            self._export_predict(data)
        elif path == "/api/predict/zones/rebuild":
            self._rebuild_zones()
        elif path == "/api/imagelab/process":
            self._imagelab_process(data)
        elif path == "/api/imagelab/upload":
            self._imagelab_upload(data)
        elif path == "/api/samples/delete":
            ids = data.get("ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
                self.send_error(400, "ids: a list of sample ids")
                return
            self._send_json({"deleted": store.delete_many(ids)})
        elif path.startswith("/api/samples/") and path.endswith("/label"):
            self._set_label(path[len("/api/samples/"):-len("/label")], data)
        elif path.startswith("/api/samples/") and path.endswith("/bbox"):
            self._set_bbox(path[len("/api/samples/"):-len("/bbox")], data)
        elif path.startswith("/api/samples/") and path.endswith("/qr"):
            sample_id = path[len("/api/samples/"):-len("/qr")]
            if not ID_RE.match(sample_id):
                self.send_error(404)
                return
            meta, err = store.redecode_qr(sample_id)
            self._send_json_or_error(meta, err)
        else:
            self.send_error(404)

    def do_DELETE(self):
        path, _ = self._route()
        if not path.startswith("/api/samples/"):
            self.send_error(404)
            return
        parts = path[len("/api/samples/"):].split("/")
        sample_id = parts[0]
        if not ID_RE.match(sample_id):
            self.send_error(404)
            return
        if len(parts) == 2 and parts[1] == "label":
            ok = store.clear_label(sample_id)
        elif len(parts) == 1:
            ok = store.delete(sample_id)
        else:
            self.send_error(404)
            return
        if not ok:
            self.send_error(404)
            return
        self._send_json({"ok": True})


def _get_lan_ip():
    """Return the host's LAN IP without actually contacting anything."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def start_server():
    """Start the control HTTP server on a daemon thread; return the server handle."""
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((HOST, CONTROL_PORT), ControlHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    feeds.start()   # what is pushed over SSE (state, new sets, QR statistics)
    print("OAK Labeling UI:")
    print(f"  - 로컬:  http://localhost:{CONTROL_PORT}   (라벨링: /label)")
    lan_ip = _get_lan_ip() if HOST == "0.0.0.0" else None
    if lan_ip:
        print(f"  - LAN:   http://{lan_ip}:{CONTROL_PORT}")
    elif HOST != "0.0.0.0":
        print(f"  - 바인딩: {HOST} (ON_HOST) - 이 주소에서만 접속할 수 있습니다")
    return server
