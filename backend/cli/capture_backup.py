"""Back up one day of the captures folder: statistics, a zip, then the backed-up files are deleted.

    ./bin/capture_backup.sh                     today's sets, started at 23:59 (waits until then)
    ./bin/capture_backup.sh --date 2026-10-15   that day's sets, started now (the day is over)
    ./bin/capture_backup.sh --now               today's sets, started now
    ./bin/capture_backup.sh --all               every day that has sets (up to today), one zip per day, oldest first
    ./bin/capture_backup.sh --dry-run           only say what would be done
    ./bin/capture_backup.sh --delete --date 2026-10-15   NO backup: delete that day's data (asks first; --yes skips it)
    ./bin/capture_backup.sh --delete --all --now         NO backup: delete everything up to today (asks first)

What it does, in this order (it stops at the first problem and then deletes nothing):
  1. waits for 23:59 of the day (--at HH:MM changes the time, --now does not wait)
  2. finds the day's sets (a set = one shoe pass: its cuts, every camera's picture, thumbnails and JSON files).
     A set that was saved in the last SETTLE_SEC seconds is left for later: it may still be written.
  3. writes captures/readme.md: statistics of the day per view and per hour, and the QR results
  4. zips the sets and readme.md into <backup folder>/capture_<date>.zip (ON_BACKUP_DIR, default backups/;
     a name that exists is not overwritten: capture_<date>_2.zip)
  5. checks the zip (every file is read back and its CRC is checked, names and sizes are compared)
  6. only then deletes the files that are in the zip from the captures folder
The statistics come from the sets' JSON files: no camera and no running app is needed (the app may keep running;
it notices the deleted files within two minutes)."""

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

SETTLE_SEC = 60            # a set file younger than this may still be written: not archived now
FREE_MARGIN = 1.02         # the zip needs this much free space next to the size of what goes in
MIN_FREE_BYTES = 200 * 1024 * 1024
MARKER = "<!-- capture_backup -->"


# ---- small helpers -------------------------------------------------------------------------------------------------

def log(message):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def fmt_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    value, i = float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{value:.0f} {units[i]}" if i == 0 else f"{value:.2f} {units[i]}"


def pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "-"


def table(header, rows, align=None):
    """A markdown table; align: a string like 'lrrr' (left / right) for the columns."""
    align = align or "l" * len(header)
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(" ---: " if a == "r" else " --- " for a in align) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


# ---- finding the day's sets ----------------------------------------------------------------------------------------

def load_sets(C):
    """Every set file of the captures folder: [(meta, mtime)]. Files that cannot be read are skipped (and counted)."""
    out, bad = [], 0
    try:
        entries = [e for e in os.scandir(C.SETS_DIR) if e.name.endswith(".json")]
    except OSError:
        return [], 0
    for e in entries:
        try:
            meta = json.loads(Path(e.path).read_text(encoding="utf-8"))
            if "id" not in meta:
                raise ValueError("no id")
            out.append((meta, e.stat().st_mtime))
        except (OSError, ValueError):
            bad += 1
    return out, bad


def files_of(meta, C):
    """The files of one cut: [(path, kind, role, size)] for the pictures, thumbnails, per-camera JSON and set JSON that
    exist. kind: image | thumb | view_json | set_json."""
    from ..storage.paths import image_path, meta_path, primary_role, thumb_path, view_meta_path
    sid, primary = meta["id"], primary_role(meta)
    found = []
    for role in list(meta.get("views") or [primary]):
        is_primary = role == primary
        for kind, path in (("image", image_path(sid, role, primary=is_primary)),
                           ("thumb", thumb_path(sid, role, primary=is_primary)),
                           ("view_json", view_meta_path(sid, role))):
            try:
                found.append((path, kind, role, path.stat().st_size))
            except OSError:
                pass
    path = meta_path(sid)
    found.append((path, "set_json", "-", path.stat().st_size if path.exists() else 0))
    return found


