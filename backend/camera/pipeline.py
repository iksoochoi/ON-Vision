"""Builds the depthai pipeline of one camera: streams, and the YOLO network on the reference camera."""

import depthai as dai

from ..core import config as C
from ..core.state import state, state_lock
from ..detect.background import get_background


def _load_archive(pipeline):
    if C.MODEL_ARCHIVE:
        print(f"Model: custom archive {C.MODEL_ARCHIVE}")
        return dai.NNArchive(C.MODEL_ARCHIVE)
    print(f"Model: zoo '{C.MODEL_ZOO_NAME}' (downloaded on first run)")
    desc = dai.NNModelDescription(C.MODEL_ZOO_NAME)
    desc.platform = pipeline.getDefaultDevice().getPlatformAsString()
    return dai.NNArchive(dai.getModelFromZoo(desc))


def _nn_input_type(archive):
    """The pixel format a CUSTOM model's NN archive says it wants (e.g. RGB888p for a model trained on RGB
    pictures), so the camera feeds the right channel order. BGR888p, as before, for the zoo model and when the
    archive does not say."""
    if not C.MODEL_ARCHIVE:
        return dai.ImgFrame.Type.BGR888p
    try:
        name = str(archive.getConfig().model.inputs[0].preprocessing.daiType)
        kind = getattr(dai.ImgFrame.Type, name)
    except Exception:
        print("Model: the archive does not name its input format: feeding BGR888p")
        return dai.ImgFrame.Type.BGR888p
    print(f"Model: the archive wants {name} input")
    return kind


def build_pipeline(pipeline, slot, slow_usb=False):
    """Create the nodes and output queues for one camera. Returns the queues in a dict."""
    cam = pipeline.create(dai.node.Camera).build()
    cap_fps = C.FULL_FPS if slot.is_trigger else C.SIDE_FPS
    pv_fps = C.FULL_FPS if slot.is_trigger else C.SIDE_PREVIEW_FPS
    if slow_usb:   # a USB2 link carries ~30 MB/s: keep the cameras alive with lower rates
        cap_fps, pv_fps = min(cap_fps, 3), min(pv_fps, 5)
    if slot.capture_size:
        full = cam.requestOutput(size=slot.capture_size, type=dai.ImgFrame.Type.NV12, fps=cap_fps)
    else:
        full = cam.requestFullResolutionOutput(
            type=dai.ImgFrame.Type.NV12, fps=cap_fps, useHighestResolution=True,
        )
    preview = cam.requestOutput(size=slot.preview_size, type=dai.ImgFrame.Type.NV12, fps=pv_fps)
    cw, ch = slot.capture_size or (4056, 3040)
    pw, ph = slot.preview_size
    load = (cw * ch * cap_fps + pw * ph * pv_fps) * 1.5 / 1e6
    print(f"[{slot.role}] streams: capture {cw}x{ch}@{cap_fps:g}fps + preview {pw}x{ph}@{pv_fps:g}fps"
          f" = ~{load:.0f} MB/s (USB3 carries ~300, USB2 ~30)")

    queues = {"classes": [], "det": None}
    if slot.is_trigger:   # only the reference camera detects; switching it rebuilds both cameras
        if C.DETECTOR == "model":
            archive = _load_archive(pipeline)
            nn_w, nn_h = archive.getInputSize()
            # STRETCH keeps the whole field of view, so bbox coordinates map 1:1 onto the frame.
            nn_input = cam.requestOutput(
                size=(nn_w, nn_h), type=_nn_input_type(archive),
                resizeMode=dai.ImgResizeMode.STRETCH, fps=C.NN_FPS,
            )
            nn = pipeline.create(dai.node.DetectionNetwork).build(nn_input, archive)
            nn.setConfidenceThreshold(C.DETECT_CONF)
            try:
                queues["classes"] = list(nn.getClasses() or [])
            except Exception:
                pass
            queues["det"] = nn.out.createOutputQueue(maxSize=4, blocking=False)
            print(f"Trigger classes: {'any' if C.ALL_CLASSES else C.TARGET_CLASSES}, bbox >= "
                  f"{C.MODEL_MIN_AREA:.0%} of the frame (model has {len(queues['classes'])} classes)")
        else:
            # background subtraction runs on the preview frames: no extra stream over USB
            print(f"[{slot.role}] Detector: background subtraction on the preview"
                  + ("" if get_background(slot.role).exists
                     else " (no background yet - use the 'background' button)"))
        with state_lock:
            state["detector"] = {
                "mode": C.DETECTOR,
                "model": (C.MODEL_ARCHIVE or C.MODEL_ZOO_NAME) if C.DETECTOR == "model" else "",
                "classes": queues["classes"],
                "targets": C.TARGET_CLASSES if C.DETECTOR == "model" else ["shoe"],
            }

    queues["full"] = full.createOutputQueue(maxSize=2, blocking=False)
    queues["preview"] = preview.createOutputQueue(maxSize=2, blocking=False)
    queues["control"] = cam.inputControl.createInputQueue()
    return queues
