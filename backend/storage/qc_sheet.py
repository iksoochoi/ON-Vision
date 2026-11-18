"""The site QC workbook (the weekly "CTC_LS Shoes QC_raw data (...).xlsx", sheet 'Master data_cell 1': one row per
inspection) made into one row per shoe, the QR trusted (unit, L/R, size from the QR, not from the sheet's columns).

    read_xlsx(path, sheet)        the rows of a sheet, standard library only (no openpyxl needed to read)
    process_master(path)          -> {"data": [inspection rows], "shoes": [shoe rows], "problems": [...]}
    write_workbook(dst, ...)      the "_QR only" workbook: Summary (COUNTIFS formulas) / Shoes / Data; needs openpyxl

The shoe rows have the column names of the workbook's Shoes sheet (Date, QR, Result, Defect type, Types recorded ...),
so backend.cli.qc_export takes them as they are. One shoe = one QR; a QR recorded with both OK and Defect is
'Conflict', with several defect types 'Multiple types' (the types in 'Types recorded').
Made from train/qc_sheet/qr_only_workbook.py (pandas, on the PC). Changed: the lot of the QR is one or two letters
(2026-11-10: EQBB-BA-K1, before EQBB-AN-K1 was read as A + lot N); the types of a shoe are counted one by one (a row
with two types is two types); other models than ON1WH-B-B (2026-11-10: ON1GR-L-B, HM3LS Lucent/Black) and the
model code of the QR in "Model (QR)"; a row with nothing in the type columns (0/1, after 'Defect') takes the types
of the grade columns (the same 18 types in the same order, between GRADE and OK; 2026-11-14 row 7385: only
'Spray Quality' = C there), marked in Data's 'Type from'."""

import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

MASTER = "Master data_cell 1"
X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
QR_RE = re.compile(r"^(ON1[A-Z]{2}-[A-Z]-[A-Z])[#-](\d+)(T?)([LR])EQBB([A-Z]{1,2}?)K1(W\d[AB])(\d)(\d{4})([LR])$")
MULTIPLE = "Multiple types"
SHOE_COLS = ["Date", "QR", "Model (QR)", "Unit (QR)", "L/R (QR)", "Size (QR)", "Last", "Lot", "Serial", "Rows", "Result", "Defect type",
             "Types recorded", "Conflict: types recorded", "Original rows"]
INSTALL_HINT = ("the summary workbook needs openpyxl: in the app folder on the Mac, "
                "'.venv/bin/python -m pip install openpyxl' (then restart the app)")


# ---- reading an .xlsx with the standard library ----

def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def sheet_names(path):
    with zipfile.ZipFile(path) as z:
        return [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{X}sheet")]


def read_xlsx(path, sheet_name):
    """The rows of one sheet as lists of values (strings, or numbers for numeric cells). SystemExit if no such sheet."""
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
            raise SystemExit(f"{getattr(path, 'name', path)} has no sheet '{sheet_name}' (it has: {', '.join(sheets)})")
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
            n = int(row.get("r") or len(rows) + 1)
            while len(rows) < n - 1:   # empty rows keep their place (the row number is the sheet's)
                rows.append([])
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        return rows


def to_day(value):
    """A Date cell as YYYYMMDD: an Excel day number, or text like 2026-11-05 / 2026-11-05 00:00:00; None if not a date."""
    if isinstance(value, float):
        return (date(1899, 12, 30) + timedelta(days=int(value))).strftime("%Y%m%d")
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _text(v):
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v).strip()


# ---- one row per shoe ----

