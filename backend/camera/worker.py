"""DepthAI workers: one thread per camera.

Every camera streams (all share the same 4:3 field of view):
    full      capture size (per camera setting), kept in the slot's frame ring
    preview   MJPEG preview (per camera resolution; the reference camera gets the detection overlay)
The reference camera (default "top") also detects the shoe:
    ON_DETECTOR=model:      YOLO DetectionNetwork on the camera (extra stream at the model input size)
    ON_DETECTOR=background: its own preview frames are compared with the empty-belt reference
                            (no extra stream, so no extra USB traffic)

Every detection feeds CenterTrigger (backend/detect/trigger.py). When it fires, all cameras contribute the
frame closest in time to each shot (before / at / after the center) and each set is stored.

Resolution changes and a new reference camera rebuild the pipeline of the cameras concerned
(slot.request_restart). A camera that is missing or unplugged is retried every few seconds;
the others keep running."""

import threading
import time

import cv2
import depthai as dai

from ..capture.session import TriggerSession, capture_set, describe_frame
from ..capture.presence import presence
from ..capture.sharpness import luma_small
from ..core import config as C
from ..core.events import broadcast
from ..core.rotation import rotate_dets, rotate_image, wrap_frame
from ..core.state import state, state_lock
from ..detect.background import bg_dets, get_background
from ..detect.model import poll_detections, msg_ts
from ..health import collect
from .controls import (FOCUS_SETTLE_SEC, apply_exposure, apply_focus, apply_isp, apply_lens, isp_setters,
                       lens_position)
from .device import (KNOWN_DEVICE_RETRY_SEC, KNOWN_DEVICE_TRIES, NOT_FOUND_RETRY_SEC, SLOW_USB, DeviceNotFound,
                     claim_device, print_assignment, release_device, usb_speed)
from .overlay import draw_label, draw_overlay
from .pipeline import build_pipeline
from .registry import registry
from .settings_guard import GIVE_UP_AFTER, SettingsGuard

RETRY_SEC = 5
ESCALATE_EVERY_SEC = 600.0   # a camera that ignores its settings is restarted at most this often
RESUME_GAP_SEC = 3.0         # pictures that come back after a pause this long: the camera may have lost its settings

_BOOT_LOCK = threading.Lock()   # boot one OAK at a time; simultaneous USB boots are unreliable
_escalated = {}                 # role -> when its camera was last restarted because it ignored its settings


def zoomed_preview(slot):
    """The zoomed view for the live picture, made from the newest full-size frame (the preview stream
    cannot be cropped: it is small). Only the cropped part is converted, then shrunk to the preview
    size. None when there is no frame yet."""
    item = slot.latest()
    if item is None:
        return None
    img = item[1].getCvFrame()
    scale = max(slot.preview_size) / max(img.shape[:2])
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img


