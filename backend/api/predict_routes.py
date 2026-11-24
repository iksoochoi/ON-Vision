"""Routes of the Predict page: the defect detection results the worker (backend/predict/worker.py) wrote."""

import time
import uuid
from datetime import datetime

import cv2

from ..core.config import PREDICT_CORRECT, PREDICT_SEG_GROW
from ..core.events import broadcast
from ..storage import cutout, gap_calib, predictions
from ..storage.paths import ID_RE
from ..storage.predict_export import export_predictions
from ..storage.zones import zones
from ..storage.store import store

FILTERS = ("all", "defect", "ok", "error")
MAX_LIMIT = 200


def _period(data):
    """(first, last) yyyy-mm-dd from {"from", "to"}, or raises ValueError with what is wrong."""
    first, last = str(data.get("from") or ""), str(data.get("to") or "")
    try:
        a, b = datetime.strptime(first, "%Y-%m-%d"), datetime.strptime(last, "%Y-%m-%d")
    except ValueError:
        raise ValueError("from / to: dates as yyyy-mm-dd") from None
    if a > b:
        raise ValueError("from is after to")
    return first, last


class PredictRoutes:
    def _get_job(self, query):
        """GET /api/predict/job?from=&to= -> {"sets", "with_result"} of that period (before asking for it), "job" (the
        request waiting / being worked on, or null) and "progress" (the worker's status of the current or last period)."""
        out = {"job": predictions.read_job(), "progress": predictions.read_status().get("job")}
        if query.get("from") or query.get("to"):
            try:
                out.update(predictions.period_counts(*_period({"from": query.get("from", [""])[0], "to": query.get("to", [""])[0]})))
            except ValueError as e:
                self.send_error(400, str(e))
                return
        self._send_json(out)

    def _start_job(self, data):
        """POST /api/predict/job. Body: {"from", "to" (yyyy-mm-dd, both days included), "redo": true = also the sets that
        already have a result}. The worker predicts the sets of that period (with a QR value) when no new set is waiting.
        One period at a time: 409 while one is asked for (cancel it first). -> {"job", "sets", "with_result"}"""
        try:
            first, last = _period(data)
        except ValueError as e:
            self.send_error(400, str(e))
            return
        if predictions.read_job() is not None:
            self.send_error(409, "a period is being predicted already: cancel it first")
            return
        job = {"id": uuid.uuid4().hex[:12], "from": first, "to": last, "redo": bool(data.get("redo")),
               "requested_at": time.time()}
        predictions.write_job(job)
        broadcast({"key": "predict_changed"})
        self._send_json({"job": job, **predictions.period_counts(first, last)})

    def _cancel_job(self, data):
        """POST /api/predict/job/cancel. Body: {"id"} (optional: only that request). The results made so far stay."""
        cancelled = predictions.clear_job(data.get("id") or None)
        if cancelled:
            broadcast({"key": "predict_changed"})
        self._send_json({"cancelled": cancelled})

    def _set_live(self, data):
        """POST /api/predict/live. Body: {"on": true | false}: the worker predicts the new sets the line saves (or not).
        -> {"live"}"""
        on = data.get("on")
        if not isinstance(on, bool):
            self.send_error(400, "on: true or false")
            return
        predictions.write_live(on)
        broadcast({"key": "predict_changed"})
        self._send_json({"live": on})

    # ---- the cam plate calibration of the bonding gap (storage/gap_calib.py): by hand, an estimated K ----
    HEALTH_SETS = 100

    def _get_calib(self):
        """GET /api/predict/calib -> {"in_use": [site | default, version], "active", "default", "candidate" (summaries or
        null), "request", "progress" (the worker's status "calib"), "health" (the cam plate in the latest results),
        "history"}"""
        rows, _ = predictions.index.page("all", None, None, self.HEALTH_SETS, 0, False)
        results = [r for r in (predictions.read_result(x["id"]) for x in rows) if r]
        active = gap_calib.read_calib(gap_calib.ACTIVE_DIR)
        candidate = gap_calib.read_calib(gap_calib.CANDIDATE_DIR)
        self._send_json({"in_use": list(gap_calib.in_use()),
                         "active": gap_calib.summary(active) if active else None,
                         "default": gap_calib.summary(gap_calib.read_calib(gap_calib.DEFAULT_DIR)),
                         "candidate": gap_calib.summary(candidate) if candidate else None,
                         "request": gap_calib.read_request(), "progress": predictions.read_status().get("calib"),
                         "health": gap_calib.health(results), "health_sets": len(results), "history": gap_calib.history()[:10],
                         "sets_used": gap_calib.CALIB_SETS})

    def _send_template(self, query):
        """GET /api/predict/calib/template?which=active|candidate|default|in_use&view=left|right -> the template (PNG)."""
        path = gap_calib.template_path(query.get("which", [""])[0], query.get("view", [""])[0])
        if path is None:
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _start_calib(self, data):
        """POST /api/predict/calib. Body: {"from", "to"} (yyyy-mm-dd): the worker makes a candidate from the pictures of
        that period. One at a time (409)."""
        try:
            first, last = _period(data)
        except ValueError as e:
            self.send_error(400, str(e))
            return
        if gap_calib.read_request() is not None:
            self.send_error(409, "a calibration is being made already: cancel it first")
            return
        n = len(predictions.period_ids(first, last))
        if n < gap_calib.MIN_GOOD:
            self.send_error(400, f"only {n} sets with a QR value in this period (at least {gap_calib.MIN_GOOD})")
            return
        req = {"id": uuid.uuid4().hex[:12], "from": first, "to": last, "requested_at": time.time()}
        gap_calib.write_request(req)
        broadcast({"key": "predict_changed"})
        self._send_json({"request": req, "sets": min(n, gap_calib.CALIB_SETS), "in_period": n})

    def _calib_action(self, action, data):
        """POST /api/predict/calib/cancel | apply | discard | default -> {"done": true | false}"""
        if action == "cancel":
            done = gap_calib.clear_request(data.get("id") or None)
        elif action == "apply":
            done = gap_calib.apply_candidate()
        elif action == "discard":
            done = gap_calib.discard_candidate()
        elif action == "default":
            done = gap_calib.use_default()
        else:
            self.send_error(404)
            return
        broadcast({"key": "predict_changed"})
        self._send_json({"done": done, "in_use": list(gap_calib.in_use())})

    def _rebuild_zones(self):
        """POST /api/predict/zones/rebuild: make the shoe zones again from the latest sets (in the background)."""
        started = predictions.index.rebuild_zones()
        self._send_json({"started": bool(started), **zones.status()})

    def _delete_predict(self, data):
        """POST /api/predict/delete. Body: {"ids": [set ids]} (these results) or {"all": true} (every result).
        Only the results go; the sets and their pictures stay. -> {"deleted": n}"""
        ids = data.get("ids")
        if data.get("all") is True and ids is None:
            n = predictions.delete_results(None)
        elif isinstance(ids, list) and ids and len(ids) <= 5000 and all(isinstance(i, str) and ID_RE.match(i) for i in ids):
            n = predictions.delete_results(ids)
        else:
            self.send_error(400, 'ids: a list of set ids, or {"all": true}')
            return
        if n:
            broadcast({"key": "predict_changed"})
        self._send_json({"deleted": n})

    def _export_predict(self, data):
        """POST /api/predict/export. Body: {"ids": [set ids]} or {"filter", "cls", "date"} (the list as the page shows it);
        "corrected": true | false (default: as the server starts), "originals": true | false.
        Saves the pictures with the boxes + result.json per set into exports/predict_<time>/ and a zip: the summary."""
        corrected = data.get("corrected")
        source = {}
        ids = data.get("ids")
        if ids is not None:
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
                self.send_error(400, "ids: a list of set ids")
                return
        else:
            flt = data.get("filter") if data.get("filter") in FILTERS else "all"
            cls, date = data.get("cls") or None, data.get("date") or None
            rows, _ = predictions.index.page(flt, cls, date, 0, 0, PREDICT_CORRECT if corrected is None else bool(corrected))
            ids = [r["id"] for r in rows]
            source = {"filter": flt, "cls": cls, "date": date}
        try:
            result = export_predictions(ids, corrected, bool(data.get("originals")), source)
        except ValueError as e:
            self.send_error(400, str(e))
            return
        except OSError as e:
            self.send_error(500, str(e))
            return
        self._send_json(result)

    def _send_cutout(self, sample_id, query):
        """GET /api/predict/<id>/cutout?view=<camera>&kind=thumb|image: the picture with the background removed, as the
        defect model saw it (made from the outline stored in the result; nothing is stored)."""
        role = query.get("view", [""])[0]
        thumb = query.get("kind", ["image"])[0] == "thumb"
        result = predictions.read_result(sample_id) if ID_RE.match(sample_id) else None
        seg = (((result or {}).get("views") or {}).get(role) or {}).get("seg")
        if not seg or not seg.get("found"):
            self.send_error(404, "no outline for this picture")
            return
        path = store.view_file(sample_id, role, thumb=thumb)
        image = cv2.imread(str(path)) if path is not None else None
        if image is None:
            self.send_error(404)
            return
        grow = ((result.get("seg") or {}).get("grow"))
        ok, data = cv2.imencode(".jpg", cutout.cutout(image, seg["polygon"], PREDICT_SEG_GROW if grow is None else grow),
                                [cv2.IMWRITE_JPEG_QUALITY, 88 if thumb else 92])
        if not ok:
            self.send_error(500)
            return
        data = data.tobytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _get_predict(self, rest, query):
        """/api/predict?filter=all|defect|ok|error&cls=&date=&limit=&offset=&corrected=1|0 -> {"rows", "totals", "status"}
        /api/predict/<id>                                                      -> {"result", "views", "primary"}
        /api/predict/<id>/cutout?view=<camera>&kind=thumb|image                -> the picture with the background removed (JPEG)"""
        if rest == "zones":
            self._send_json(zones.status())
            return
        if rest == "job":
            self._get_job(query)
            return
        if rest == "calib":
            self._get_calib()
            return
        if rest == "calib/template":
            self._send_template(query)
            return
        if rest.endswith("/cutout"):
            self._send_cutout(rest[:-len("/cutout")], query)
            return
        if not rest:
            predictions.index.ensure_zones()
            flt = query.get("filter", ["all"])[0]
            try:
                limit = min(MAX_LIMIT, max(0, int(query.get("limit", ["60"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                limit, offset = 60, 0
            corrected = query.get("corrected", ["1" if PREDICT_CORRECT else "0"])[0] != "0"
            rows, totals = predictions.index.page(flt if flt in FILTERS else "all", query.get("cls", [""])[0] or None,
                                                  query.get("date", [""])[0] or None, limit, offset, corrected)
            self._send_json({"rows": rows, "totals": totals, "status": predictions.index.status(), "job": predictions.read_job(),
                             "live": predictions.read_live()})
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
        self._send_json({"result": result, "views": list(meta.get("views") or result.get("views") or []), "primary": primary,
                         "context": predictions.context(result, meta)})