def collect(day, C, now=None):
    """The passes of `day`: [{"key", "time", "cuts": [meta...], "files": [...], "info": [summary...]}], and what was left
    out: {"unsettled": passes saved a moment ago, "unreadable": set files that could not be read}.
    The day of a pass is the day of its first cut (a pass that crosses midnight stays in one piece)."""
    from ..storage.shoes import ShoeIndex
    now = time.time() if now is None else now
    metas, bad = load_sets(C)
    passes = {}
    for meta, mtime in metas:
        key = (meta.get("burst") or {}).get("id") or meta["id"]
        p = passes.setdefault(key, {"key": key, "cuts": [], "mtime": 0.0})
        p["cuts"].append(meta)
        p["mtime"] = max(p["mtime"], mtime)
    chosen, unsettled = [], 0
    for p in passes.values():
        first = min((m.get("timestamp") or "") for m in p["cuts"])
        p["time"] = first
        if first[:10] != day.isoformat():
            continue
        if now - p["mtime"] < SETTLE_SEC:
            unsettled += 1
            continue
        p["cuts"].sort(key=lambda m: (m.get("timestamp") or "", m["id"]))
        p["info"] = [ShoeIndex._summary(m) for m in p["cuts"]]
        p["files"] = [f for m in p["cuts"] for f in files_of(m, C)]
        chosen.append(p)
    chosen.sort(key=lambda p: p["time"])
    return chosen, {"unsettled": unsettled, "unreadable": bad}


# ---- statistics ----------------------------------------------------------------------------------------------------

def statistics(passes, roles):
    """Everything the readme says, as plain numbers."""
    from ..storage.shoes import SIZE_LABELS, _size_class
    stats = {"passes": len(passes), "cuts": 0, "images": 0, "bytes": 0, "first": None, "last": None,
             "views": {r: {"passes": 0, "cuts": 0, "images": 0, "image_bytes": 0, "thumb_bytes": 0, "json_bytes": 0}
                       for r in roles},
             "json_bytes": 0, "hours": {}, "labels": {}, "partial": []}
    qr = {"ok": 0, "fail": 0, "seen": 0, "sources": {}, "methods": {}, "views": {}, "read_sizes": [0] * len(SIZE_LABELS),
          "unread_sizes": [0] * len(SIZE_LABELS), "read_no_size": 0, "shoes": {}, "no_id": []}
    for p in passes:
        views_in_pass = set()
        for meta, info, in zip(p["cuts"], p["info"]):
            stats["cuts"] += 1
            expected = set(info["views"])
            for r in expected - set(info["present"]):
                stats["partial"].append((p["key"], meta["id"], r, (meta.get("missing_views") or {}).get(r, "picture missing")))
            status = info["status"]
            stats["labels"][status] = stats["labels"].get(status, 0) + 1
        for path, kind, role, size in p["files"]:
            stats["bytes"] += size
            if kind == "set_json":
                stats["json_bytes"] += size
                continue
            v = stats["views"].setdefault(role, {"passes": 0, "cuts": 0, "images": 0, "image_bytes": 0, "thumb_bytes": 0,
                                                 "json_bytes": 0})
            if kind == "image":
                v["images"] += 1
                v["image_bytes"] += size
                views_in_pass.add(role)
                stats["images"] += 1
            elif kind == "thumb":
                v["thumb_bytes"] += size
            else:
                v["json_bytes"] += size
        for r in views_in_pass:
            stats["views"][r]["passes"] += 1
        for info in p["info"]:
            for r in info["present"]:
                stats["views"].setdefault(r, {"passes": 0, "cuts": 0, "images": 0, "image_bytes": 0, "thumb_bytes": 0,
                                              "json_bytes": 0})["cuts"] += 1
        t = p["time"]
        stats["first"] = min(stats["first"] or t, t)
        stats["last"] = max(stats["last"] or t, t)
        size = sum(f[3] for f in p["files"])
        hour = int(t[11:13]) if t[11:13].isdigit() else -1
        h = stats["hours"].setdefault(hour, {"passes": 0, "ok": 0, "fail": 0, "images": 0, "bytes": 0})
        h["passes"] += 1
        h["images"] += sum(1 for f in p["files"] if f[1] == "image")
        h["bytes"] += size
        # ---- QR, per pass
        ok = [i for i in p["info"] if i["shoe_id"] is not None]
        read_px = max((i["read_px"] or 0 for i in p["info"]), default=0) or None
        seen_px = max((i["seen_px"] or 0 for i in p["info"]), default=0) or None
        if ok:
            qr["ok"] += 1
            h["ok"] += 1
            shoe = ok[0]["shoe_id"]
            source = next((i["source"] for i in ok if i["source"]), None) or "unknown"
            qr["sources"][source] = qr["sources"].get(source, 0) + 1
            best = max((c for m in p["cuts"] for c in (m.get("qr") or []) if c.get("size_px")),
                       key=lambda c: c["size_px"], default=None)
            if best:
                qr["methods"][best.get("method") or "unknown"] = qr["methods"].get(best.get("method") or "unknown", 0) + 1
                qr["views"][best["view"]] = qr["views"].get(best["view"], 0) + 1
                qr["read_sizes"][_size_class(read_px or best["size_px"])] += 1
            else:
                qr["views"]["(live scan only)"] = qr["views"].get("(live scan only)", 0) + 1
                qr["read_no_size"] += 1
            s = qr["shoes"].setdefault(shoe, {"passes": 0, "first": t, "last": t, "source": source})
            s["passes"] += 1
            s["first"], s["last"] = min(s["first"], t), max(s["last"], t)
        else:
            qr["fail"] += 1
            h["fail"] += 1
            if seen_px:
                qr["seen"] += 1
                qr["unread_sizes"][_size_class(seen_px)] += 1
            qr["no_id"].append((t, p["key"], seen_px))
    stats["qr"] = qr
    stats["size_labels"] = SIZE_LABELS
    return stats


