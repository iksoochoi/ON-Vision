"""Routes for one sample: its data, pictures, label, bbox and QR."""

from ..storage import set_download
from ..storage.paths import ID_RE
from ..storage.store import store


class SampleRoutes:
    def _get_sample(self, rest, query):
        parts = rest.split("/")
        sample_id = parts[0]
        if not ID_RE.match(sample_id):
            self.send_error(404)
            return
        if len(parts) == 1:
            meta = store.get(sample_id)
            if meta is None:
                self.send_error(404)
            else:
                self._send_json(meta)
        elif len(parts) == 2 and parts[1] in ("image", "thumb"):
            path = store.view_file(sample_id, query.get("view", [""])[0], thumb=parts[1] == "thumb")
            if path is None:
                self.send_error(404)
                return
            try:
                data = path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")   # a picture never changes under its id
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def _download_sets(self, query):
        """?ids=<id>,<id>,...: the frames (pictures + saved data) as one zip, see storage/set_download.py."""
        ids = [i for i in query.get("ids", [""])[0].split(",") if i]
        try:
            tmp, name, size = set_download.build(ids)
        except set_download.DownloadError as e:
            self.send_error(e.code, str(e))
            return
        except OSError as e:
            self.send_error(500, f"could not make the zip: {e}")
            return
        with tmp:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while chunk := tmp.read(1 << 20):
                self.wfile.write(chunk)

    def _set_bbox(self, sample_id, data):
        """Body: {x,y,w,h} draw | {"confirm": true} | {"clear": true} | {"auto": true}."""
        if not ID_RE.match(sample_id):
            self.send_error(404)
            return
        if data.get("confirm"):
            meta, err = store.confirm_bbox(sample_id)
        elif data.get("clear"):
            meta, err = store.clear_bbox(sample_id)
        elif data.get("auto"):
            meta, err = store.auto_bbox(sample_id)
        else:
            meta, err = store.set_bbox(sample_id, data.get("x"), data.get("y"),
                                       data.get("w"), data.get("h"))
        self._send_json_or_error(meta, err)

    def _set_label(self, sample_id, data):
        if not ID_RE.match(sample_id):
            self.send_error(404)
            return
        defects = data.get("defects")
        if not isinstance(defects, list):
            defects = []
        views = data.get("views")
        if not isinstance(views, list):
            views = []
        meta, err = store.set_label(sample_id, data.get("status"), defects, data.get("note"),
                                    [v for v in views if isinstance(v, str)],
                                    apply_burst=bool(data.get("apply_burst")))
        if err == "not found":
            self.send_error(404)
        elif err:
            self.send_error(400, err)
        else:
            self._send_json(meta)