def process_master(path):
    """The inspection rows with a QR and the shoes of a site QC workbook. Raises ValueError when it is not one."""
    rows = read_xlsx(path, MASTER) if MASTER in sheet_names(path) else None
    if rows is None:
        raise ValueError(f"no sheet '{MASTER}' (it has: {', '.join(sheet_names(path))}): not a site QC workbook")
    if not rows:
        raise ValueError(f"the sheet '{MASTER}' is empty")
    names = [_text(h) for h in rows[0]]
    try:
        i_ok, i_def = names.index("OK"), names.index("Defect")
    except ValueError:
        raise ValueError(f"'{MASTER}': no 'OK' / 'Defect' column in the first row") from None
    width = max(len(r) for r in rows)
    # the defect type columns: after 'Defect', up to the last named column (Contamination ... Spray Gap)
    types = [i for i in range(i_def + 1, len(names)) if names[i]]
    # the grade columns (a letter or 1 per type, between GRADE and OK): the same types in the same order (2026-11:
    # where both are filled they always agree); used when a row has nothing in the type columns
    i_grade = names.index("GRADE") if "GRADE" in names else 6
    grades = list(range(i_grade + 1, i_ok))
    if len(grades) != len(types):
        grades = []
    # the QR column: the one whose values look like a shoe QR (the sheet leaves its title empty; AT before)
    hits = {i: sum(1 for r in rows[1:] if i < len(r) and QR_RE.match(_text(r[i]))) for i in range(width)}
    i_qr = max(hits, key=hits.get) if hits else None
    if not i_qr or not hits[i_qr]:
        raise ValueError(f"'{MASTER}': no column with shoe QR values (ON1WH-B-B#...)")
    problems = []
    data = []
    get = lambda r, i: r[i] if i < len(r) else ""
    for n, r in enumerate(rows[1:], 2):   # n: the row number in the sheet
        qr = _text(get(r, i_qr))
        if not qr:
            continue
        m = QR_RE.match(qr)
        if not m:
            problems.append(f"row {n}: '{qr}' is not a shoe QR (left out)")
            continue
        day = to_day(get(r, 0))
        if day is None:
            problems.append(f"row {n}: date '{get(r, 0)}' (left out)")
            continue
        model, num, half, lr, lot, unit, unit_n, serial, _ = m.groups()
        size = f"M{num}{'.5' if half else ''}"
        last = f"{unit}{unit_n}-#{num}{half}-{lr}"
        res = _num(get(r, i_def)) if _text(get(r, i_def)) != "" else None
        result = {0.0: "OK", 1.0: "Defect"}.get(res, "(blank)")
        dtypes = [names[i] for i in types if _num(get(r, i)) > 0]
        source = "type columns" if dtypes else ""
        if not dtypes and grades:
            dtypes = [names[t] for g, t in zip(grades, types) if _text(get(r, g)) not in ("", "0")]
            source = "grade columns" if dtypes else ""
        diff = ", ".join(k for k, bad in (("Unit", _text(get(r, 5)) != unit), ("L/R", _text(get(r, 4)) != lr),
                                          ("Size", _text(get(r, 2)) != size)) if bad)
        grade = _text(get(r, 6))
        data.append({"Original row": n, "day": day, "Model": _text(get(r, 1)), "Size": _text(get(r, 2)),
                     "Division": _text(get(r, 3)), "L/R": _text(get(r, 4)), "Unit No.": _text(get(r, 5)),
                     "GRADE": grade or None, "Result": result, "types": dtypes, "Defect type": ", ".join(dtypes),
                     "Type from": source, "QR": qr, "Model (QR)": model, "Unit (QR)": unit, "L/R (QR)": lr, "Size (QR)": size, "Last": last, "Lot": lot,
                     "Serial": int(serial), "Sheet differs from QR": diff})
    if not data:
        raise ValueError(f"'{MASTER}': no rows with a shoe QR")
    by_qr = {}
    for d in data:
        by_qr.setdefault(d["QR"], []).append(d)
    for d in data:
        d["Rows with this QR"] = len(by_qr[d["QR"]])
    shoes = []
    for qr, g in by_qr.items():
        res = sorted({d["Result"] for d in g} - {"(blank)"})
        result = res[0] if len(res) == 1 else ("Conflict" if res else "(blank)")
        types = []
        for d in g:
            for t in d["types"]:
                if t not in types:
                    types.append(t)
        dtype = (types[0] if len(types) == 1 else MULTIPLE if types else "") if result == "Defect" else ""
        f = g[0]
        shoes.append({"day": f["day"], "Date": f"{f['day'][:4]}-{f['day'][4:6]}-{f['day'][6:]}", "QR": qr,
                      "Model (QR)": f["Model (QR)"], "Unit (QR)": f["Unit (QR)"], "L/R (QR)": f["L/R (QR)"], "Size (QR)": f["Size (QR)"],
                      "Last": f["Last"], "Lot": f["Lot"], "Serial": f["Serial"], "Rows": len(g), "Result": result,
                      "Defect type": dtype, "Types recorded": "; ".join(types) if dtype == MULTIPLE else "",
                      "Conflict: types recorded": ", ".join(types) if result == "Conflict" else "",
                      "Original rows": ", ".join(str(d["Original row"]) for d in g)})
    shoes.sort(key=lambda s: (s["day"], s["Model (QR)"], s["Unit (QR)"], s["Serial"]))
    return {"data": data, "shoes": shoes, "problems": problems, "type_names": [names[i] for i in types]}


