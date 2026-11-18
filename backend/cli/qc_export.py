"""The saved sets of the shoes in a site QC sheet (OK / Defect from the line), as one zip with a folder per result.

    python -m backend.cli.qc_export --xlsx "captures/CTC_LS Shoes QC_raw data (11.10~11.14).xlsx" --dry-run
    python -m backend.cli.qc_export --xlsx "captures/CTC_LS Shoes QC_raw data (11.05~09)_QR only.xlsx"
    python -m backend.cli.qc_export --xlsx ... --result Defect,Conflict      # only these results
    python -m backend.cli.qc_export --xlsx ... --any-day                     # also sets of other days than the QC day
    python -m backend.cli.qc_export --xlsx ... --defect-only --dry-run       # only Defect shoes, a folder per defect type
    python -m backend.cli.qc_export --xlsx ... --dry-run --csv found.csv     # also the found / not found of every shoe

The QC Export page of the app (/qcexport) does the same from an uploaded site workbook (backend/storage/qc_jobs.py).

The sheet: the site QC workbook as it comes (sheet 'Master data_cell 1': one row per inspection; made into one row per
shoe by backend/storage/qc_sheet.py), or the 'Shoes' sheet of a "_QR only" workbook (one row per shoe: Date, QR,
Result, Defect type ...), or a .csv with the same columns. A shoe is found by its QR value exactly as the line recorded
it ('#' and all), compared with the QR value saved in the set (shoe_id). Only the sets of the day in the Date column
are taken (--any-day: every day). Sets saved without a QR value (no-id) can not be matched and are not in it.

    exports/qc_<first day>_<last day>_<yyyymmdd_hhmmss>.zip
        README.txt
        labels.csv                    one row per shoe of the sheet: result, defect type, found or not, sets, pictures
        manifest.json                 the sheet, the options, counts, the shoes not found
        <Result>/<shoe>/<set>/<id>.json             the data of one frame as it is saved
        <Result>/<shoe>/<set>/<id>_<camera>.jpg     the pictures of that frame, as saved (full size)

<Result> is OK, Defect, Conflict (the line recorded the QR on several rows, with both OK and Defect) or NoResult (no
result recorded). <shoe> is the QR value as it goes in file names ('#' becomes '-'), <set> the id of the shoe's pass.

--defect-only: only the Defect shoes, in exports/qc_defect_<first day>_<last day>_<time>.zip, with a folder per defect type
(the 'Defect type' column) in place of the result folder: Bonding/<shoe>/<set>/..., Over_spray/..., Unknown_type/... (no
type recorded). A shoe the line recorded with several types ('Multiple types') is in the folder of EACH of them (its sets
copied into each; labels.csv 'types' and 'path' list them all): the types come from the 'Types recorded' column of the
sheet ('; ' between them) or, without it, from the rows of the shoe's QR in the workbook's 'Data' sheet (its 'Defect type'
column). Without either it stays in Multiple_types/. Conflict shoes are not in it (the rows disagree on OK / Defect): add
them with a second run, --result Conflict.

--csv <file>: also write labels.csv (one row per shoe of the sheet, found yes / no, sets, pictures) to that file, with
--dry-run too.

The captures folder is only READ. --dry-run writes nothing but the --csv file: it counts the shoes found and not found per result, the sets
and pictures, and the size of the zip (pictures are stored, not compressed: the zip is as large as the pictures)."""

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # also runnable as a plain file

from backend.storage.qc_sheet import MASTER, MULTIPLE, process_master, read_xlsx, sheet_names, to_day  # noqa: E402

