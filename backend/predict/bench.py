"""How much does the defect model cost on THIS machine? Run it on the Mac Mini and send back the printed table / the json.

    python backend/predict/bench.py [--src captures] [--n 5] [--runs 3] [--devices auto|cpu,mps] [--imgsz 1280,960,640]
                                    [--threads 0,2] [--out bench.json]

Takes --n pictures (the first ones found under --src, only READ), runs every combination of device / size / thread
count --runs times over them and prints, per combination:
  ms/pic    time of one picture (median), after one warm-up run
  set (5)   what a shoe pass with five pictures takes
  cpu %     CPU the process used while predicting, in percent of ONE core (200 = two cores busy)
  rss MB    memory of the process afterwards
A shoe passes about every 10 s: the 'set (5)' time must stay well below that, and the cpu % shows what the camera app
would have to share the machine with. Torch threads: 0 = torch's default (all cores), N = at most N."""

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2  # noqa: E402

from backend.predict import engine  # noqa: E402


def _rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(engine.ROOT / "captures"), help="a folder (or one picture) to take pictures from")
    ap.add_argument("--n", type=int, default=5, help="pictures per run (5 = one shoe pass)")
    ap.add_argument("--runs", type=int, default=3, help="repeats of the n pictures")
    ap.add_argument("--devices", default="auto", help="comma-separated: auto, cpu, mps, 0 (all that exist are tried)")
    ap.add_argument("--imgsz", default="1280,960", help="comma-separated input sizes")
    ap.add_argument("--threads", default="0", help="comma-separated torch thread counts (0 = default)")
    ap.add_argument("--model", help="model file")
    ap.add_argument("--out", help="also write the table as json")
    args = ap.parse_args()

    import torch
    files = engine.find_images(args.src, args.n)
    if not files:
        sys.exit(f"no pictures under {args.src}")
    images = [cv2.imread(str(p)) for p in files]
    images = [im for im in images if im is not None]
    print(f"{platform.platform()} | {platform.processor() or platform.machine()} | {os.cpu_count()} cores | torch {torch.__version__}")
    print(f"{len(images)} pictures from {args.src} ({images[0].shape[1]}x{images[0].shape[0]}), {args.runs} runs each\n")

    devices = []
    for d in args.devices.split(","):
        d = engine.pick_device(d.strip())
        if d == "mps" and not torch.backends.mps.is_available():
            continue
        if d not in devices:
            devices.append(d)
    rows = []
    default_threads = torch.get_num_threads()
    print(f"{'device':<7}{'imgsz':>6}{'threads':>8}{'ms/pic':>9}{'set (5)':>10}{'cpu %':>8}{'rss MB':>8}")
    for device in devices:
        model, device = engine.load(args.model, device)
        for imgsz in (int(v) for v in args.imgsz.split(",")):
            for threads in (int(v) for v in args.threads.split(",")):
                torch.set_num_threads(threads or default_threads)
                engine.predict(model, device, images[0], imgsz)   # warm-up (kernels, memory)
                times, wall0, cpu0 = [], time.perf_counter(), time.process_time()
                for _ in range(args.runs):
                    for im in images:
                        times.append(engine.predict(model, device, im, imgsz)["ms"])
                wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
                ms = statistics.median(times)
                row = {"device": device, "imgsz": imgsz, "threads": threads or default_threads, "ms_per_picture": round(ms, 1),
                       "ms_max": round(max(times), 1), "set5_s": round(ms * 5 / 1000, 2), "cpu_percent": round(100 * cpu / wall),
                       "rss_mb": round(_rss_mb())}
                rows.append(row)
                print(f"{device:<7}{imgsz:>6}{row['threads']:>8}{ms:>9.0f}{row['set5_s']:>9.1f}s{row['cpu_percent']:>8}{row['rss_mb']:>8}")
    torch.set_num_threads(default_threads)
    if args.out:
        Path(args.out).write_text(json.dumps({"machine": platform.platform(), "cores": os.cpu_count(), "torch": torch.__version__, "rows": rows}, indent=1), encoding="utf-8")
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