def build_readme(day, stats, roles, extra):
    """The readme.md of the backup: markdown text."""
    q = stats["qr"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = [MARKER, f"# 캡처 백업 통계 {day.isoformat()}", "",
         f"- 만든 시각: {now}", f"- 대상 날짜: **{day.isoformat()}** (세트의 첫 촬영 시각 기준)",
         f"- 세트(신발 한 번 지나감): **{stats['passes']}** · 컷: {stats['cuts']} · 이미지: **{stats['images']}** · "
         f"전체 용량: **{fmt_bytes(stats['bytes'])}** (사진 + 썸네일 + JSON)",
         f"- 첫 촬영: {(stats['first'] or '-').replace('T', ' ')} · 마지막 촬영: {(stats['last'] or '-').replace('T', ' ')}",
         f"- 압축 파일: `{extra['zip_name']}`", ""]
    if extra.get("left_out"):
        L += [f"- 이번에 제외된 것: {extra['left_out']}", ""]
    L += ["## 1. View(카메라)별", "",
          "세트 = 그 View의 사진이 있는 신발 통과 수, 컷 = 촬영 시점 수, 이미지 = 사진 파일 수.", ""]
    rows, tot = [], {"passes": 0, "cuts": 0, "images": 0, "image_bytes": 0, "thumb_bytes": 0, "json_bytes": 0}
    for r in roles + sorted(set(stats["views"]) - set(roles)):
        v = stats["views"].get(r)
        if v is None:
            continue
        for k in tot:
            tot[k] += v[k]
        rows.append([r, v["passes"], v["cuts"], v["images"], fmt_bytes(v["image_bytes"]), fmt_bytes(v["thumb_bytes"]),
                     fmt_bytes(v["json_bytes"]), fmt_bytes(v["image_bytes"] + v["thumb_bytes"] + v["json_bytes"]),
                     fmt_bytes(v["image_bytes"] / v["images"]) if v["images"] else "-"])
    rows.append(["**합계**", stats["passes"], stats["cuts"], tot["images"], fmt_bytes(tot["image_bytes"]),
                 fmt_bytes(tot["thumb_bytes"]), fmt_bytes(tot["json_bytes"] + stats["json_bytes"]),
                 fmt_bytes(stats["bytes"]), fmt_bytes(tot["image_bytes"] / tot["images"]) if tot["images"] else "-"])
    L += [table(["View", "세트", "컷", "이미지", "사진 용량", "썸네일 용량", "JSON 용량", "합계", "사진 평균"], rows,
                "lrrrrrrrr"), "",
          "합계의 JSON 용량에는 세트 JSON(`.sets/`)이 포함됩니다.", ""]
    if stats["partial"]:
        L += [f"사진이 빠진 컷: **{len(stats['partial'])}**건 (카메라 끊김 등)", "",
              table(["세트(pass)", "컷", "View", "이유"], stats["partial"][:200]), ""]
        if len(stats["partial"]) > 200:
            L += [f"… 외 {len(stats['partial']) - 200}건", ""]
    L += ["## 2. 시간대별", "",
          table(["시", "세트", "이미지", "용량", "QR 인식", "QR 미인식", "인식률"],
                [[("--" if h < 0 else f"{h:02d}:00"), d["passes"], d["images"], fmt_bytes(d["bytes"]), d["ok"], d["fail"],
                  pct(d["ok"], d["passes"])] for h, d in sorted(stats["hours"].items())], "lrrrrrr"), ""]
    L += ["## 3. QR 인식 결과", "",
          f"- 세트 {stats['passes']}개 중 **인식 {q['ok']}** · **미인식 {q['fail']}** · **인식률 {pct(q['ok'], stats['passes'])}**",
          f"- 미인식 {q['fail']}개 중 QR 위치는 찾았지만 읽지 못함 **{q['seen']}** · 아예 못 찾음 {q['fail'] - q['seen']}",
          f"- 서로 다른 신발(QR 값) **{len(q['shoes'])}**종", ""]
    if q["sources"]:
        L += ["### 값을 읽은 곳", "",
              table(["출처", "세트"], sorted(q["sources"].items(), key=lambda kv: -kv[1])),
              "", "출처: `capture`=저장된 사진, `live`=실시간 스캔, `live_late`=캡처 직후 실시간 스캔, `capture_zoom`=확대 검색", ""]
    if q["views"]:
        L += ["### 어느 카메라에서 읽었나", "", table(["View", "세트"], sorted(q["views"].items(), key=lambda kv: -kv[1])), ""]
    if q["methods"]:
        L += ["### 읽은 방법 (저장된 사진)", "",
              table(["방법", "세트"], sorted(q["methods"].items(), key=lambda kv: -kv[1])),
              "", "`cv`=기본 방식, 그 밖의 이름은 못 읽은 사진을 다시 읽는 추가 시도(`+zxing`=zxing-cpp)입니다.", ""]
    L += ["### QR 크기별 (사진에서 가장 큰 QR의 한 변, px)", "",
          table(["크기(px)", "읽음", "위치만 찾고 못 읽음"],
                [[stats["size_labels"][i], q["read_sizes"][i] or "-", q["unread_sizes"][i] or "-"]
                 for i in range(len(stats["size_labels"]))] + [["실시간 스캔으로 읽음(크기 없음)", q["read_no_size"] or "-", "-"]],
                "lrr"), ""]
    L += ["### 라벨 상태", "", table(["상태", "컷"], sorted(stats["labels"].items())), ""]
    L += ["### 신발(QR 값)별 세트", "",
          table(["QR 값", "세트", "첫 촬영", "마지막 촬영", "출처"],
                [[f"`{k}`", v["passes"], v["first"][11:19], v["last"][11:19], v["source"]]
                 for k, v in sorted(q["shoes"].items(), key=lambda kv: kv[1]["first"])]) if q["shoes"] else "인식된 신발 없음", ""]
    L += [f"### QR 값이 없는 세트 ({len(q['no_id'])})", "",
          table(["촬영 시각", "세트(pass)", "위치만 찾은 QR 크기(px)"],
                [[t.replace("T", " ")[:19], f"`{k}`", round(s) if s else "-"] for t, k, s in q["no_id"][:300]])
          if q["no_id"] else "없음", ""]
    if len(q["no_id"]) > 300:
        L += [f"… 외 {len(q['no_id']) - 300}건", ""]
    L += ["## 4. 압축 파일 안의 구성", "",
          "```",
          "readme.md                 이 파일",
          ".sets/<id>.json          세트(컷)마다 하나: 검출 정보, QR, 라벨",
          "<view>/<id>.jpg|json     카메라(top, back, left, front, right)별 사진과 그 카메라의 JSON",
          ".thumbs/<view>/<id>.jpg  썸네일",
          "```", "",
          f"파일 {extra['files']}개 (readme.md 포함). 압축 안의 경로는 captures 폴더 기준이라 압축을 풀면 그대로 captures 폴더에 합칠 수 있습니다.",
          ""]
    return "\n".join(L)


# ---- zip -----------------------------------------------------------------------------------------------------------

def zip_path(out_dir, day):
    """capture_<date>.zip; a name that exists is not overwritten: capture_<date>_2.zip, _3, ..."""
    candidate = out_dir / f"capture_{day.isoformat()}.zip"
    n = 2
    while candidate.exists() or Path(str(candidate) + ".part").exists():
        candidate = out_dir / f"capture_{day.isoformat()}_{n}.zip"
        n += 1
    return candidate


def write_zip(target, items, captures):
    """items: [(path, arcname)]. Stored without compression (the pictures are JPEG already: it would only take time).
    Written as <name>.part and renamed when it is complete."""
    part = Path(str(target) + ".part")
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for n, (path, arc) in enumerate(items, 1):
            z.write(path, arc)
            if n % 500 == 0:
                log(f"  zipped {n}/{len(items)} files")
    return part


def verify_zip(part, items):
    """Read the zip back: every file's CRC, and the same names and sizes as what was meant to go in. Returns an error
    text or None."""
    with zipfile.ZipFile(part) as z:
        bad = z.testzip()
        if bad:
            return f"the zip is damaged: {bad}"
        inside = {i.filename: i.file_size for i in z.infolist()}
    wanted = {arc: Path(p).stat().st_size for p, arc in items}
    if set(inside) != set(wanted):
        return f"the zip holds {len(inside)} files, {len(wanted)} were meant ({len(set(wanted) ^ set(inside))} differ)"
    wrong = [a for a in wanted if inside[a] != wanted[a]]
    if wrong:
        return f"{len(wrong)} files have another size in the zip (first: {wrong[0]})"
    return None


# ---- the run -------------------------------------------------------------------------------------------------------

def wait_until(target):
    """Sleep until the clock shows `target` (a datetime). The time is read again every round: a Mac that slept
    in between is not a problem."""
    last = None
    while True:
        left = (target - datetime.now()).total_seconds()
        if left <= 0:
            return
        if last is None or last - left >= 600 or left < 60 and (last is None or last - left >= 10):
            log(f"waiting for {target.strftime('%Y-%m-%d %H:%M')} ({int(left // 3600)} h {int(left % 3600 // 60)} min {int(left % 60)} s to go)")
            last = left
        time.sleep(min(left, 30))


def delete_files(items):
    """Delete [(path, arcname)]: the set files (.sets/) first, so that a set is never listed with its pictures gone.
    Returns (how many were deleted, ["name: error", ...] for those that could not be)."""
    deleted, failed = 0, []
    for path, arc in sorted(items, key=lambda it: not it[1].startswith(".sets/")):
        try:
            Path(path).unlink()
            deleted += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            failed.append(f"{arc}: {e}")
    return deleted, failed


def preview(days, C):
    """What a deletion would take, per day, from one look at the set files: {day: (sets, images, bytes)}."""
    metas, _ = load_sets(C)
    passes = {}
    for meta, _mtime in metas:
        key = (meta.get("burst") or {}).get("id") or meta["id"]
        p = passes.setdefault(key, {"time": meta.get("timestamp") or "", "images": 0, "bytes": 0})
        p["time"] = min(p["time"], meta.get("timestamp") or "")
        for path, kind, role, size in files_of(meta, C):
            p["bytes"] += size
            p["images"] += 1 if kind == "image" else 0
    out = {d: [0, 0, 0] for d in days}
    for p in passes.values():
        try:
            d = date.fromisoformat(p["time"][:10])
        except ValueError:
            continue
        if d in out:
            out[d][0] += 1
            out[d][1] += p["images"]
            out[d][2] += p["bytes"]
    return {d: tuple(v) for d, v in out.items()}


def confirm_delete(days, C, args):
    """Deleting without a backup cannot be undone: show what goes and ask. --yes skips the question (a run in the
    background has nobody to ask). Returns True to go on."""
    seen = preview(days, C)
    sets = sum(v[0] for v in seen.values())
    images = sum(v[1] for v in seen.values())
    size = sum(v[2] for v in seen.values())
    for d in days:
        log(f"  {d.isoformat()}: {seen[d][0]} sets, {seen[d][1]} images, {fmt_bytes(seen[d][2])}")
    log(f"WILL DELETE (with NO backup): {sets} sets, {images} images, {fmt_bytes(size)} of {len(days)} day(s)")
    if args.yes or args.dry_run:
        return True
    if not sys.stdin.isatty():
        log("STOP: this run cannot ask (no terminal). Add --yes to delete without asking. Nothing was changed.")
        return False
    answer = input("Type 'delete' to delete them for good: ")
    if answer.strip().lower() != "delete":
        log("cancelled. Nothing was changed.")
        return False
    return True


def days_with_sets(C, upto):
    """The days (date objects, oldest first) that have sets, up to and including `upto`: a pass counts for the day of
    its first cut."""
    metas, _ = load_sets(C)
    first = {}
    for meta, _mtime in metas:
        key = (meta.get("burst") or {}).get("id") or meta["id"]
        t = meta.get("timestamp") or ""
        first[key] = min(first.get(key, t), t)
    days = set()
    for t in first.values():
        try:
            d = date.fromisoformat(t[:10])
        except ValueError:
            continue
        if d <= upto:
            days.add(d)
    return sorted(days)


def backup_day(day, args, C, out_dir):
    """Back up the sets of one day (the steps in the module text). Returns the exit code: 0 done (or nothing to do),
    1 some files could not be deleted, 2 not enough room for the zip, 3 the zip did not check out."""
    passes, left_out = collect(day, C)
    if not passes:
        log(f"no sets of {day.isoformat()} were found" + (f" ({left_out['unsettled']} are still being saved)" if left_out["unsettled"] else ""))
        return 0
    roles = [r for r, _ in C.CAMERAS]
    stats = statistics(passes, roles)
    note = []
    if left_out["unsettled"]:
        note.append(f"{left_out['unsettled']}개 세트는 방금 저장되어 다음 백업으로 미뤘습니다")
    if left_out["unreadable"]:
        note.append(f"읽을 수 없는 세트 파일 {left_out['unreadable']}개")

    items = [(p_[0], str(Path(p_[0]).relative_to(C.CAPTURES_DIR)).replace(os.sep, "/")) for p in passes for p_ in p["files"]]
    total = sum(p_[3] for p in passes for p_ in p["files"])
    target = zip_path(out_dir, day)
    readme_text = build_readme(day, stats, roles, {"zip_name": target.name, "files": len(items) + 1,
                                                  "left_out": ", ".join(note)})
    log(f"{len(passes)} sets, {stats['cuts']} cuts, {stats['images']} images, {len(items)} files, {fmt_bytes(total)}; "
        f"QR read in {stats['qr']['ok']} of {stats['passes']} sets")
    if args.delete:   # no zip, no readme: the data is only deleted
        if args.dry_run:
            log(f"dry run: would DELETE {len(items)} files ({fmt_bytes(total)}) of {len(passes)} sets, with no backup")
            return 0
        deleted, failed = delete_files(items)
        log(f"deleted {deleted} files ({len(passes)} sets, {fmt_bytes(total)}) of {day.isoformat()} from {C.CAPTURES_DIR}, no backup"
            + (f"; {len(failed)} could not be deleted (first: {failed[0]})" if failed else ""))
        return 1 if failed else 0
    if args.dry_run:
        log(f"dry run: would write {C.CAPTURES_DIR / 'readme.md'}, make {target} and "
            + ("keep" if args.keep else "delete") + f" {len(items)} files")
        print("\n" + readme_text[:3000] + ("\n…" if len(readme_text) > 3000 else ""))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    if free < total * FREE_MARGIN + MIN_FREE_BYTES:
        log(f"STOP: {fmt_bytes(free)} free in {out_dir}, the zip needs about {fmt_bytes(total * FREE_MARGIN + MIN_FREE_BYTES)}. Nothing was changed.")
        return 2

    readme = C.CAPTURES_DIR / "readme.md"
    try:   # a readme of somebody else's is kept
        if readme.exists() and MARKER not in readme.read_text(encoding="utf-8", errors="replace")[:200]:
            readme.replace(C.CAPTURES_DIR / "readme.previous.md")
    except OSError:
        pass
    readme.write_text(readme_text, encoding="utf-8")
    log(f"wrote {readme}")
    items.append((readme, "readme.md"))

    log(f"zipping {len(items)} files to {target.name} ...")
    started = time.time()
    try:
        part = write_zip(target, items, C.CAPTURES_DIR)
        problem = verify_zip(part, items)
    except (OSError, zipfile.BadZipFile) as e:
        problem = f"{type(e).__name__}: {e}"
        part = Path(str(target) + ".part")
    if problem:
        log(f"STOP: {problem}. Nothing was deleted; the incomplete zip {part.name} was left for a look.")
        return 3
    part.replace(target)
    log(f"zip ready and checked: {target} ({fmt_bytes(target.stat().st_size)}, {time.time() - started:.0f} s)")

    if args.keep:
        log("--keep: the files stay in the captures folder")
        return 0
    # the statistics (readme.md) stay in the captures folder: the next backup replaces them
    deleted, failed = delete_files([(p, a) for p, a in items if a != "readme.md"])
    log(f"deleted {deleted} files from {C.CAPTURES_DIR}" + (f"; {len(failed)} could not be deleted (first: {failed[0]})" if failed else ""))
    log("done. The app notices the removed sets within two minutes.")
    return 1 if failed else 0



def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="the day to back up, YYYY-MM-DD (default: today)")
    ap.add_argument("--all", action="store_true",
                    help="every day that has sets up to the day (today unless --date), oldest first, one zip per day")
    ap.add_argument("--at", default="23:59", help="start at this time of the day (default 23:59)")
    ap.add_argument("--now", action="store_true", help="do not wait for the time")
    ap.add_argument("--out", help="folder for the zip (default ON_BACKUP_DIR, backups/ in the project)")
    ap.add_argument("--keep", action="store_true", help="make the zip but do not delete the files from captures")
    ap.add_argument("--dry-run", action="store_true", help="say what would be done, change nothing")
    ap.add_argument("--delete", action="store_true",
                    help="NO backup: only delete the day's data (with --all: every day up to the day). Asks first")
    ap.add_argument("--yes", action="store_true", help="with --delete: do not ask (needed when there is no terminal)")
    args = ap.parse_args(argv)
    if args.delete and args.keep:
        ap.error("--delete and --keep contradict each other")

    from ..core import config as C
    day = date.fromisoformat(args.date) if args.date else date.today()
    hh, mm = (int(x) for x in args.at.split(":"))
    start = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
    out_dir = Path(args.out).expanduser() if args.out else C.BACKUP_DIR
    if args.delete:
        log(f"DELETE the sets of {'every day up to ' if args.all else ''}{day.isoformat()} from {C.CAPTURES_DIR}, with NO backup"
            + (" (dry run)" if args.dry_run else ""))
        if not confirm_delete(days_with_sets(C, day) if args.all else [day], C, args):
            return 4
    else:
        log(f"back up the sets of {'every day up to ' if args.all else ''}{day.isoformat()} from {C.CAPTURES_DIR} to {out_dir}"
            + (" (dry run)" if args.dry_run else " (the files are deleted after the zip is checked)" if not args.keep else " (files are kept)"))
    if not args.now and not args.dry_run and datetime.now() < start:
        wait_until(start)
    elif not args.now and datetime.now() >= start and day == date.today():
        log(f"it is past {args.at} already: starting now")

    days = days_with_sets(C, day) if args.all else [day]
    if args.all:
        log(f"{len(days)} day(s) with sets: " + (", ".join(d.isoformat() for d in days) or "none"))
    code = 0
    for d in days:
        if args.all:
            log(f"==== {d.isoformat()} ====")
        code = max(code, backup_day(d, args, C, out_dir))
        if code >= 2:   # no room / a bad zip: the next day would meet the same problem
            log("stopping here: the later days are left as they are")
            break
    return code


if __name__ == "__main__":
    sys.exit(main())
