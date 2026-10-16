"""Routes for the live state and the camera controls."""

from ..camera.registry import registry
from ..capture import disk_guard
from ..core.config import CAM_PARAMS
from ..core.events import broadcast
from ..core.rotation import unrotate_point
from ..core.state import state, state_lock
from .state_payload import build_state


class StateRoutes:
    def _state_payload(self, light=False):
        """The whole state (see state_payload.py). `light` is kept for old callers: the counts come from the index
        in memory now, so there is nothing to leave out."""
        return build_state()

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
        # the click is on the zoomed, rotated view; the focus region is in sensor coordinates
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        crop = slot.view_crop()
        if crop is not None:
            x, y = crop[0] + x * (crop[2] - crop[0]), crop[1] + y * (crop[3] - crop[1])
        x, y = unrotate_point(x, y, slot.rotation)
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
