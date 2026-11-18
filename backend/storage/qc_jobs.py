"""The QC Export page's work (one at a time, on a thread): the uploaded site QC workbook is read and matched with the
saved sets (a preview: per result / defect type the shoes found, sets, pictures, size), the "_QR only" summary workbook
is written next to it (needs openpyxl), then on request the zip of backend/cli/qc_export.py is made, with progress
and a cancel.

    exports/qc_sheets/<yyyymmdd_hhmmss>_<name>.xlsx              the uploaded workbook
    exports/qc_sheets/<yyyymmdd_hhmmss>_<name>_QR only.xlsx      the summary workbook (Summary / Shoes / Data)
    exports/qc_[defect_]<first day>_<last day>_<time>.zip        the zip (as the command line makes it)

The captures folder is only read."""

import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from ..cli import qc_export as X
from ..core import config as C
from ..core.state import state, state_lock
from . import qc_sheet as Q
from .paths import ID_RE, image_path, primary_role, qr_prefix, time_key

SHEETS_DIR = C.EXPORTS_DIR / "qc_sheets"
MAX_UPLOAD = 40 * 1024 * 1024
ZIP_RE = re.compile(r"^qc_(defect_)?\d{8}_\d{8}_\d{8}_\d{6}\.zip$")
FILE_RE = re.compile(r"^\d{8}_\d{6}_[^/\\]{1,150}\.xlsx$")
KEEP_FILES = 20            # the newest uploads / workbooks kept in qc_sheets (older ones are deleted)
NOT_FOUND_SHOWN = 300

_lock = threading.Lock()
_job = {"stage": "idle"}   # idle | reading | ready | exporting | done | cancelled | error
_data = {}                 # the shoes, the sets found ... of the sheet (not sent to the page)
_cancel = threading.Event()


def _safe_name(name):
    stem = Path(str(name or "sheet")).stem
    stem = re.sub(r"[^A-Za-z0-9 ._()~#-]+", "_", stem).strip(" ._") or "sheet"
    return stem[:100]


def _set(**kw):
    with _lock:
        _job.update(kw)


def _prune():
    files = sorted(SHEETS_DIR.glob("*.xlsx"), key=lambda p: p.name, reverse=True)
    stamps = []
    for p in files:
        s = p.name[:15]
        if s not in stamps:
            stamps.append(s)
    for p in files:
        if p.name[:15] not in stamps[:KEEP_FILES]:
            p.unlink(missing_ok=True)


def status():
    with _lock:
        out = dict(_job)
    with state_lock:
        out["auto_enabled"] = bool(state.get("auto_enabled"))
    try:
        out["free_bytes"] = shutil.disk_usage(C.EXPORTS_DIR if C.EXPORTS_DIR.exists() else C.CAPTURES_DIR).free
    except OSError:
        out["free_bytes"] = None
    out["zips"] = recent_zips()
    return out


def recent_zips(n=10):
    try:
        zips = [p for p in C.EXPORTS_DIR.iterdir() if ZIP_RE.match(p.name)]
    except OSError:
        return []
    zips.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "bytes": p.stat().st_size,
             "time": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")} for p in zips[:n]]


def busy():
    with _lock:
        return _job.get("stage") in ("reading", "exporting")


# ---- upload: read, match, summary workbook ----

def upload(name, raw):
    """Start reading an uploaded workbook. Returns an error text or None."""
    if not str(name or "").lower().endswith(".xlsx"):
        return "an .xlsx file is expected (the site QC workbook)"
    if not raw or len(raw) > MAX_UPLOAD:
        return f"the file is empty or bigger than {MAX_UPLOAD // (1024 * 1024)} MB"
    if raw[:2] != b"PK":
        return "not an .xlsx file (an .xlsx is a zip; save it from Excel as .xlsx)"
    if busy():
        return "busy: wait until the work in progress is done (or cancel it)"
    SHEETS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SHEETS_DIR / f"{stamp}_{_safe_name(name)}.xlsx"
    path.write_bytes(raw)
    _cancel.clear()
    with _lock:
        _job.clear()
        _job.update(stage="reading", step="reading the workbook", file=path.name, name=str(name), started=time.time())
        _data.clear()
    threading.Thread(target=_read, args=(path, str(name)), daemon=True).start()
    _prune()
    return None


def _summary_rows(P, per_type=False):
    rows = []
    if per_type:
        for t, (n_sh, n_fd, n_p) in sorted(P["per_type"].items(), key=lambda x: (-x[1][0], x[0])):
            rows.append({"key": t, "shoes": n_sh, "found": n_fd, "pictures": n_p})
    else:
        for f in X.FOLDER_ORDER:
            p = P["per"][f]
            if p["asked"]:
                rows.append({"key": f, "shoes": p["asked"], "found": p["found"], "sets": p["sets"],
                             "pictures": p["pictures"], "bytes": p["bytes"]})
    return rows


