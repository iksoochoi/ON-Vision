"""See the web pages (Predict, Gallery, Label ...) on a PC without cameras.

    python -m backend.cli.preview_pc                                   # some sets of the newest capture folder
    python -m backend.cli.preview_pc --source captures/capture_2026-10-24 --sets 20 --open

A few sets of --source are COPIED into a work folder (the source is only read, never changed), the defect worker predicts them
(the same one as the service: segmentation, background removal, defect model; it needs backend/predict/requirements.txt),
and the web server starts on 127.0.0.1 with the copy, without cameras: the Live page shows them as off. Ctrl+C stops it and
deletes the work folder (--keep leaves it). The service and the real captures folder are not touched: ON_CAPTURES_DIR, ON_PORT,
ON_HOST and ON_LOG_DIR are set for this run only (a copy needs about 40 MB per set)."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
MARKER = ".on_preview"   # in the work folder: only a folder made by this script is ever deleted
MB = 1024 * 1024


def find_source(arg):
    """The folder with the sets: --source, else captures/ when it has sets, else the newest captures/capture_* that has."""
    if arg:
        path = Path(arg).expanduser()
        path = path if path.is_absolute() else PROJECT_DIR / path
        if not (path / ".sets").is_dir():
            raise SystemExit(f"{path} has no .sets folder: it is not a captures folder")
        return path
    base = PROJECT_DIR / "captures"
    candidates = [base] + sorted(base.glob("capture_*"), reverse=True)
    for path in candidates:
        if (path / ".sets").is_dir() and any((path / ".sets").glob("*.json")):
            return path
    raise SystemExit(f"no sets found in {base}: give the folder with --source")


def pick_sets(source, count):
    """Ids of `count` sets spread over the whole folder (all of them when count is 0 or there are fewer)."""
    ids = sorted(p.stem for p in (source / ".sets").glob("*.json"))
    if count <= 0 or count >= len(ids):
        return ids
    step = len(ids) / count
    return [ids[int(i * step)] for i in range(count)]


def files_of(source, set_id):
    """(source file, path relative to the captures folder) of a set: its JSON, and the picture, JSON and thumbnail of every view."""
    out = [(source / ".sets" / f"{set_id}.json", Path(".sets") / f"{set_id}.json")]
    try:
        views = json.loads((source / ".sets" / f"{set_id}.json").read_text(encoding="utf-8")).get("views") or []
    except (OSError, ValueError):
        views = []
    for role in views:
        for rel in (Path(role) / f"{set_id}.jpg", Path(role) / f"{set_id}.json", Path(".thumbs") / role / f"{set_id}.jpg"):
            if (source / rel).exists():
                out.append((source / rel, rel))
    return out


def make_workdir(path, source):
    """A fresh, empty work folder. An older one is deleted only when this script made it (MARKER)."""
    path = path.resolve()
    source = source.resolve()
    if path == source or source in path.parents or path in source.parents:
        raise SystemExit(f"the work folder {path} and the source {source} must be separate folders")
    if path.exists():
        if not (path / MARKER).exists():
            raise SystemExit(f"{path} exists and was not made by this script: give another --work folder")
        shutil.rmtree(path)
    (path / "captures").mkdir(parents=True)
    (path / MARKER).write_text("made by backend.cli.preview_pc; safe to delete\n", encoding="utf-8")
    return path


def cleanup(work):
    if (work / MARKER).exists():
        shutil.rmtree(work, ignore_errors=True)


def port_in_use(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="the captures folder to copy sets from (default: captures/, else the newest captures/capture_*)")
    ap.add_argument("--sets", type=int, default=10, help="how many sets to copy, spread over the folder (0 = all; default 10)")
    ap.add_argument("--port", type=int, default=8095, help="the port of the preview (default 8095: not the service's 8090)")
    ap.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "on_preview"), help="the work folder (default: <temp>/on_preview)")
    ap.add_argument("--no-predict", action="store_true", help="do not run the defect worker (the Predict page stays empty)")
    ap.add_argument("--keep", action="store_true", help="keep the work folder when stopped")
    ap.add_argument("--open", action="store_true", help="open the Predict page in the browser")
    args = ap.parse_args()

    if port_in_use(args.port):
        raise SystemExit(f"port {args.port} is in use (the service? another preview?): give another --port")
    source = find_source(args.source)
    ids = pick_sets(source, args.sets)
    if not ids:
        raise SystemExit(f"{source} has no sets")
    plan = [(sid, files_of(source, sid)) for sid in ids]
    need = sum(src.stat().st_size for _, files in plan for src, _ in files)
    work = make_workdir(Path(args.work), source)
    try:
        free = shutil.disk_usage(work).free
        if free < need * 1.2 + 500 * MB:
            raise SystemExit(f"not enough disk space: the copy needs {need / MB:.0f} MB, {free / MB:.0f} MB are free (try --sets {max(1, len(ids) // 2)})")
        print(f"copying {len(ids)} sets of {source} ({need / MB:.0f} MB) to {work} ...")
        captures = work / "captures"
        for _, files in plan:
            for src, rel in files:
                (captures / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, captures / rel)

        # this run only; set before anything of backend/ is imported (the settings are read when it is imported)
        env = dict(os.environ, ON_CAPTURES_DIR=str(captures), ON_PORT=str(args.port), ON_HOST="127.0.0.1",
                   ON_LOG_DIR=str(work / "logs"), ON_PREDICT_MAX_AGE_HOURS="0", ON_PREDICT_MAX_BACKLOG="100000", PYTHONUNBUFFERED="1")
        os.environ.update({k: env[k] for k in ("ON_CAPTURES_DIR", "ON_PORT", "ON_HOST", "ON_LOG_DIR")})

        if not args.no_predict:
            print("predicting the copied sets (the first start loads the models) ...")
            code = subprocess.call([sys.executable, "-m", "backend.predict.worker", "--once"], cwd=PROJECT_DIR, env=env)
            if code != 0:
                print(f"the defect worker stopped with code {code} (are the packages of backend/predict/requirements.txt installed?): "
                      "the pages are shown without results")

        from backend.api.server import start_server
        server = start_server()
        url = f"http://127.0.0.1:{args.port}"
        print(f"\nPreview without cameras: {url}/predict   (also /browse, /label, /qr, /imagelab)\nCtrl+C stops it"
              + ("" if args.keep else f" and deletes {work}"))
        if args.open:
            import webbrowser
            webbrowser.open(url + "/predict")
        try:
            while True:   # a sleep loop, not Event().wait(): on Windows a wait without a timeout does not see Ctrl+C
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nstopping ...")
        server.shutdown()
        time.sleep(0.5)
    finally:
        if args.keep:
            print(f"the work folder stays: {work}")
        else:
            cleanup(work)


if __name__ == "__main__":
    main()