def _camera_loop(slot, pipeline, q, stop):
    """Run until stopped, the device drops, or a restart is requested (returns True for restart)."""
    role = slot.role
    setters = isp_setters()
    last_exposure = (None, None, None)
    last_lens = 0         # 0 = the camera starts in continuous autofocus: nothing to send until it changes
    focus_settle = None   # when to read the lens position after a click-focus
    last_cam = {}
    session = None
    last_dets = []
    bg_frames = None  # preview frames collected while a background capture is running
    rotation = slot.rotation
    guard = SettingsGuard() if C.SETTINGS_GUARD else None   # does the camera follow the exposure it was told to use?
    last_frame = None     # when the last picture came (a pause in the pictures: see RESUME_GAP_SEC)

    while not stop.is_set() and pipeline.isRunning():
        if slot.take_restart():
            print(f"[{role}] restarting with new settings")
            return True

        if slot.rotation != rotation:   # the view turned: tracks and half-collected frames are stale
            rotation = slot.rotation
            print(f"[{role}] rotation -> {rotation}°")
            if session is not None:
                session.reset()
            last_dets = []
            if bg_frames is not None:
                bg_frames = []

        crop = slot.view_crop()   # zoom of the view (None = whole view); never on the reference camera
        frame = q["full"].tryGet()
        # small luma copies: for the sharpest-frame pick and, on the reference camera, the adjacent shots
        need_gray = C.PICK_BEST or C.PICK_WINDOW_SEC > 0 or (slot.is_trigger and C.ADJACENT)
        got_frame = frame is not None
        if got_frame:
            now = time.monotonic()
            if last_frame is not None and now - last_frame >= RESUME_GAP_SEC:
                # The pictures stopped for a while and are back (10-24 12:23: LEFT, 12 s, no restart of the app's camera).
                # A camera that hiccups like this has come back with its default settings (automatic exposure of 30 ms,
                # automatic focus ...) without telling anyone: send everything again at once.
                message = (f"the pictures are back after {now - last_frame:.0f} s: the camera may have lost its settings, "
                           "sending them again")
                print(f"[{role}] {message}")
                collect.emit("CAMERA_SETTINGS_REAPPLIED", "INFO", message, camera=role, alert=False)
                last_exposure, last_lens = (None, None, None), None
                last_cam.clear()
            last_frame = now
        while frame is not None:
            gray = luma_small(frame, rotation) if need_gray else None
            slot.push_frame(msg_ts(frame), wrap_frame(frame, rotation, crop), gray)
            if C.PICK_BEST and not slot.is_trigger:   # the empty belt, to tell later whether a shoe is in the picture
                presence.observe(role, gray, rotation)
            slot.count("full")
            frame = q["full"].tryGet()

        params = slot.snapshot_params()
        apply_isp(q["control"], setters, params, last_cam)
        sent_before = last_exposure
        last_exposure = apply_exposure(
            q["control"], params["iso"], params["exposure_ms"], params["ae_limit_ms"],
            last_exposure, role,
        )
        last_lens = apply_lens(q["control"], params["lens_position"], last_lens, role)
        if guard is not None:
            if last_exposure != sent_before:
                guard.sent()   # the pictures need a moment to show the new settings
            elif got_frame:
                item = slot.latest()
                info = describe_frame(item[1]) if item else {}
                action = guard.check(params, info.get("exposure_us"), info.get("iso"))
                if action == "resend":
                    first = guard.resends == 1
                    message = (f"the camera does not follow its settings ({guard.reason}): sending them again "
                               f"({guard.resends}/{GIVE_UP_AFTER})")
                    print(f"[{role}] {message}")
                    collect.emit("CAMERA_SETTINGS_LOST", "WARNING", message, camera=role, alert=first,
                                 key=("CAMERA_SETTINGS_LOST", role))
                    last_exposure, last_lens = (None, None, None), None   # everything goes out again at the next turn
                    last_cam.clear()
                elif action == "escalate":
                    now = time.monotonic()
                    restart = now - _escalated.get(role, -1e9) >= ESCALATE_EVERY_SEC
                    message = (f"the camera still ignores its settings after {GIVE_UP_AFTER} tries ({guard.reason}): "
                               + ("restarting the camera" if restart else "restarted a short while ago, not again"))
                    print(f"[{role}] {message}")
                    collect.emit("CAMERA_SETTINGS_IGNORED", "ERROR", message, camera=role,
                                 key=("CAMERA_SETTINGS_IGNORED", role))
                    if restart:
                        _escalated[role] = now
                        slot.request_restart()
                elif action == "restored":
                    message = "the camera follows its settings again"
                    print(f"[{role}] {message}")
                    collect.emit("CAMERA_SETTINGS_RESTORED", "INFO", message, camera=role,
                                 recovers=[("CAMERA_SETTINGS_LOST", role), ("CAMERA_SETTINGS_IGNORED", role)])

        is_ref = slot.is_trigger
        bg_detect = is_ref and C.DETECTOR == "background"
        auto_enabled, manual = True, False
        with state_lock:
            if state["bg_capture_request"] == role:
                state["bg_capture_request"] = None
                bg_frames = []
                print(f"[{role}] Background capture: collecting {C.BG_FRAMES} frames")
            if is_ref:
                auto_enabled = state["auto_enabled"]
                manual = state["manual_capture"]
                state["manual_capture"] = False

        if is_ref and session is None:
            session = TriggerSession()
        elif not is_ref:
            session = None

        if is_ref and C.DETECTOR == "model" and q["det"] is not None:
            for det_ts, dets in poll_detections(q["det"], q["classes"]):
                dets = rotate_dets(dets, rotation)
                last_dets = dets
                session.on_detections(det_ts, dets, auto_enabled, paused=bg_frames is not None)

        preview = q["preview"].tryGet()
        if preview is not None:
            slot.count("preview")
            # convert only when someone needs the picture: live view, background capture, detection
            if slot.stream_on or bg_frames is not None or bg_detect:
                img = zoomed_preview(slot) if crop is not None and bg_frames is None else None
                zoomed = img is not None
                if not zoomed:   # not zoomed (or no zoomed frame yet): the preview stream itself
                    img = rotate_image(preview.getCvFrame(), rotation)
                if bg_detect and bg_frames is None:
                    last_dets = bg_dets(img, role)
                    session.on_detections(msg_ts(preview), last_dets, auto_enabled)
                if bg_frames is not None:
                    bg_frames.append(img.copy())
                    if len(bg_frames) >= C.BG_FRAMES:
                        ok = get_background(role).set_from_frames(bg_frames)
                        bg_frames = None
                        if session is not None:
                            session.reset()
                        last_dets = []
                        print(f"[{role}] Background saved" if ok else f"[{role}] Background save failed")
                        broadcast({"key": "background", "role": role, "value": ok})
                if slot.stream_on:
                    if is_ref:
                        note = None
                        if bg_frames is not None:
                            note = f"capturing background {len(bg_frames)}/{C.BG_FRAMES}"
                        elif C.DETECTOR == "background" and not get_background(role).exists:
                            note = "NO BACKGROUND - empty the belt and press 'background'"
                        draw_overlay(img, last_dets, auto_enabled, note)
                    elif bg_frames is not None:
                        draw_overlay(img, [], False, f"capturing background {len(bg_frames)}/{C.BG_FRAMES}")
                    draw_label(img, role.upper() + (f"  x{slot.zoom:g}" if zoomed else ""))
                    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80 if is_ref else 70])
                    if ok:
                        slot.set_preview(buf.tobytes())

        focus = slot.take_focus_request()
        latest = slot.latest()
        if focus is not None and latest is not None:
            apply_focus(q["control"], focus, latest[1], role)
            focus_settle = time.monotonic() + FOCUS_SETTLE_SEC
        if focus_settle is not None and time.monotonic() >= focus_settle:
            focus_settle = None
            item = slot.latest()
            pos = lens_position(item[1]) if item else None
            if pos:   # keep where the autofocus ended: locked now, and remembered across restarts
                slot.set_param("lens_position", pos)
                registry.save_settings()
                broadcast({"key": "cam", "role": role, "name": "lens_position", "value": pos})
                print(f"[{role}] Focus settled at lens position {pos} (locked)")

        if manual and capture_set("manual") is None:
            print("Manual capture requested but no frame yet")

        time.sleep(0.002)
    return False