def counts(shoes):
    """{"results": {result: n}, "types": {defect type: n} (of the Defect shoes; a Multiple types shoe in each),
    "days": [first, last]} for a summary."""
    res, typ = {}, {}
    for s in shoes:
        res[s["Result"]] = res.get(s["Result"], 0) + 1
        if s["Result"] == "Defect":
            for t in (s["Types recorded"].split("; ") if s["Types recorded"] else [s["Defect type"] or "(none)"]):
                typ[t] = typ.get(t, 0) + 1
    days = sorted({s["day"] for s in shoes})
    return {"results": res, "types": dict(sorted(typ.items(), key=lambda x: -x[1])), "days": [days[0], days[-1]]}


# ---- the "_QR only" workbook ----

def write_workbook(dst, src_name, sheet, found=None):
    """sheet: process_master()'s result; found: {QR: (yes/no, sets)} of the Mac's sets, or None. Needs openpyxl."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError(INSTALL_HINT) from None
    data, shoes, type_names = sheet["data"], sheet["shoes"], sheet["type_names"]
    F = "Arial"
    H = Font(name=F, bold=True, color="FFFFFF"); HF = PatternFill("solid", fgColor="37474F")
    B = Font(name=F); BB = Font(name=F, bold=True); T = Font(name=F, bold=True, size=13)
    NOTE = Font(name=F, italic=True, color="555555", size=9)
    SUB = PatternFill("solid", fgColor="E8EEF1"); WARN = PatternFill("solid", fgColor="FFF2CC")
    GREY = PatternFill("solid", fgColor="E0E0E0")
    thin = Side(style="thin", color="BBBBBB"); BOX = Border(top=thin, bottom=thin, left=thin, right=thin)
    wb = Workbook()
    day = lambda d: date(int(d[:4]), int(d[4:6]), int(d[6:]))

    def table(ws, header, rows, widths):
        for j, h in enumerate(header, 1):
            c = ws.cell(1, j, h); c.font = H; c.fill = HF
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, row in enumerate(rows, 2):
            for j, v in enumerate(row, 1):
                ws.cell(i, j, v).font = B
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(2, 1)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{1 + len(rows)}"

    # Shoes (the Summary finds its columns by name)
    ws = wb.active; ws.title = "Shoes"
    hdr = SHOE_COLS + (["Found on Mac", "Sets on Mac"] if found is not None else [])
    L = lambda name: get_column_letter(hdr.index(name) + 1)
    i_res, i_mac = hdr.index("Result"), (hdr.index("Found on Mac") if found is not None else None)
    rows = [[day(s["day"])] + [s[c] for c in SHOE_COLS[1:]]
            + ([found.get(s["QR"], ("no", 0))[0], found.get(s["QR"], ("no", 0))[1]] if found is not None else [])
            for s in shoes]
    table(ws, hdr, rows, [11, 34, 11, 9, 8, 9, 14, 6, 8, 6, 9, 16, 30, 26, 16, 12, 11])
    for i, r in enumerate(rows, 2):
        ws.cell(i, 1).number_format = "yyyy-mm-dd"
        if r[i_res] in ("Conflict", "(blank)"):
            for j in range(1, len(hdr) + 1):
                ws.cell(i, j).fill = WARN
        if found is not None and r[i_mac] == "no":
            ws.cell(i, i_mac + 1).fill = GREY
    NS = len(rows) + 1

    # Data
    wd = wb.create_sheet("Data")
    dcols = ["Original row", "Date", "Model", "Size", "Division", "L/R", "Unit No.", "GRADE", "Result", "Defect type", "Type from",
             "QR", "Model (QR)", "Unit (QR)", "L/R (QR)", "Size (QR)", "Last", "Lot", "Serial", "Sheet differs from QR", "Rows with this QR"]
    drows = [[d["Original row"], day(d["day"])] + [d[c] for c in dcols[2:]] for d in data]
    table(wd, dcols, drows, [9, 11, 22, 7, 8, 5, 8, 7, 8, 16, 12, 34, 11, 9, 8, 9, 14, 6, 8, 14, 9])
    for i, r in enumerate(drows, 2):
        wd.cell(i, 2).number_format = "yyyy-mm-dd"
        if r[-2]:
            wd.cell(i, len(dcols) - 1).fill = WARN
        if r[dcols.index("Type from")] == "grade columns":
            wd.cell(i, dcols.index("Type from") + 1).fill = WARN
        if r[-1] > 1:
            wd.cell(i, len(dcols)).fill = WARN
    ND = len(drows) + 1

    # Summary: COUNTIFS over Shoes (Conflict / blank shoes are not OK nor Defect: out of the rate)
    sm = wb.create_sheet("Summary", 0)
    rng = lambda col: f"Shoes!${col}$2:${col}${NS}"
    RES, MAC = rng(L("Result")), (rng(L("Found on Mac")) if found is not None else None)
    DL = lambda name: get_column_letter(dcols.index(name) + 1)
    days = sorted({s["day"] for s in shoes})
    span = f"{days[0][4:6]}/{days[0][6:]}~{days[-1][4:6]}/{days[-1][6:]}"
    sm["A1"] = f"QC result per shoe — rows with a QR only ({span}, {MASTER})"; sm["A1"].font = T
    notes = [f"Source: {src_name}, sheet '{MASTER}'. Only rows with a shoe QR are used.",
             "The QR is taken as correct: Unit, L/R and Size come from the QR, not from the sheet's columns "
             f"(they differ on some rows, see Data!{DL('Sheet differs from QR')}).",
             "One shoe = one QR. A QR recorded on several rows with both OK and Defect is 'Conflict' and is left out of the rates.",
             "Defect rate = Defect / (OK + Defect). 'Multiple types': a Defect shoe recorded with several types "
             f"(Shoes!{L('Types recorded')} lists them; counted under Multiple types below).",
             "Defect type: from the 0/1 type columns; a row with none there takes the types written in the grade columns "
             f"(the same types, between GRADE and OK; Data!{DL('Type from')} = grade columns)."]
    if found is not None:
        notes.append("Found on Mac: the shoe's sets saved on the Mac (same QR, same day). Not found: no set with that QR "
                     "that day (QR not read / misread / not captured).")
    for i, t in enumerate(notes, 2):
        sm.cell(i, 1, t).font = NOTE
    state = {"row": len(notes) + 3}
    NC = 8 if found is not None else 6

    def block(title, key_col, keys, key_label, is_date=False):
        row = state["row"]
        sm.cell(row, 1, title).font = BB; row += 1
        heads = [key_label, "Shoes", "OK", "Defect", "Conflict", "Defect rate"] + (["Found on Mac", "Not found"] if found is not None else [])
        for j, h in enumerate(heads, 1):
            c = sm.cell(row, j, h); c.font = H; c.fill = HF; c.alignment = Alignment(horizontal="center", wrap_text=True)
        row += 1; first = row
        for k in keys:
            sm.cell(row, 1, k).font = B
            if is_date:
                sm.cell(row, 1).number_format = "yyyy-mm-dd"
            crit = f"{rng(key_col)},$A{row}"
            sm.cell(row, 2, f"=COUNTIFS({crit})")
            sm.cell(row, 3, f'=COUNTIFS({crit},{RES},"OK")')
            sm.cell(row, 4, f'=COUNTIFS({crit},{RES},"Defect")')
            sm.cell(row, 5, f'=COUNTIFS({crit},{RES},"Conflict")')
            sm.cell(row, 6, f"=IFERROR(D{row}/(C{row}+D{row}),0)")
            if found is not None:
                sm.cell(row, 7, f'=COUNTIFS({crit},{MAC},"yes")')
                sm.cell(row, 8, f'=COUNTIFS({crit},{MAC},"no")')
            row += 1
        sm.cell(row, 1, "Total").font = BB
        for j, col in enumerate("BCDE", 2):
            sm.cell(row, j, f"=SUM({col}{first}:{col}{row - 1})").font = BB
        if found is not None:
            for j, col in ((7, "G"), (8, "H")):
                sm.cell(row, j, f"=SUM({col}{first}:{col}{row - 1})").font = BB
        sm.cell(row, 6, f"=IFERROR(D{row}/(C{row}+D{row}),0)").font = BB
        for r in range(first, row + 1):
            sm.cell(r, 6).number_format = "0.0%"
            for j in range(1, NC + 1):
                sm.cell(r, j).border = BOX
                if r == row:
                    sm.cell(r, j).fill = SUB
        state["row"] = row + 2

    row = state["row"]
    sm.cell(row, 1, "Rows and shoes").font = BB; row += 1
    dq, dd = DL("QR"), DL("Sheet differs from QR")
    facts = [("Rows with a QR (Data)", f"=COUNTA(Data!${dq}$2:${dq}${ND})"),
             ("Shoes (distinct QR)", f"=COUNTA({rng(L('QR'))})"),
             ("QR on more than one row (shoes)", f'=COUNTIFS({rng(L("Rows"))},">1")'),
             ("Rows where the sheet differs from the QR", f'=COUNTIFS(Data!${dd}$2:${dd}${ND},"?*")')]
    if found is not None:
        facts += [("Shoes found on Mac", f'=COUNTIFS({MAC},"yes")'), ("Shoes not found on Mac", f'=COUNTIFS({MAC},"no")')]
    for k, f in facts:
        sm.cell(row, 1, k).font = B; sm.cell(row, 2, f)
        for j in (1, 2):
            sm.cell(row, j).border = BOX
        row += 1
    state["row"] = row + 1

    block("By date", L("Date"), [day(d) for d in days], "Date", is_date=True)
    block("By model (from QR)", L("Model (QR)"), sorted({s["Model (QR)"] for s in shoes}), "Model")
    block("By unit (from QR)", L("Unit (QR)"), sorted({s["Unit (QR)"] for s in shoes}), "Unit")
    block("By L/R (from QR)", L("L/R (QR)"), ["L", "R"], "L/R")
    block("By size (from QR)", L("Size (QR)"), sorted({s["Size (QR)"] for s in shoes}, key=lambda z: float(z[1:])), "Size")
    per_last = {}
    for s in shoes:
        per_last[s["Last"]] = per_last.get(s["Last"], 0) + 1
    block("By last (most shoes first)", L("Last"), sorted(per_last, key=lambda k: -per_last[k]), "Last")

    row = state["row"]
    sm.cell(row, 1, "Defect types (Defect shoes)").font = BB; row += 1
    TC = 5 if found is not None else 3
    for j, h in enumerate(["Defect type", "Shoes", "Share of defects"] + (["Found on Mac", "Not found"] if found is not None else []), 1):
        c = sm.cell(row, j, h); c.font = H; c.fill = HF; c.alignment = Alignment(horizontal="center", wrap_text=True)
    row += 1; first = row
    tcount = {}
    for s in shoes:
        if s["Result"] == "Defect":
            tcount[s["Defect type"]] = tcount.get(s["Defect type"], 0) + 1
    tlist = sorted(type_names, key=lambda t: -tcount.get(t, 0)) + ([MULTIPLE] if MULTIPLE in tcount else [])
    for t in tlist:
        sm.cell(row, 1, t).font = B
        K = rng(L("Defect type"))
        sm.cell(row, 2, f'=COUNTIFS({K},$A{row},{RES},"Defect")')
        if found is not None:
            sm.cell(row, 4, f'=COUNTIFS({K},$A{row},{RES},"Defect",{MAC},"yes")')
            sm.cell(row, 5, f'=COUNTIFS({K},$A{row},{RES},"Defect",{MAC},"no")')
        row += 1
    tot = row
    sm.cell(tot, 1, "Total").font = BB; sm.cell(tot, 2, f"=SUM(B{first}:B{tot - 1})").font = BB
    if found is not None:
        for col in "DE":
            sm[f"{col}{tot}"] = f"=SUM({col}{first}:{col}{tot - 1})"; sm[f"{col}{tot}"].font = BB
    for r in range(first, tot + 1):
        sm.cell(r, 3, f"=IFERROR(B{r}/$B${tot},0)").number_format = "0.0%"
        for j in range(1, TC + 1):
            sm.cell(r, j).border = BOX
    for j in range(1, TC + 1):
        sm.cell(tot, j).fill = SUB
    for col, w in zip("ABCDEFGH", [34, 10, 10, 10, 10, 12, 12, 11]):
        sm.column_dimensions[col].width = w
    for r in sm.iter_rows():
        for c in r:
            if c.font is None or c.font.name != F:
                c.font = Font(name=F, bold=c.font.bold if c.font else False)
    wb.save(dst)