RESULT_FOLDERS = {"OK": "OK", "Defect": "Defect", "Conflict": "Conflict", "(blank)": "NoResult", "": "NoResult"}
FOLDER_ORDER = ["OK", "Defect", "Conflict", "NoResult"]
NEED = ["Date", "QR", "Result"]
SET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")   # a pass id that can be a folder name
README = """Saved sets of the shoes in a site QC sheet, by the result the line recorded.

Sheet: {sheet} ({rows} shoes), made {created}.
Days: {first} ~ {last}; {days_rule}
Found: {found} of {asked} shoes, {sets} sets, {frames} frames, {pictures} pictures.

<Result>/<shoe>/<set>/<id>_<camera>.jpg   the pictures as saved (full size); <id>.json the data saved with the frame
Result folders: OK, Defect, Conflict (the QR was recorded on several rows, with both OK and Defect), NoResult.
labels.csv: one row per shoe of the sheet (also the ones not found): result, defect type, unit, L/R, size, last, folder.
"""
README_DEFECT = ("\nDefect shoes only (--defect-only): the first folder is the defect type in place of the result "
                 "(Bonding/<shoe>/<set>/...).\nA shoe the line recorded with several types is in the folder of each "
                 "of them (the same sets copied; labels.csv 'types' and 'path' list them), so the shoes of the type "
                 "folders add up to more than the shoes.\nMultiple_types: several types, but which ones is not known; "
                 "Unknown_type: no type recorded. Conflict shoes are not in it.\n")


class Cancelled(Exception):
    pass


