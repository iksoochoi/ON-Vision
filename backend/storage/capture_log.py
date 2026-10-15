"""The capture log: one line in logs/capture.log for every capture that ran the image saving, automatic or
manual: whether each camera's picture was saved, how many pictures, which shoe, and how much has been
collected so far (this run, and everything saved on the disk).

A capture that could not be saved is logged too (DROPPED: the writer was backed up, SKIPPED: the
reference camera had no picture), so the log shows every request, not only the good ones.

Status of a line: SAVED (every picture of every camera), PARTIAL (some missing, the line says which camera
and why), FAILED (nothing was saved), DROPPED, SKIPPED. Written to ON_LOG_DIR (default logs/), at most
ON_CAPTURE_LOG_MAX_MB per file, 5 older files are kept. ON_CAPTURE_LOG=0 switches it off. Logging never
interferes with saving: a problem here is only printed."""

import json
import logging
import logging.handlers
import threading
from datetime import datetime

from ..core import config as C

BACKUPS = 5
_lock = threading.Lock()
_logger = None
_run = {"started": None, "captures": 0, "images": 0, "failed": 0, "dropped": 0, "skipped": 0, "shoes": set()}


def _get():
    """The file logger, created on first use (the folder is made then)."""
    global _logger
    if _logger is None:
        C.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            C.LOG_DIR / "capture.log", maxBytes=C.CAPTURE_LOG_MAX_MB * 1024 * 1024, backupCount=BACKUPS,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("on_capture_log")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        _logger = logger
    return _logger


def _write(status, text):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        _get().info(f"{now} {status:<7} {text}")
    except Exception as e:  # a full disk or a missing folder must not stop the capture
        print(f"[capture log] could not write: {e}")


def _qr_px_text(results):
    """How big the QR code was in the pictures: "right:130(read)" / "right:58(not-read)", the best of the
    cuts per camera; "-" when no camera placed a code (nothing found at all)."""
    best = {}
    for r in results:
        for role, (px, read) in (r.get("qr_px") or {}).items():
            if px > best.get(role, (0, False))[0]:
                best[role] = (px, read)
    return ",".join(f"{role}:{px}({'read' if read else 'not-read'})" for role, (px, read) in best.items()) or "-"


def _quote(text):
    return json.dumps(text, ensure_ascii=False)


def start(info):
    """A marker line when the app starts: the run counters begin here."""
    if not C.CAPTURE_LOG:
        return
    with _lock:
        _run.update(started=datetime.now(), captures=0, images=0, failed=0, dropped=0, skipped=0, shoes=set())
        _write("START", info)


def _report(call, *args):
    """Tell the collection log / alerts (backend/health/collect.py) about a capture that went wrong. This
    does not depend on ON_CAPTURE_LOG, and it never raises."""
    try:
        from ..health import collect
        getattr(collect, call)(*args)
    except Exception as e:
        print(f"[capture log] could not report to the collection log: {e}")


def event(status, source, text):
    """A capture that did not reach the disk: status DROPPED or SKIPPED."""
    _report("capture_lost", status, source, text.strip())
    if not C.CAPTURE_LOG:
        return
    with _lock:
        key = "dropped" if status == "DROPPED" else "skipped"
        _run[key] += 1
        _write(status, f"{source:<6} {text} | run: {_run_text()}")


def _run_text():
    return (f"captures={_run['captures']} images={_run['images']} failed_images={_run['failed']} "
            f"shoes={len(_run['shoes'])} dropped={_run['dropped']} skipped={_run['skipped']}")


def job(source, results, shoe, expected, took, totals, burst=None):
    """One capture that ran the image saving.

    results  one dict per cut (a manual capture has one, a shoe pass up to three):
             {"id", "saved": [roles with a saved picture], "missing": {role: reason}, "error": text or None}
    shoe     (text, source, others) of the QR value, or (None, None, [])
    expected the cameras that should each have a picture in every cut (all configured cameras)
    totals   what is saved now: {"sets", "cuts", "images", "shoes", "no_id_sets"}"""
    if not results:
        return
    cuts, per_camera, saved_total = len(results), [], 0
    cameras_ok = 0
    for role in expected:
        ok = sum(1 for r in results if role in r["saved"])
        saved_total += ok
        cameras_ok += ok == cuts
        why = []
        for r in results:
            reason = r["error"] and f"set failed: {r['error']}" or r["missing"].get(role)
            if role not in r["saved"] and reason and reason not in why:
                why.append(reason)
        per_camera.append(f"{role} {ok}/{cuts}" + (f" [{'; '.join(why)}]" if ok < cuts and why else ""))
    wanted = cuts * len(expected)
    status = "SAVED" if saved_total == wanted else ("FAILED" if saved_total == 0 else "PARTIAL")
    if status != "SAVED":
        _report("capture_problem", status, source, results, per_camera, burst)
    if not C.CAPTURE_LOG:
        return
    text, how, others = shoe
    shoe_text = f"shoe={_quote(text)}({how})" if text else "shoe=-"
    shoe_text += " qr_px=" + _qr_px_text(results)
    if others:
        shoe_text += f" other_codes={_quote(others)}"
    with _lock:
        _run["captures"] += 1
        _run["images"] += saved_total
        _run["failed"] += wanted - saved_total
        if text:
            _run["shoes"].add(text)
        head = (f"{source:<6} id={results[0]['id']}" + (f" pass={burst}" if burst else "")
                + f" cuts={cuts} cameras={cameras_ok}/{len(expected)} images={saved_total}/{wanted} {shoe_text}"
                + f" took={took:.1f}s")
        _write(status, f"{head} | {' '.join(per_camera) if len(per_camera) < 20 else ''}"
                       f" | run: {_run_text()}"
                       f" | total: sets={totals['sets']} cuts={totals['cuts']} images={totals['images']}"
                       f" shoes={totals['shoes']} sets_without_shoe_id={totals['no_id_sets']}")
