"""Routes of the Predict page: the defect detection results the worker (backend/predict/worker.py) wrote."""

from ..storage import predictions
from ..storage.paths import ID_RE
from ..storage.store import store

FILTERS = ("all", "defect", "ok", "error")
MAX_LIMIT = 200


class PredictRoutes:
    def _get_predict(self, rest, query):
        """/api/predict?filter=all|defect|ok|error&cls=&date=&limit=&offset=  -> {"rows", "totals", "status"}
        /api/predict/<id>                                                      -> {"result", "views", "primary"}"""
        if not rest:
            flt = query.get("filter", ["all"])[0]
            try:
                limit = min(MAX_LIMIT, max(0, int(query.get("limit", ["60"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                limit, offset = 60, 0
            rows, totals = predictions.index.page(flt if flt in FILTERS else "all", query.get("cls", [""])[0] or None,
                                                  query.get("date", [""])[0] or None, limit, offset)
            self._send_json({"rows": rows, "totals": totals, "status": predictions.index.status()})
            return
        if not ID_RE.match(rest):
            self.send_error(404)
            return
        result = predictions.read_result(rest)
        if result is None:
            self.send_error(404, "no result for this set (yet)")
            return
        meta = store.get(rest) or {}
        primary = meta.get("primary_view")
        self._send_json({"result": result, "views": list(meta.get("views") or result.get("views") or []), "primary": primary})
