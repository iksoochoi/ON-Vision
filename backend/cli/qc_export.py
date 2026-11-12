"""The saved sets of the shoes in a site QC sheet (OK / Defect from the line), as one zip with a folder per result.

    python -m backend.cli.qc_export --xlsx "captures/CTC_LS Shoes QC_raw data (11.05~09)_QR only.xlsx" --dry-run
    python -m backend.cli.qc_export --xlsx "captures/CTC_LS Shoes QC_raw data (11.05~09)_QR only.xlsx"
    python -m backend.cli.qc_export --xlsx ... --result Defect,Conflict      # only these results
    python -m backend.cli.qc_export --xlsx ... --any-day                     # also sets of other days than the QC day
    python -m backend.cli.qc_export --xlsx ... --defect-only --dry-run       # only Defect shoes, a folder per defect type

The sheet is the 'Shoes' sheet of the "_QR only" workbook (one row per shoe: Date, QR, Result, Defect type ...), or a
.csv with the same columns. A shoe is found by its QR value exactly as the line recorded it ('#' and all), compared with
the QR value saved in the set (shoe_id). Only the sets of the day in the Date column are taken (--any-day: every day).
Sets saved without a QR value (no-id) can not be matched and are not in it.

    exports/qc_<first day>_<last day>_<yyyymmdd_hhmmss>.zip
        README.txt
        labels.csv                    one row per shoe of the sheet: result, defect type, found or not, sets, pictures
        manifest.json                 the sheet, the options, counts, the shoes not found
        <Result>/<shoe>/<set>/<id>.json             the data of one frame as it is saved
        <Result>/<shoe>/<set>/<id>_<camera>.jpg     the pictures of that frame, as saved (full size)

<Result> is OK, Defect, Conflict (the line recorded the QR on several rows, with both OK and Defect) or NoResult (no
result recorded). <shoe> is the QR value as it goes in file names ('#' becomes '-'), <set> the id of the shoe's pass.

--defect-only: only the Defect shoes, in exports/qc_defect_<first day>_<last day>_<time>.zip, with a folder per defect type
(the 'Defect type' column) in place of the result folder: Bonding/<shoe>/<set>/..., Over_spray/..., Multiple_types/...
(a shoe with several types; labels.csv does not say which), Unknown_type/... (no type recorded). Conflict shoes are not in
it (the rows disagree on OK / Defect): add them with a second run, --result Conflict.

The captures folder is only READ. --dry-run writes nothing: it counts the shoes found and not found per result, the sets
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
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # also runnable as a plain file

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
X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def type_folder(defect_type):
    """A defect type as a folder name: 'Over spray' -> Over_spray, none -> Unknown_type."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(defect_type or "").strip()).strip("._-")
    return name or "Unknown_type"


def log(message):
    print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


