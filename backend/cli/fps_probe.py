"""Measure what ONE camera really delivers for a requested frame rate (the app must not be running).

    ./bin/install_launchd.sh uninstall                  # the service holds the cameras: stop it first
    .venv/bin/python -m backend.cli.fps_probe --sweep   # several requests, one table
    .venv/bin/python -m backend.cli.fps_probe --fps 10 --size 4208x3120 --device 1944301081DC6E2600

Per run it opens the camera with the same stream settings as the app (a capture stream and a 640x480 preview),
reads frames for --seconds and prints
    delivered   frames per second that arrived on the host
    sensor      frame numbers per second of the device's clock (a gap between numbers = frames dropped on the
                device; sensor much higher than delivered = the device drops, sensor = delivered = the sensor
                itself runs at that rate)
    gaps        missing frame numbers
The --sweep table compares the plain request with one that also sets the sensor's own rate (build(sensorFps=...)),
which shows which of the two the camera follows. Every run finds the camera again (it restarts after each run)."""

import argparse
import sys
import time

WARMUP_SEC = 2.0


def parse_size(text):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def find_device(device_id, wait_sec=0.0):
    """(info, id) of the camera. A camera that was closed restarts and shows up again after a few seconds, with a
    new info: ask again every time, for up to wait_sec, never reuse an old info."""
    import depthai as dai
    deadline = time.monotonic() + wait_sec
    while True:
        devices = dai.Device.getAllAvailableDevices()
        for info in devices:
            try:
                found = str(info.getDeviceId())
            except AttributeError:
                found = str(info.deviceId)
            if not device_id or found.endswith(device_id):
                return info, found
        if time.monotonic() >= deadline:
            raise RuntimeError(f"camera {device_id or '(any)'} not found among {len(devices)} device(s)"
                               " (is the app still running? stop the service first)")
        time.sleep(1.0)


def _build(pipeline, sensor_fps):
    import depthai as dai
    node = pipeline.create(dai.node.Camera)
    if not sensor_fps:
        return node.build()
    try:
        return node.build(sensorFps=sensor_fps)
    except TypeError:   # an older signature: build(socket, resolution, fps)
        return node.build(dai.CameraBoardSocket.CAM_A, None, sensor_fps)


def probe(device_id, size, fps, seconds, sensor_fps=None, preview_fps=None):
    """Run one measurement of the camera `device_id`. Returns a dict."""
    import depthai as dai
    nv12 = dai.ImgFrame.Type.NV12
    info, _ = find_device(device_id, wait_sec=40)
    device = dai.Device(info)
    try:
        pipeline = dai.Pipeline(device)
        cam = _build(pipeline, sensor_fps)
        full = cam.requestOutput(size=size, type=nv12, fps=fps)
        preview = cam.requestOutput(size=(640, 480), type=nv12, fps=preview_fps or fps)
        q_full = full.createOutputQueue(maxSize=4, blocking=False)
        q_preview = preview.createOutputQueue(maxSize=4, blocking=False)
        pipeline.start()
        speed = getattr(device.getUsbSpeed(), "name", "?")
        n, first, last, started, latest = 0, None, None, None, None
        t0 = time.monotonic()
        while time.monotonic() - t0 < WARMUP_SEC + seconds:
            q_preview.tryGet()   # drained like the app does
            msg = q_full.tryGet()
            if msg is None:
                time.sleep(0.002)
                continue
            if time.monotonic() - t0 < WARMUP_SEC:
                continue   # the first frames come at a different pace
            now = time.monotonic()
            stamp = (msg.getTimestamp().total_seconds(), msg.getSequenceNum())
            if first is None:
                first, started = stamp, now
            last, n, latest = stamp, n + 1, now
        pipeline.stop()
        if n < 2:
            return {"usb": speed, "n": n}
        span, arrived = last[0] - first[0], latest - started
        frames = last[1] - first[1]
        return {"usb": speed, "n": n, "delivered": (n - 1) / max(arrived, 1e-6), "by_time": (n - 1) / span if span > 0 else 0,
                "sensor": frames / span if span > 0 else 0, "gaps": frames - (n - 1)}
    finally:
        try:
            device.close()
        except Exception:
            pass


def show(label, result):
    if result.get("n", 0) < 2:
        print(f"{label:<34} no frames (usb={result.get('usb')})")
        return
    print(f"{label:<34} delivered {result['delivered']:5.1f}  sensor {result['sensor']:5.1f}  gaps {result['gaps']:4d}"
          f"   usb={result['usb']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--device", default="", help="device id or its unique ending (backend.cli.list_cameras)")
    ap.add_argument("--size", default="4208x3120", help="capture size, default 4208x3120")
    ap.add_argument("--fps", type=float, default=10, help="requested capture fps")
    ap.add_argument("--preview-fps", type=float, default=0, help="requested preview fps (default: same as --fps)")
    ap.add_argument("--sensor-fps", type=float, default=0, help="also set the sensor's own rate: build(sensorFps=...)")
    ap.add_argument("--seconds", type=float, default=8, help="measuring time per run")
    ap.add_argument("--sweep", action="store_true", help="try 5, 8, 10, 12, 15 fps (10 and 15 also with sensorFps)")
    args = ap.parse_args()

    try:
        _, device_id = find_device(args.device)
    except RuntimeError as e:
        raise SystemExit(str(e))
    size = parse_size(args.size)
    print(f"camera {device_id}, capture {size[0]}x{size[1]}, {args.seconds:g} s per run (a run takes ~{int(args.seconds + 8)} s)")
    if not args.sweep:
        show(f"request {args.fps:g} fps" + (f", sensorFps {args.sensor_fps:g}" if args.sensor_fps else ""),
             probe(device_id, size, args.fps, args.seconds, args.sensor_fps or None, args.preview_fps or None))
        return
    for fps in (5, 8, 10, 12, 15):
        for sensor in ((None, fps) if fps in (10, 15) else (None,)):
            label = f"request {fps} fps" + (f" + sensorFps {fps}" if sensor else "")
            try:
                show(label, probe(device_id, size, fps, args.seconds, sensor, args.preview_fps or None))
            except Exception as e:  # noqa: BLE001 - a failed run must not end the sweep
                print(f"{label:<34} failed: {type(e).__name__}: {e}")
            sys.stdout.flush()
            time.sleep(3)   # the device restarts when it is closed: it needs a moment before it is opened again


if __name__ == "__main__":
    main()