def _run_camera_once(slot, stop, claimed_event=None):
    """Open the camera, run it until it stops. Returns True when a restart was requested."""
    try:
        info, device_id = claim_device(slot)
    finally:
        if claimed_event is not None:
            claimed_event.set()   # lets run_pipeline start the next camera (keeps the role order)
    device = pipeline = None
    try:
        with _BOOT_LOCK:
            slot.take_restart()   # the settings are applied by this build
            device = dai.Device(info)
            slot.usb_speed = usb_speed(device)
            slow = slot.usb_speed in SLOW_USB
            pipeline = dai.Pipeline(device)
            queues = build_pipeline(pipeline, slot, slow)
            pipeline.start()
        slot.actual_id = device_id
        slot.set_online(True)
        capture = "full" if slot.capture_size is None else "%dx%d" % slot.capture_size
        print(f"[{slot.role}] online ({device_id}) USB {slot.usb_speed or '?'}, preview "
              f"{slot.preview_size[0]}x{slot.preview_size[1]}, capture {capture}"
              f"{', reference' if slot.is_trigger else ''}")
        if slow:
            print(f"[{slot.role}] WARNING: USB2 link - rates lowered. Use a USB3 port/cable (blue) "
                  f"or a powered USB3 hub; hubs and long cables often fall back to USB2.")
        print_assignment()
        return _camera_loop(slot, pipeline, queues, stop)
    finally:
        for closer in (getattr(pipeline, "stop", None), getattr(device, "close", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass
        release_device(slot.role)


def _camera_thread(slot, stop, claimed_event=None):
    """Keep one camera running: reconnect after failures (no device yet, USB drop, ...)
    and rebuild it right away when its settings changed."""
    last_reason, repeats = None, 0
    while not stop.is_set():
        restart, retry = False, RETRY_SEC
        try:
            restart = _run_camera_once(slot, stop, claimed_event)
            reason = "restarting" if restart else "pipeline stopped"
        except DeviceNotFound as e:
            known = bool(slot.device_id or slot.actual_id)
            reason = f"DeviceNotFound: {e}"
            retry = KNOWN_DEVICE_RETRY_SEC if known and repeats < KNOWN_DEVICE_TRIES else NOT_FOUND_RETRY_SEC
        except Exception as e:  # depthai raises RuntimeError for lost connections etc.
            reason = f"{type(e).__name__}: {e}"
        slot.set_online(False, reason)
        if stop.is_set():
            break
        if restart:
            last_reason, repeats = None, 0
            continue
        repeats = repeats + 1 if reason == last_reason else 0
        last_reason = reason
        if repeats % 20 == 0:   # same message again and again is noise: repeat it now and then
            print(f"[{slot.role}] offline: {reason} - retrying every {retry}s")
        if stop.wait(retry):
            break


def start_workers(stop):
    """Start one worker thread per configured camera, in role order. Returns the threads."""
    slots = registry.slots()
    print("Cameras: " + ", ".join(
        f"{s.role}{'*' if s.is_trigger else ''}={s.device_id or 'auto'}" for s in slots)
        + "  (* = reference camera)")
    threads = []
    for s in slots:
        claimed = threading.Event()
        t = threading.Thread(target=_camera_thread, args=(s, stop, claimed), name=f"camera-{s.role}",
                             daemon=True)
        t.start()
        threads.append(t)
        # Roles without a fixed device take the next free OAK in USB order. Letting each camera claim
        # its OAK before the next one looks keeps that order (the reference camera comes first).
        claimed.wait(timeout=30)
    return threads