def _read(path, name):
    try:
        try:
            sheet = Q.process_master(path)
        except (ValueError, SystemExit) as e:
            raise ValueError(str(e)) from None
        shoes = X.with_folders(sheet["shoes"])
        _set(step=f"{len(shoes)} shoes: looking for their sets on this Mac")
        found, info = X.find_sets(shoes, False, C, ID_RE, time_key, cancel=_cancel.is_set)
        _set(step="counting the pictures")
        P_all = X.plan(shoes, found, False, {}, image_path, primary_role, qr_prefix)
        defect = [s for s in shoes if s["folder"] == "Defect"]
        multi = X.recorded_types(None, "", defect)
        P_def = X.plan(defect, found, True, multi, image_path, primary_role, qr_prefix) if defect else None
        found_map = {r["qr"]: (r["found"], r["sets"]) for r in P_all["rows"]}
        # the OK shoes found per QC day (for the OK sample of the defect zip) and the size of one: an estimate
        ok_days, ok_pics = {}, []
        for r in P_all["rows"]:
            if r["folder"] == "OK" and r["found"] == "yes":
                d = ok_days.setdefault(r["date"], {})   # {date: {model: found OK shoes}}
                d[r["model"]] = d.get(r["model"], 0) + 1
                ok_pics.append(r["pictures"])
        per_pic = P_all["total"] / P_all["pictures"] if P_all["pictures"] else 0
        c = Q.counts(shoes)
        models = {}
        for s in shoes:
            models[s["Model (QR)"]] = models.get(s["Model (QR)"], 0) + 1
        with _lock:
            _data.update(shoes=shoes, path=path, name=name, sheet=sheet)
        example = None   # one set as it goes in the zip, for the page's tree: <shoe>/<set>/<id>.json, <id>_<camera>.jpg
        if P_all["entries"]:
            folder, meta, files = P_all["entries"][0]
            parts = folder.split("/")
            example = {"shoe": parts[1], "set": parts[2], "id": meta["id"], "cameras": [r for r, _, _ in files]}
        preview = {
            "rows": len(sheet["data"]), "shoes": len(shoes), "days": c["days"], "models": models,
            "problems": sheet["problems"][:30], "problems_count": len(sheet["problems"]),
            "frames_scanned": info["frames_scanned"], "frames_without_qr": info["frames_without_qr"],
            "per_result": _summary_rows(P_all), "found": P_all["found"], "sets": P_all["sets"],
            "pictures": P_all["pictures"], "bytes": P_all["total"],
            "defect": None if P_def is None else {
                "per_type": _summary_rows(P_def, True), "shoes": P_def["asked"], "found": P_def["found"],
                "pictures": P_def["pictures"], "bytes": P_def["total_zip"], "multiple": len(multi),
                "copies_bytes": P_def["extra_bytes"]},
            "not_found": [{"qr": r["qr"], "date": r["date"], "result": r["result"], "type": r["defect_type"]}
                          for r in P_all["rows"] if r["found"] == "no"][:NOT_FOUND_SHOWN],
            "not_found_count": P_all["asked"] - P_all["found"],
            "example": example,
            "ok_days": ok_days, "ok_shoe_bytes": round(per_pic * (sum(ok_pics) / len(ok_pics))) if ok_pics else 0,
        }
        _set(step="writing the summary workbook", preview=preview)
        wb = path.with_name(f"{path.stem}_QR only.xlsx")
        try:
            Q.write_workbook(wb, name, sheet, found_map)
            _set(workbook=wb.name, workbook_error=None)
        except RuntimeError as e:   # no openpyxl
            _set(workbook=None, workbook_error=str(e))
        except OSError as e:
            _set(workbook=None, workbook_error=f"could not write the summary workbook: {e}")
        _set(stage="ready", step="", finished=time.time())
    except X.Cancelled:
        _set(stage="cancelled", step="")
    except ValueError as e:
        _set(stage="error", step="", error=str(e))
    except Exception as e:   # noqa: BLE001 - shown on the page
        _set(stage="error", step="", error=f"{type(e).__name__}: {e}")


# ---- export: the zip ----

