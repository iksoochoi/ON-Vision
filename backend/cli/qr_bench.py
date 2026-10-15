"""Compare the ways of reading the QR code on a folder of saved pictures (the app must not be needed: no camera).

    .venv/bin/python -m backend.cli.qr_bench <folder> [--roi 0,0.45,1,0.35]

Runs every .jpg / .png of the folder (and its sub folders) three times with the same settings the app uses:
  plain        ON_QR_ENHANCE=0       the reading of before the extra attempts
  enhanced     ON_QR_ENHANCE=1       cut around the located code, straighten, sharpen / deblur, other contrast
  enhanced+zx  ... and zxing-cpp     (needs the zxing-cpp package)
and prints how many pictures were read, the time per picture and which attempt read them. Use a COPY of the
pictures (this tool only reads them). Say which pictures are known to carry a code with --expect TEXT to count only
reads of that value."""

import argparse
import sys
import time
from pathlib import Path

EXTENSIONS = {".jpg", ".jpeg", ".png"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--roi", default="", help="search area x,y,w,h as fractions (default: ON_QR_ROI of the .env)")
    ap.add_argument("--expect", default="", help="count a picture as read only when this exact value was read")
    args = ap.parse_args()

    import cv2
    from ..core import config as C
    from ..qr import decode, enhance

    if args.roi:
        C.QR_ROI = C._parse_roi(args.roi)
    files = sorted(p for p in Path(args.folder).rglob("*") if p.suffix.lower() in EXTENSIONS)
    if not files:
        raise SystemExit(f"no pictures in {args.folder}")
    roi = "x,y,w,h = " + ",".join(f"{v:g}" for v in C.QR_ROI) if C.QR_ROI else "the whole picture"
    print(f"{len(files)} picture(s), search area: {roi}, extra-attempt budget {C.QR_ENHANCE_SEC:g} s per picture")
    zx = enhance.zxing_ready()
    if not zx:
        print("zxing-cpp is not available: the third run is skipped")
    modes = [("plain", False, False), ("enhanced", True, False)] + ([("enhanced+zx", True, True)] if zx else [])
    images = [(p, cv2.imread(str(p))) for p in files]
    table = {}
    for name, enh, use_zx in modes:
        C.QR_ENHANCE, C.QR_ZXING = enh, use_zx
        read, spent, methods, texts = 0, 0.0, {}, {}
        for path, img in images:
            if img is None:
                continue
            started = time.monotonic()
            found = decode.decode_qr(img, roi=C.QR_ROI, seen=[], deep=True)
            spent += time.monotonic() - started
            found = [c for c in found if not args.expect or c["text"] == args.expect]
            if found:
                read += 1
                methods[found[0].get("method", "?")] = methods.get(found[0].get("method", "?"), 0) + 1
                texts[path] = found[0]["text"]
        table[name] = (read, spent, methods, texts)
    print()
    for name, (read, spent, methods, _) in table.items():
        print(f"{name:<12} read {read:>4}/{len(images)} ({100 * read / len(images):4.0f}%)   {spent / len(images):5.2f} s per picture")
        print("             by: " + (", ".join(f"{k} {v}" for k, v in sorted(methods.items(), key=lambda kv: -kv[1])) or "-"))
    best = table[modes[-1][0]][3]
    base = table["plain"][3]
    new = [p for p in best if p not in base]
    if new:
        print(f"\nread only by the extra attempts ({len(new)}):")
        for p in new[:40]:
            print(f"  {p}  ->  {best[p]}")
    lost = [p for p in base if p not in best]
    if lost:
        print(f"\nREAD BEFORE BUT NOT NOW ({len(lost)}): the extra attempts must not lose reads, tell the developer")
        for p in lost[:20]:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
