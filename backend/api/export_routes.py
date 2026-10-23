"""Routes for the YOLO dataset export and the download of its ZIP files."""

from ..core.config import EXPORTS_DIR
from ..storage.export import ZIP_RE, export_dataset
from ..storage.predict_export import ZIP_RE as PREDICT_ZIP_RE


class ExportRoutes:
    def _export(self, data):
        try:
            result = export_dataset(
                reviewed_only=bool(data.get("reviewed_only")),
                val_ratio=data.get("val_ratio", 0.2),
                include_negatives=data.get("include_negatives", True),
            )
        except (ValueError, TypeError) as e:
            self.send_error(400, str(e))
            return
        except OSError as e:
            self.send_error(500, str(e))
            return
        self._send_json(result)

    def _download_export(self, name):
        if not (ZIP_RE.match(name) or PREDICT_ZIP_RE.match(name)):
            self.send_error(404)
            return
        path = EXPORTS_DIR / name
        try:
            size = path.stat().st_size
            f = path.open("rb")
        except OSError:
            self.send_error(404)
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)
