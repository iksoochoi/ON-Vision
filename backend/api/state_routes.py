"""Routes for the live state and the camera controls."""

from ..camera.registry import registry
from ..capture import disk_guard
from ..core import config as C
from ..core.config import CAM_PARAMS, TARGET_CLASSES, UI
from ..core.events import broadcast
from ..core.rotation import unrotate_point
from ..core.state import state, state_lock
from ..detect.background import get_background
from ..qr.hub import hub as qr_hub
from ..storage.store import store


class StateRoutes:
    def _state_payload(self, light=False):
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
        payload["counts"] = None if light else store.counts()
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

    def _set_auto(self, data):
        enabled = bool(data.get("enabled"))
        if enabled:
            why = disk_guard.blocked_message(disk_guard.status(fresh=True))
            if why:
                self.send_error(409, why)
                return
        with state_lock:
            state["auto_enabled"] = enabled
        broadcast({"key": "auto_enabled", "value": enabled})
        self._send_json({"auto_enabled": enabled})

    def _set_focus(self, data):
        slot = registry.get(data.get("role") or registry.reference_role)
        try:
            x, y = float(data["x"]), float(data["y"])
        except (KeyError, TypeError, ValueError):
            self.send_error(400)
            return
        if slot is None:
            self.send_error(400, "unknown camera")
            return
        # the click is on the rotated view; the focus region is in sensor coordinates
        x, y = unrotate_point(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0), slot.rotation)
        with slot.lock:
            slot.focus_request = (x, y)
        self._send_json({"ok": True})

    def _set_cam(self, data):
        """Body: {name, value, role?}. role = a camera role, or "all" / missing for every camera."""
        role = data.get("role") or "all"
        slots = registry.slots() if role == "all" else [registry.get(role)]
        if None in slots:
            self.send_error(400, "unknown camera")
            return
        name = data.get("name")
        spec = CAM_PARAMS.get(name)
        if spec is None:
            self.send_error(400, "unknown parameter")
            return
        raw = data.get("value")
        if spec["type"] == "int":
            try:
                v = int(float(raw))
            except (TypeError, ValueError):
                self.send_error(400)
                return
            v = max(spec["min"], min(spec["max"], v))
            if name == "iso" and v != 0:
                v = max(100, v)
        else:
            if raw not in spec["values"]:
                self.send_error(400)
                return
            v = raw
        for slot in slots:
            slot.set_param(name, v)
        registry.save_settings()   # remembered across restarts
        broadcast({"key": "cam", "role": role, "name": name, "value": v})
        self._send_json({"role": role, "name": name, "value": v})