def fmt_bytes(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


# ---- the sheet (an .xlsx read with the standard library only: no openpyxl on the Mac) ----

def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path, sheet_name):
    """The rows of one sheet as lists of values (strings, or numbers for numeric cells)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        strings = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{X}si"):
                strings.append("".join(t.text or "" for t in si.iter(f"{X}t")))
        book = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = {s.get("name"): rels[s.get(f"{R}id")] for s in book.iter(f"{X}sheet")}
        if sheet_name not in sheets:
            raise SystemExit(f"{path.name} has no sheet '{sheet_name}' (it has: {', '.join(sheets)})")
        target = sheets[sheet_name].lstrip("/")
        target = target if target.startswith("xl/") else f"xl/{target}"
        rows = []
        for row in ET.fromstring(z.read(target)).iter(f"{X}row"):
            cells = {}
            for c in row.iter(f"{X}c"):
                kind, v = c.get("t"), c.find(f"{X}v")
                if kind == "s":
                    value = strings[int(v.text)] if v is not None else ""
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{X}t"))
                elif v is None:
                    value = ""
                elif kind in ("str", "e", "b"):
                    value = v.text or ""
                else:
                    value = float(v.text)
                cells[_col(c.get("r"))] = value
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        return rows


def _day(value):
    """A Date cell as YYYYMMDD: an Excel day number, or text like 2026-11-05 / 2026-11-05 00:00:00."""
    if isinstance(value, float):
        return (date(1899, 12, 30) + timedelta(days=int(value))).strftime("%Y%m%d")
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return None


def read_sheet(path, sheet_name):
    """[{column: value}] of the shoes in the sheet, with 'day' (YYYYMMDD) and 'folder' (the result folder) added."""
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = [list(r) for r in csv.reader(f)]
    else:
        rows = read_xlsx(path, sheet_name)
    if not rows:
        raise SystemExit(f"{path.name}: the sheet is empty")
    head = [str(h).strip() for h in rows[0]]
    missing = [n for n in NEED if n not in head]
    if missing:
        raise SystemExit(f"{path.name}: no column {', '.join(missing)} (the 'Shoes' sheet of the _QR only workbook has them)")
    shoes, bad, seen = [], [], set()
    for n, row in enumerate(rows[1:], 2):
        rec = {h: (row[i] if i < len(row) else "") for i, h in enumerate(head) if h}
        qr = str(rec["QR"]).strip()
        if not qr:
            continue
        day = _day(rec["Date"])
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


# ---- the sets ----

def find_sets(shoes, any_day, C, ID_RE, time_key):
    """{QR: [(set name, [frame meta])]} of the shoes (same day as the sheet unless any_day), and the counts of the days scanned."""
    by_qr = {s["QR"]: s for s in shoes}
    days = {s["day"] for s in shoes}
    first, last = min(days), max(days)
    found, scanned, no_id = {}, 0, 0
    for e in os.scandir(C.SETS_DIR):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", required=True, help="the _QR only workbook (its 'Shoes' sheet) or a .csv with the same columns")
    ap.add_argument("--sheet", default="Shoes", help="the sheet to read (default Shoes)")
    ap.add_argument("--captures", help="the captures folder (default ON_CAPTURES_DIR / captures); only read")
    ap.add_argument("--out", help="the zip to write (default exports/qc_<first>_<last>_<time>.zip)")
    ap.add_argument("--result", help="only these results, e.g. Defect,Conflict (OK, Defect, Conflict, NoResult; default all)")
    ap.add_argument("--defect-only", action="store_true",
                    help="only the Defect shoes, a folder per defect type (qc_defect_....zip)")
    ap.add_argument("--any-day", action="store_true", help="also the sets of other days than the Date of the shoe in the sheet")
    ap.add_argument("--limit", type=int, default=0, help="only the first N shoes found (a test)")
    ap.add_argument("--dry-run", action="store_true", help="only count the shoes, sets, pictures and the size; write nothing")
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

    shoes, bad = read_sheet(sheet_path, args.sheet)
    if args.defect_only:
        if args.result and {r.strip() for r in args.result.split(",") if r.strip()} != {"Defect"}:
            raise SystemExit("--defect-only takes the Defect shoes only: leave out --result")
        args.result = "Defect"
    if args.result:
        want = {RESULT_FOLDERS.get(r.strip(), r.strip()) for r in args.result.split(",") if r.strip()}
        unknown = want - set(FOLDER_ORDER)
        if unknown:
            raise SystemExit(f"--result: {', '.join(sorted(unknown))}? (OK, Defect, Conflict, NoResult)")
        shoes = [s for s in shoes if s["folder"] in want]
    if not shoes:
        raise SystemExit("no shoes in the sheet" + (f" with result {args.result}" if args.result else ""))
    for line in bad[:10]:
        log(f"sheet: {line}")
    log(f"{sheet_path.name} [{args.sheet}]: {len(shoes)} shoes; looking for their sets in {C.SETS_DIR} ...")
    found, info = find_sets(shoes, args.any_day, C, ID_RE, time_key)
    if args.limit:
        found = dict(list(found.items())[:args.limit])

    # what goes in: per shoe the sets, frames and pictures
    rows, entries, folders, total = [], [], {}, 0
    per = {f: {"asked": 0, "found": 0, "sets": 0, "pictures": 0, "bytes": 0} for f in FOLDER_ORDER}
    per_day, per_type = {}, {}
    for s in shoes:
        f = s["folder"]
        top = type_folder(s.get("Defect type")) if args.defect_only else f   # the first folder in the zip
        per[f]["asked"] += 1
        sets = found.get(s["QR"], [])
        name = ""
        n_frames = n_pics = size = 0
        if sets:
            base = qr_prefix(s["QR"]) or "shoe"
            name, k = base, 1
            while (top, name) in folders:
                k += 1
                name = f"{base}_{k}"
            folders[(top, name)] = s["QR"]
            for set_name, frames in sets:
                for meta in frames:
                    files = frame_files(meta, image_path, primary_role)
                    entries.append((f"{top}/{name}/{set_name}", meta, files))
                    n_frames += 1
                    n_pics += len(files)
                    size += sum(sz for _, _, sz in files)
            per[f]["found"] += 1
            per[f]["sets"] += len(sets)
            per[f]["pictures"] += n_pics
            per[f]["bytes"] += size
            d = per_day.setdefault(s["day"], [0, 0, 0, 0])
            d[0] += 1; d[1] += len(sets); d[2] += n_pics; d[3] += size
            total += size
        if args.defect_only:
            t = per_type.setdefault(top, [0, 0, 0])
            t[0] += 1; t[1] += bool(sets); t[2] += n_pics
        rows.append({"qr": s["QR"], "date": f"{s['day'][:4]}-{s['day'][4:6]}-{s['day'][6:]}", "result": s["Result"] or "(blank)",
                     "folder": f, "defect_type": str(s.get("Defect type", "")).strip(),
                     "conflict_types": str(s.get("Conflict: types recorded", "")).strip(),
                     "unit": s.get("Unit (QR)", ""), "lr": s.get("L/R (QR)", ""), "size": s.get("Size (QR)", ""),
                     "last": s.get("Last", ""), "found": "yes" if sets else "no", "sets": len(sets),
                     "frames": n_frames, "pictures": n_pics, "path": f"{top}/{name}" if name else ""})
    asked = len(shoes)
    n_found = sum(1 for r in rows if r["found"] == "yes")
    n_sets = sum(r["sets"] for r in rows)
    n_frames = sum(r["frames"] for r in rows)
    n_pics = sum(r["pictures"] for r in rows)
    first, last = info["first"], info["last"]
    out = Path(args.out).expanduser() if args.out else \
        C.EXPORTS_DIR / f"qc_{'defect_' if args.defect_only else ''}{first}_{last}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    print(f"{'dry run (nothing is written)' if args.dry_run else 'export'}: {C.CAPTURES_DIR}")
    print(f"  sheet     {sheet_path.name} [{args.sheet}], {asked} shoes, days {first[:4]}-{first[4:6]}-{first[6:]} ~ "
          f"{last[:4]}-{last[4:6]}-{last[6:]}" + (" (sets of any day)" if args.any_day else " (sets of the same day only)"))
    print(f"  scanned   {info['frames_scanned']} frames of those days, {info['frames_without_qr']} without a QR value (can not be matched)")
    print(f"  {'result':9} {'shoes':>6} {'found':>6} {'sets':>6} {'pictures':>9} {'size':>10}")
    for f in FOLDER_ORDER:
        p = per[f]
        if p["asked"]:
            print(f"  {f:9} {p['asked']:6d} {p['found']:6d} {p['sets']:6d} {p['pictures']:9d} {fmt_bytes(p['bytes']):>10}")
    print(f"  {'total':9} {asked:6d} {n_found:6d} {n_sets:6d} {n_pics:9d} {fmt_bytes(total):>10}")
    if per_type:
        print(f"  {'defect type (folder)':22} {'shoes':>6} {'found':>6} {'pictures':>9}")
        for t, (n_sh, n_fd, n_p) in sorted(per_type.items(), key=lambda x: (-x[1][0], x[0])):
            print(f"  {t:22} {n_sh:6d} {n_fd:6d} {n_p:9d}")
    for day, (n_sh, n_st, n_p, n_b) in sorted(per_day.items()):
        print(f"  {day[:4]}-{day[4:6]}-{day[6:]}  {n_sh:5d} shoes  {n_st:5d} sets  {n_p:6d} pictures  {fmt_bytes(n_b):>10}")
    not_found = [r["qr"] for r in rows if r["found"] == "no"]
    if not_found:
        print(f"  not found {len(not_found)} shoes, e.g. {', '.join(not_found[:5])}")
    probe = out.parent
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    print(f"  zip       about {fmt_bytes(total)} -> {out}  (free at {probe}: {fmt_bytes(free)})")
    if args.dry_run:
        return
    if not entries:
        raise SystemExit("no sets found for these shoes: nothing to write")
    if free < total * 1.02 + 200 * 1024 * 1024:
        raise SystemExit(f"not enough free disk space at {probe} for the zip ({fmt_bytes(total)} needed)")

    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "sheet": sheet_path.name, "sheet_name": args.sheet,
                "options": {"result": args.result or "all", "defect_only": args.defect_only, "any_day": args.any_day,
                            "limit": args.limit},
                "per_defect_type": {t: {"shoes": v[0], "found": v[1], "pictures": v[2]} for t, v in per_type.items()},
                "captures": str(C.CAPTURES_DIR), "days": [first, last], "shoes": asked, "found": n_found,
                "sets": n_sets, "frames": n_frames, "pictures": n_pics, "bytes": total,
                "per_result": per, "not_found": not_found, "sheet_problems": bad}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    readme = README.format(sheet=sheet_path.name, rows=asked, created=manifest["created"],
                           first=f"{first[:4]}-{first[4:6]}-{first[6:]}", last=f"{last[:4]}-{last[4:6]}-{last[6:]}",
                           days_rule="sets of any day" if args.any_day else "only the sets of the day the line checked the shoe",
                           found=n_found, asked=asked, sets=n_sets, frames=n_frames, pictures=n_pics)
    if args.defect_only:
        readme += ("\nDefect shoes only (--defect-only): the first folder is the defect type in place of the result "
                   "(Bonding/<shoe>/<set>/...).\nMultiple_types: the line recorded several types on the shoe; "
                   "Unknown_type: no type recorded. Conflict shoes are not in it.\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    written, step, done_bytes = 0, max(1, len(entries) // 20), 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:   # JPEG does not shrink: stored
            zf.writestr("README.txt", readme)
            zf.writestr("labels.csv", "﻿" + buf.getvalue())   # BOM: Excel opens it as UTF-8
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for n, (folder, meta, files) in enumerate(entries, 1):
                zf.writestr(f"{folder}/{meta['id']}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                for role, path, size in files:
                    try:
                        zf.write(path, f"{folder}/{meta['id']}_{role}.jpg")
                        written += 1
                        done_bytes += size
                    except OSError:   # deleted meanwhile: left out
                        pass
                if n % step == 0:
                    log(f"{n}/{len(entries)} frames, {fmt_bytes(done_bytes)}")
        part.replace(out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log(f"done: {out} ({fmt_bytes(out.stat().st_size)}), {n_found} shoes, {n_sets} sets, {written} pictures")


if __name__ == "__main__":
    main()
