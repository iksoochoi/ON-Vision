"""Camera controls sent to a running OAK: ISP parameters, exposure, lens (manual focus) and click-to-focus."""

import depthai as dai

from ..core.config import CAM_PARAMS, DEFAULT_EXPOSURE_US, DEFAULT_ISO

ISP_PARAMS = [k for k in CAM_PARAMS if k not in ("iso", "exposure_ms", "ae_limit_ms", "lens_position")]
AE_DEFAULT_LIMIT_US = 33000   # what the camera's auto exposure tops out at when no limit is set
FOCUS_SETTLE_SEC = 1.5        # after a click-focus: the lens needs this long to settle before it is read


def isp_setters():
    ctl = dai.CameraControl
    return {
        "wb": lambda c, v: (c.setAutoWhiteBalanceMode(ctl.AutoWhiteBalanceMode.AUTO)
                            if v == 0 else c.setManualWhiteBalance(v)),
        "sharpness": lambda c, v: c.setSharpness(v),
        "luma_denoise": lambda c, v: c.setLumaDenoise(v),
        "chroma_denoise": lambda c, v: c.setChromaDenoise(v),
        "ae_comp": lambda c, v: c.setAutoExposureCompensation(v),
        "brightness": lambda c, v: c.setBrightness(v),
        "contrast": lambda c, v: c.setContrast(v),
        "hw_saturation": lambda c, v: c.setSaturation(v),
        "antibanding": lambda c, v: c.setAntiBandingMode(getattr(ctl.AntiBandingMode, v)),
    }


def apply_isp(control_queue, setters, cam_snap, last_sent):
    """Send only the ISP params that changed since the last call."""
    ctrl = dai.CameraControl()
    changed = False
    for name in ISP_PARAMS:
        if last_sent.get(name) != cam_snap[name]:
            setters[name](ctrl, cam_snap[name])
            last_sent[name] = cam_snap[name]
            changed = True
    if changed:
        control_queue.send(ctrl)


def apply_exposure(control_queue, iso, exp_ms, ae_limit_ms, last_sent, tag=""):
    """Send manual exposure / auto-exposure command when (iso, exp, auto limit) change."""
    if (iso, exp_ms, ae_limit_ms) == last_sent:
        return last_sent
    ctrl = dai.CameraControl()
    if iso == 0 and exp_ms == 0:
        ctrl.setAutoExposureEnable()
        ctrl.setAutoExposureLimit(ae_limit_ms * 1000 if ae_limit_ms else AE_DEFAULT_LIMIT_US)
        print(f"[{tag}] Exposure -> auto" + (f", at most {ae_limit_ms}ms" if ae_limit_ms else ""))
    else:
        actual_iso = iso if iso > 0 else DEFAULT_ISO
        actual_exp_us = exp_ms * 1000 if exp_ms > 0 else DEFAULT_EXPOSURE_US
        ctrl.setManualExposure(actual_exp_us, actual_iso)
        print(f"[{tag}] Exposure -> ISO {actual_iso}, {actual_exp_us}us")
    control_queue.send(ctrl)
    return (iso, exp_ms, ae_limit_ms)


def apply_lens(control_queue, lens, last_sent, tag=""):
    """lens 0 = continuous autofocus (the camera default), 1..255 = focus locked at that lens position."""
    if lens == last_sent:
        return last_sent
    ctrl = dai.CameraControl()
    if lens > 0:
        ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
        ctrl.setManualFocus(lens)
        print(f"[{tag}] Focus -> locked at lens position {lens}")
    else:
        ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.CONTINUOUS_VIDEO)
        print(f"[{tag}] Focus -> continuous autofocus")
    control_queue.send(ctrl)
    return lens


def lens_position(frame):
    """Lens position (0..255) the frame was taken at, or None when the API does not tell."""
    try:
        return int(frame.getLensPosition())
    except (AttributeError, TypeError, ValueError):
        return None


def apply_focus(control_queue, focus, full, tag=""):
    """One-shot autofocus on a region centered at the clicked point (normalized coords)."""
    sw, sh = full.getWidth(), full.getHeight()
    cx, cy = int(focus[0] * sw), int(focus[1] * sh)
    region = max(sw, sh) // 10
    rx = max(0, min(cx - region // 2, sw - region))
    ry = max(0, min(cy - region // 2, sh - region))
    ctrl = dai.CameraControl()
    ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.AUTO)
    ctrl.setAutoFocusRegion(rx, ry, region, region)
    ctrl.setAutoFocusTrigger()
    control_queue.send(ctrl)
    print(f"[{tag}] Focus -> sensor ({cx}, {cy}), region {region}x{region}")
