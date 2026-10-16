"""The live state as the Live page wants it: the whole of it once (GET /api/state), and its changing part pushed
over SSE (the "state" event, see feeds.py)."""

from ..camera.registry import registry
from ..capture import disk_guard
from ..core import config as C
from ..core.config import CAM_PARAMS, TARGET_CLASSES, UI
from ..core.state import state, state_lock
from ..detect.background import get_background
from ..qr.hub import hub as qr_hub
from ..storage.store import store

# what the page already has after the first full state: the settings that do not change while it is open
STATIC_KEYS = ("cam_spec", "choices", "target_classes", "ui")


def build_state():
    """Everything the Live page needs. The counts come from the index in memory: no file is read."""
    with state_lock:
        payload = {
            "auto_enabled": state["auto_enabled"],
            "auto_count": state["auto_count"],
            "detector": dict(state["detector"]),
        }
    payload["cams"] = [slot.describe() for slot in registry.slots()]  # reference camera first
    payload["trigger_camera"] = registry.reference_role   # "reference camera" in the UI
    payload["cam_spec"] = CAM_PARAMS
    payload["choices"] = {"preview": C.PREVIEW_CHOICES, "capture": C.CAPTURE_CHOICES}
    payload["target_classes"] = TARGET_CLASSES
    payload["shots"] = {"count": C.SHOTS, "spread": C.SHOT_SPREAD, "mode": C.SHOT_MODE}
    payload["ui"] = UI
    payload["counts"] = store.counts()
    backgrounds = {}
    for slot in registry.slots():
        bg = get_background(slot.role)
        backgrounds[slot.role] = {"exists": bg.exists, "updated": bg.updated}
    payload["backgrounds"] = backgrounds
    payload["background"] = backgrounds[registry.reference_role]   # the reference camera's
    payload["qr"] = {"enabled": C.QR_ENABLED, "latest": qr_hub.snapshot(1)["latest"]}
    with state_lock:
        stopped = state["disk_stop"]
    payload["disk"] = {**disk_guard.status(), "stopped": stopped}
    return payload


def build_changing_state():
    """The same without the settings that never change while a page is open: what is pushed every few seconds."""
    payload = build_state()
    for key in STATIC_KEYS:
        payload.pop(key, None)
    return payload