def export(mode, results=None, any_day=False, set_folder=True, ok_per_day=0):
    """Start the zip. mode 'all' (results: a list of OK / Defect / Conflict / NoResult, empty = all) or 'defect'
    (ok_per_day > 0: also OK/ with that many OK shoes of each QC day); set_folder False: no folder per pass
    (<folder>/<shoe>/<id>_<camera>.jpg). Returns an error text or None."""
    if busy():
        return "busy: wait until the work in progress is done (or cancel it)"
    with _lock:
        if "shoes" not in _data:
            return "upload a QC workbook first"
        shoes = list(_data["shoes"])
        name = _data["name"]
    defect_only = mode == "defect"
    ok_per_day = max(0, min(100, int(ok_per_day or 0))) if defect_only else 0
    ok_pool = [s for s in shoes if s["folder"] == "OK"] if ok_per_day else []
    try:
        shoes = X.select(shoes, None if defect_only else (list(results or []) or None), defect_only)
    except SystemExit as e:
        return str(e)
    _cancel.clear()
    _set(stage="exporting", step="looking for the sets", export={"mode": mode, "results": results or [], "any_day": bool(any_day), "set_folder": bool(set_folder), "ok_per_day": ok_per_day,
                                                                  "done": 0, "total": 0, "bytes": 0, "total_bytes": 0,
                                                                  "started": time.time()}, error=None)
    threading.Thread(target=_export, args=(shoes, name, defect_only, bool(any_day), results, bool(set_folder),
                                           ok_pool, ok_per_day), daemon=True).start()
    return None


def _export(shoes, name, defect_only, any_day, results, set_folder=True, ok_pool=(), ok_per_day=0):
    out = None
    try:
        found, info = X.find_sets(shoes + list(ok_pool), any_day, C, ID_RE, time_key, cancel=_cancel.is_set)
        multi = X.recorded_types(None, "", shoes) if defect_only else {}
        ok_pick = X.pick_ok(ok_pool, found, ok_per_day, time_key) if ok_pool else []
        P = X.plan(shoes + ok_pick, found, defect_only, multi, image_path, primary_role, qr_prefix, set_folder)
        if not P["entries"]:
            raise ValueError("no sets found for these shoes: nothing to write")
        free = shutil.disk_usage(C.EXPORTS_DIR if C.EXPORTS_DIR.exists() else C.CAPTURES_DIR).free
        if free < P["total_zip"] * 1.02 + 200 * 1024 * 1024:
            raise ValueError(f"not enough free disk space for the zip ({X.fmt_bytes(P['total_zip'])} needed, "
                             f"{X.fmt_bytes(free)} free)")
        first, last = info["first"], info["last"]
        out = C.EXPORTS_DIR / f"qc_{'defect_' if defect_only else ''}{first}_{last}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        opts = {"result": "Defect" if defect_only else (",".join(results) if results else "all"),
                "defect_only": defect_only, "any_day": any_day, "limit": 0, "set_folder": set_folder, "ok_per_day": ok_per_day,
                "problems": [],
                "from": "QC Export page"}
        readme, manifest = X.make_texts(P, f"{name} [{Q.MASTER}]", opts, info, C.CAPTURES_DIR)
        with _lock:
            _job["export"].update(total=len(P["entries"]), total_bytes=P["total_zip"], shoes=P["found"], sets=P["sets"],
                                  ok_shoes=len(ok_pick))
            _job["step"] = "writing the zip"

        def progress(n, total, done):
            with _lock:
                _job["export"].update(done=n, bytes=done)
        X.write_zip(P, out, readme, manifest, progress, cancel=_cancel.is_set)
        with _lock:
            _job["export"].update(zip=out.name, zip_bytes=out.stat().st_size, finished=time.time())
            _job.update(stage="done", step="")
    except X.Cancelled:
        _set(stage="cancelled", step="")
    except ValueError as e:
        _set(stage="error", step="", error=str(e))
    except Exception as e:   # noqa: BLE001 - shown on the page
        _set(stage="error", step="", error=f"{type(e).__name__}: {e}")
    finally:
        with _lock:
            if _job.get("stage") in ("error", "cancelled"):
                _job["stage_after"] = "ready" if "shoes" in _data else "idle"


def cancel():
    _cancel.set()


def reset():
    """Back to the sheet's preview after an export ended (done / cancelled / error), keeping the sheet."""
    with _lock:
        if _job.get("stage") in ("done", "cancelled", "error") and "shoes" in _data:
            _job["stage"] = "ready"
            _job.pop("stage_after", None)


def sheet_file(name):
    """The path of an upload / summary workbook in qc_sheets by its file name, or None."""
    if not FILE_RE.match(name or ""):
        return None
    p = (SHEETS_DIR / name).resolve()
    return p if p.parent == SHEETS_DIR.resolve() and p.is_file() else None
