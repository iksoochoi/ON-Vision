"""Routes of the QC Export page (/qcexport): upload a site QC workbook, its preview, the zip, the files.

    GET  /api/qc/status                       the work (stage, preview, progress), the recent zips
    POST /api/qc/upload  {"name", "data"}     data: the .xlsx as base64 (or a data URL)
    POST /api/qc/export  {"mode": "all" | "defect", "results": [...], "any_day": bool, "set_folder": bool (default true),
                          "ok_per_day": int (defect: also OK/ with this many OK shoes of each QC day)}
    POST /api/qc/cancel  /  POST /api/qc/reset
    GET  /api/qc/file?name=<file>             an uploaded / summary workbook of exports/qc_sheets
    the zip: GET /exports/<qc_...zip> (export_routes)"""

import base64
import binascii

from ..storage import qc_jobs


class QcRoutes:
    def _qc_status(self):
        self._send_json(qc_jobs.status())

    def _qc_upload(self, data):
        text = data.get("data")
        if not isinstance(text, str) or not text:
            self.send_error(400, "data: the .xlsx file as base64")
            return
        try:
            raw = base64.b64decode(text.split(",", 1)[1] if text.startswith("data:") else text, validate=False)
        except (binascii.Error, ValueError):
            self.send_error(400, "data: not base64")
            return
        err = qc_jobs.upload(data.get("name"), raw)
        if err:
            self.send_error(400, err)
            return
        self._send_json(qc_jobs.status())

    def _qc_export(self, data):
        mode = data.get("mode", "all")
        results = data.get("results") or []
        if mode not in ("all", "defect") or not isinstance(results, list) or not all(isinstance(r, str) for r in results):
            self.send_error(400, "mode: all / defect, results: a list of OK / Defect / Conflict / NoResult")
            return
        try:
            ok_per_day = int(data.get("ok_per_day") or 0)
        except (TypeError, ValueError):
            self.send_error(400, "ok_per_day: a number")
            return
        err = qc_jobs.export(mode, results, bool(data.get("any_day")), data.get("set_folder", True) is not False, ok_per_day)
        if err:
            self.send_error(400, err)
            return
        self._send_json(qc_jobs.status())

    def _qc_cancel(self):
        qc_jobs.cancel()
        self._send_json(qc_jobs.status())

    def _qc_reset(self):
        qc_jobs.reset()
        self._send_json(qc_jobs.status())

    def _qc_file(self, query):
        name = query.get("name", [""])[0]
        path = qc_jobs.sheet_file(name)
        if path is None:
            self.send_error(404)
            return
        data = path.read_bytes()
        shown = name[16:] if len(name) > 16 else name   # without the yyyymmdd_hhmmss_ of the upload
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{shown}"')
        self.end_headers()
        self.wfile.write(data)