def type_folder(defect_type):
    """A defect type as a folder name: 'Over spray' -> Over_spray, none -> Unknown_type."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(defect_type or "").strip()).strip("._-")
    return name or "Unknown_type"


def recorded_types(path, sheet_name, shoes):
    """{QR: [defect type, ...]} of the 'Multiple types' shoes: the 'Types recorded' column of the sheet ('; ' between),
    else the 'Defect type' of the rows with that QR in the 'Data' sheet of the same workbook (in row order, each once).
    A shoe with fewer than two types found is left out (it stays in Multiple_types). path None: the column only."""
    multi = {s["QR"] for s in shoes if str(s.get("Defect type", "")).strip() == MULTIPLE}
    out = {}
    for s in shoes:
        if s["QR"] in multi and str(s.get("Types recorded", "")).strip():
            out[s["QR"]] = [t.strip() for t in str(s["Types recorded"]).split(";") if t.strip()]
    rest = multi - set(out)
    if rest and path is not None and path.suffix.lower() != ".csv" and sheet_name != "Data" and "Data" in sheet_names(path):
        rows = read_xlsx(path, "Data")
        head = [str(h).strip() for h in rows[0]] if rows else []
        if "QR" in head and "Defect type" in head:
            iq, it = head.index("QR"), head.index("Defect type")
            for row in rows[1:]:
                qr = str(row[iq]).strip() if iq < len(row) else ""
                t = str(row[it]).strip() if it < len(row) else ""
                if qr in rest and t and t != MULTIPLE and t not in out.setdefault(qr, []):
                    out[qr].append(t)
    return {qr: ts for qr, ts in out.items() if len(ts) >= 2}


def log(message):
    print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


def fmt_bytes(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


# ---- the sheet ----

def shoes_from_rows(rows, name):
    """The shoes of a one-row-per-shoe table (header + rows): [{column: value} + day, folder], problems."""
    if not rows:
        raise SystemExit(f"{name}: the sheet is empty")
    head = [str(h).strip() for h in rows[0]]
    missing = [n for n in NEED if n not in head]
    if missing:
        raise SystemExit(f"{name}: no column {', '.join(missing)} (the 'Shoes' sheet of the _QR only workbook has them)")
    shoes, bad, seen = [], [], set()
    for n, row in enumerate(rows[1:], 2):
        rec = {h: (row[i] if i < len(row) else "") for i, h in enumerate(head) if h}
        qr = str(rec["QR"]).strip()
        if not qr:
            continue
        day = to_day(rec["Date"])
        if day is None:
            bad.append(f"row {n}: date '{rec['Date']}'")
            continue
        if qr in seen:
            bad.append(f"row {n}: {qr} is in the sheet twice (kept the first)")
            continue
        seen.add(qr)
        result = str(rec["Result"]).strip()
        rec.update(QR=qr, day=day, Result=result, folder=RESULT_FOLDERS.get(result, "NoResult"))
        shoes.append(rec)
    return shoes, bad


def with_folders(shoes):
    for s in shoes:
        s["folder"] = RESULT_FOLDERS.get(s["Result"], "NoResult")
    return shoes


def read_sheet(path, sheet_name):
    """(shoes, problems, processed) of a .csv, a "_QR only" workbook (sheet_name, default Shoes) or a site QC workbook
    (no such sheet but 'Master data_cell 1': processed here; processed = process_master()'s result, else None)."""
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            return (*shoes_from_rows([list(r) for r in csv.reader(f)], path.name), None)
    names = sheet_names(path)
    if sheet_name not in names and MASTER in names:
        try:
            sheet = process_master(path)
        except ValueError as e:
            raise SystemExit(f"{path.name}: {e}") from None
        return with_folders(sheet["shoes"]), sheet["problems"], sheet
    return (*shoes_from_rows(read_xlsx(path, sheet_name), path.name), None)


# ---- the sets ----

def find_sets(shoes, any_day, C, ID_RE, time_key, cancel=None):
    """{QR: [(set name, [frame meta])]} of the shoes (same day as the sheet unless any_day), and the counts of the days scanned."""
    by_qr = {s["QR"]: s for s in shoes}
    days = {s["day"] for s in shoes}
    first, last = min(days), max(days)
    found, scanned, no_id = {}, 0, 0
    for e in os.scandir(C.SETS_DIR):
        if cancel and cancel():
            raise Cancelled()
        if not e.name.endswith(".json"):
            continue
        sid = e.name[:-5]
        if not ID_RE.match(sid):
            continue
        day = time_key(sid)[:8]
        if not any_day and day not in days:
            continue
        scanned += 1
        try:
            meta = json.loads(Path(e.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        qr = (meta.get("shoe_id") or "").strip()
        if not qr:
            no_id += 1
            continue
        shoe = by_qr.get(qr)
        if shoe is None or (not any_day and shoe["day"] != day):
            continue
        meta.setdefault("id", sid)
        set_name = str((meta.get("burst") or {}).get("id") or sid)   # the pass (20261105_124441_836-t60), else the frame
        found.setdefault(qr, {}).setdefault(set_name if SET_RE.match(set_name) else sid, []).append(meta)
    for qr in found:   # sets in time order, frames in time order
        found[qr] = sorted(((n, sorted(f, key=lambda m: time_key(m["id"]))) for n, f in found[qr].items()),
                           key=lambda x: time_key(x[1][0]["id"]))
    return found, {"first": first, "last": last, "frames_scanned": scanned, "frames_without_qr": no_id}


def frame_files(meta, image_path, primary_role):
    """[(camera, path, size)] of the pictures of a frame that exist."""
    primary = primary_role(meta)
    out = []
    for role in list(meta.get("views") or [primary]):
        path = image_path(meta["id"], role, primary=role == primary)
        try:
            out.append((role, path, path.stat().st_size))
        except OSError:
            pass
    return out


def plan(shoes, found, defect_only, multi, image_path, primary_role, qr_prefix):
    """What goes in the zip: per shoe its sets, frames and pictures, the counts, labels.csv (as text)."""
    rows, entries, folders, total = [], [], {}, 0
    per = {f: {"asked": 0, "found": 0, "sets": 0, "pictures": 0, "bytes": 0} for f in FOLDER_ORDER}
    per_day, per_type = {}, {}
    extra_pics = extra_bytes = 0   # the copies of the shoes with several types (in more than one folder)
    for s in shoes:
        f = s["folder"]
        types = multi.get(s["QR"]) or [s.get("Defect type")]
        tops = [type_folder(t) for t in types] if defect_only else [f]   # the first folder(s) in the zip
        tops = list(dict.fromkeys(tops))
        per[f]["asked"] += 1
        sets = found.get(s["QR"], [])
        paths = []
        n_frames = n_pics = size = 0
        if sets:
            base = qr_prefix(s["QR"]) or "shoe"
            for i, top in enumerate(tops):
                name, k = base, 1
                while (top, name) in folders:
                    k += 1
                    name = f"{base}_{k}"
                folders[(top, name)] = s["QR"]
                paths.append(f"{top}/{name}")
                for set_name, frames in sets:
                    for meta in frames:
                        files = frame_files(meta, image_path, primary_role)
                        entries.append((f"{top}/{name}/{set_name}", meta, files))
                        if i == 0:
                            n_frames += 1
                            n_pics += len(files)
                            size += sum(sz for _, _, sz in files)
                        else:
                            extra_pics += len(files)
                            extra_bytes += sum(sz for _, _, sz in files)
            per[f]["found"] += 1
            per[f]["sets"] += len(sets)
            per[f]["pictures"] += n_pics
            per[f]["bytes"] += size
            d = per_day.setdefault(s["day"], [0, 0, 0, 0])
            d[0] += 1; d[1] += len(sets); d[2] += n_pics; d[3] += size
            total += size
        if defect_only:
            for top in tops:
                t = per_type.setdefault(top, [0, 0, 0])
                t[0] += 1; t[1] += bool(sets); t[2] += n_pics
        rows.append({"qr": s["QR"], "date": f"{s['day'][:4]}-{s['day'][4:6]}-{s['day'][6:]}", "result": s["Result"] or "(blank)",
                     "folder": f, "defect_type": str(s.get("Defect type", "")).strip(),
                     "types": "; ".join(str(t) for t in multi.get(s["QR"], [])),
                     "conflict_types": str(s.get("Conflict: types recorded", "")).strip(),
                     "model": s.get("Model (QR)", ""), "unit": s.get("Unit (QR)", ""), "lr": s.get("L/R (QR)", ""),
                     "size": s.get("Size (QR)", ""), "last": s.get("Last", ""), "found": "yes" if sets else "no",
                     "sets": len(sets), "frames": n_frames, "pictures": n_pics, "path": "; ".join(paths)})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return {"rows": rows, "entries": entries, "per": per, "per_day": per_day, "per_type": per_type, "multi": multi,
            "extra_pics": extra_pics, "extra_bytes": extra_bytes, "total": total, "total_zip": total + extra_bytes,
            "asked": len(shoes), "found": sum(1 for r in rows if r["found"] == "yes"), "sets": sum(r["sets"] for r in rows),
            "frames": sum(r["frames"] for r in rows), "pictures": sum(r["pictures"] for r in rows),
            "not_found": [r["qr"] for r in rows if r["found"] == "no"], "labels_csv": buf.getvalue()}


def write_zip(P, out, readme, manifest, progress=None, cancel=None):
    """The zip of a plan (written as <out>.part, renamed when complete). progress(frames done, frames, bytes done)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    entries = P["entries"]
    written, done_bytes = 0, 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:   # JPEG does not shrink: stored
            zf.writestr("README.txt", readme)
            zf.writestr("labels.csv", "﻿" + P["labels_csv"])   # BOM: Excel opens it as UTF-8
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for n, (folder, meta, files) in enumerate(entries, 1):
                if cancel and cancel():
                    raise Cancelled()
                zf.writestr(f"{folder}/{meta['id']}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                for role, path, size in files:
                    try:
                        zf.write(path, f"{folder}/{meta['id']}_{role}.jpg")
                        written += 1
                        done_bytes += size
                    except OSError:   # deleted meanwhile: left out
                        pass
                if progress:
                    progress(n, len(entries), done_bytes)
        part.replace(out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return written


def make_texts(P, sheet_name_shown, opts, info, captures_dir):
    """(README text, manifest dict) of a plan."""
    first, last = info["first"], info["last"]
    created = datetime.now().isoformat(timespec="seconds")
    manifest = {"created": created, "sheet": sheet_name_shown, "options": opts,
                "per_defect_type": {t: {"shoes": v[0], "found": v[1], "pictures": v[2]} for t, v in P["per_type"].items()},
                "multiple_types": P["multi"], "copies": {"pictures": P["extra_pics"], "bytes": P["extra_bytes"]},
                "captures": str(captures_dir), "days": [first, last], "shoes": P["asked"], "found": P["found"],
                "sets": P["sets"], "frames": P["frames"], "pictures": P["pictures"], "bytes": P["total"],
                "per_result": P["per"], "not_found": P["not_found"], "sheet_problems": opts.get("problems", [])}
    readme = README.format(sheet=sheet_name_shown, rows=P["asked"], created=created,
                           first=f"{first[:4]}-{first[4:6]}-{first[6:]}", last=f"{last[:4]}-{last[4:6]}-{last[6:]}",
                           days_rule="sets of any day" if opts.get("any_day") else "only the sets of the day the line checked the shoe",
                           found=P["found"], asked=P["asked"], sets=P["sets"], frames=P["frames"], pictures=P["pictures"])
    if opts.get("defect_only"):
        readme += README_DEFECT
    return readme, manifest


def select(shoes, result, defect_only):
    """The shoes of the results asked (result: 'Defect,Conflict' or a list); defect_only: Defect only."""
    if defect_only:
        if result and {r.strip() for r in (result.split(",") if isinstance(result, str) else result) if r.strip()} != {"Defect"}:
            raise SystemExit("--defect-only takes the Defect shoes only: leave out --result")
        result = "Defect"
    if result:
        items = result.split(",") if isinstance(result, str) else result
        want = {RESULT_FOLDERS.get(r.strip(), r.strip()) for r in items if r.strip()}
        unknown = want - set(FOLDER_ORDER)
        if unknown:
            raise SystemExit(f"--result: {', '.join(sorted(unknown))}? (OK, Defect, Conflict, NoResult)")
        shoes = [s for s in shoes if s["folder"] in want]
    if not shoes:
        raise SystemExit("no shoes in the sheet" + (f" with result {result}" if result else ""))
    return shoes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", required=True, help="the site QC workbook, a _QR only workbook (its 'Shoes' sheet) or a .csv with the same columns")
    ap.add_argument("--sheet", default="Shoes", help="the sheet to read of a _QR only workbook (default Shoes)")
    ap.add_argument("--captures", help="the captures folder (default ON_CAPTURES_DIR / captures); only read")
    ap.add_argument("--out", help="the zip to write (default exports/qc_<first>_<last>_<time>.zip)")
    ap.add_argument("--result", help="only these results, e.g. Defect,Conflict (OK, Defect, Conflict, NoResult; default all)")
    ap.add_argument("--defect-only", action="store_true",
                    help="only the Defect shoes, a folder per defect type (qc_defect_....zip)")
    ap.add_argument("--any-day", action="store_true", help="also the sets of other days than the Date of the shoe in the sheet")
    ap.add_argument("--limit", type=int, default=0, help="only the first N shoes found (a test)")
    ap.add_argument("--dry-run", action="store_true", help="only count the shoes, sets, pictures and the size; write nothing")
    ap.add_argument("--csv", help="also write labels.csv (found / not found per shoe) to this file (with --dry-run too)")
    args = ap.parse_args()

    sheet_path = Path(args.xlsx).expanduser()
    if not sheet_path.is_file():
        raise SystemExit(f"{sheet_path}: no such file")
    if args.captures:   # the settings are read when backend.core.config is imported: set it before
        captures = Path(args.captures).expanduser().resolve()
        if not (captures / ".sets").is_dir():
            raise SystemExit(f"{captures} has no .sets folder: it is not a captures folder")
        os.environ["ON_CAPTURES_DIR"] = str(captures)

    from backend.core import config as C
    from backend.storage.paths import ID_RE, image_path, primary_role, qr_prefix, time_key

    shoes, bad, processed = read_sheet(sheet_path, args.sheet)
    shown = f"{sheet_path.name} [{MASTER if processed else args.sheet}]"
    shoes = select(shoes, args.result, args.defect_only)
    for line in bad[:10]:
        log(f"sheet: {line}")
    log(f"{shown}: {len(shoes)} shoes; looking for their sets in {C.SETS_DIR} ...")
    found, info = find_sets(shoes, args.any_day, C, ID_RE, time_key)
    if args.limit:
        found = dict(list(found.items())[:args.limit])
    multi = recorded_types(None if processed else sheet_path, args.sheet, shoes) if args.defect_only else {}
    P = plan(shoes, found, args.defect_only, multi, image_path, primary_role, qr_prefix)
    first, last = info["first"], info["last"]
    out = Path(args.out).expanduser() if args.out else \
        C.EXPORTS_DIR / f"qc_{'defect_' if args.defect_only else ''}{first}_{last}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    print(f"{'dry run (nothing is written)' if args.dry_run else 'export'}: {C.CAPTURES_DIR}")
    print(f"  sheet     {shown}, {P['asked']} shoes, days {first[:4]}-{first[4:6]}-{first[6:]} ~ "
          f"{last[:4]}-{last[4:6]}-{last[6:]}" + (" (sets of any day)" if args.any_day else " (sets of the same day only)"))
    print(f"  scanned   {info['frames_scanned']} frames of those days, {info['frames_without_qr']} without a QR value (can not be matched)")
    print(f"  {'result':9} {'shoes':>6} {'found':>6} {'sets':>6} {'pictures':>9} {'size':>10}")
    for f in FOLDER_ORDER:
        p = P["per"][f]
        if p["asked"]:
            print(f"  {f:9} {p['asked']:6d} {p['found']:6d} {p['sets']:6d} {p['pictures']:9d} {fmt_bytes(p['bytes']):>10}")
    print(f"  {'total':9} {P['asked']:6d} {P['found']:6d} {P['sets']:6d} {P['pictures']:9d} {fmt_bytes(P['total']):>10}")
    if P["per_type"]:
        print(f"  {'defect type (folder)':22} {'shoes':>6} {'found':>6} {'pictures':>9}")
        for t, (n_sh, n_fd, n_p) in sorted(P["per_type"].items(), key=lambda x: (-x[1][0], x[0])):
            print(f"  {t:22} {n_sh:6d} {n_fd:6d} {n_p:9d}")
        n_multi = sum(1 for s in shoes if str(s.get("Defect type", "")).strip() == MULTIPLE)
        if n_multi:
            print(f"  '{MULTIPLE}': {len(multi)} of {n_multi} shoes in the folder of each recorded type "
                  f"(counted in each above; {P['extra_pics']} pictures, {fmt_bytes(P['extra_bytes'])} more in the zip)"
                  + (f"; {n_multi - len(multi)} without the types stay in Multiple_types" if n_multi > len(multi) else ""))
    for day, (n_sh, n_st, n_p, n_b) in sorted(P["per_day"].items()):
        print(f"  {day[:4]}-{day[4:6]}-{day[6:]}  {n_sh:5d} shoes  {n_st:5d} sets  {n_p:6d} pictures  {fmt_bytes(n_b):>10}")
    if P["not_found"]:
        print(f"  not found {len(P['not_found'])} shoes, e.g. {', '.join(P['not_found'][:5])}")
    probe = out.parent
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    print(f"  zip       about {fmt_bytes(P['total_zip'])} -> {out}  (free at {probe}: {fmt_bytes(free)})")
    if args.csv:
        csv_path = Path(args.csv).expanduser()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("﻿" + P["labels_csv"], encoding="utf-8", newline="")   # BOM: Excel opens it as UTF-8
        print(f"  csv       {csv_path}: {P['asked']} shoes, {P['found']} found, {P['asked'] - P['found']} not found")
    if args.dry_run:
        return
    if not P["entries"]:
        raise SystemExit("no sets found for these shoes: nothing to write")
    if free < P["total_zip"] * 1.02 + 200 * 1024 * 1024:
        raise SystemExit(f"not enough free disk space at {probe} for the zip ({fmt_bytes(P['total_zip'])} needed)")
    opts = {"result": args.result or ("Defect" if args.defect_only else "all"), "defect_only": args.defect_only,
            "any_day": args.any_day, "limit": args.limit, "problems": bad}
    readme, manifest = make_texts(P, shown, opts, info, C.CAPTURES_DIR)
    step = max(1, len(P["entries"]) // 20)

    def progress(n, total, done):
        if n % step == 0:
            log(f"{n}/{total} frames, {fmt_bytes(done)}")
    written = write_zip(P, out, readme, manifest, progress)
    log(f"done: {out} ({fmt_bytes(out.stat().st_size)}), {P['found']} shoes, {P['sets']} sets, {written} pictures")


if __name__ == "__main__":
    main()
