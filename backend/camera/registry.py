"""All camera slots, the reference camera, and the persisted runtime camera settings."""

import json
import os
import threading

from ..core import config as C
from ..core.config import CAM_PARAMS
from ..core.events import broadcast
from ..core.rotation import ROTATIONS
from ..core.zoom import ZOOM_MAX, ZOOM_MIN, clamp_center, clamp_zoom
from ..detect.background import get_background
from .slot import CameraSlot, fmt_size, parse_size

class Registry:
    def __init__(self, cameras, reference_role):
        self._order = [role for role, _ in cameras]
        self._slots = {role: CameraSlot(role, device, role == reference_role) for role, device in cameras}
        self.reference_role = reference_role
        self._settings_lock = threading.Lock()
        self.load_settings()

    # ---- access ---------------------------------------------------------------

    @property
    def trigger(self):
        return self._slots[self.reference_role]

    def slots(self):
        """Reference camera first, then the others in configured order."""
        return [self.trigger] + [self._slots[r] for r in self._order if r != self.reference_role]

    def get(self, role):
        return self._slots.get(role)

    def roles(self):
        return [s.role for s in self.slots()]

    def _targets(self, role):
        if role in (None, "", "all"):
            return list(self._slots.values())
        slot = self._slots.get(role)
        return [slot] if slot else []

    # ---- runtime settings -----------------------------------------------------------

    def set_reference(self, role):
        """Make `role` the reference (detection) camera. Returns (roles to rebuild, error)."""
        if role not in self._slots:
            return [], "unknown camera"
        if role == self.reference_role:
            return [], None
        old = self.trigger
        old.is_trigger = False
        self.reference_role = role
        new = self.trigger
        new.is_trigger = True
        new.zoom, new.zoom_cx, new.zoom_cy = 1.0, 0.5, 0.5   # detection works on the whole view
        new.clear_frames()
        for slot in (old, new):   # capture rate and the detection stream depend on the role
            slot.request_restart()
        self.save_settings()
        broadcast({"key": "reference", "role": role})
        return [old.role, new.role], None

    def configure(self, role, preview=None, capture=None):
        """Change stream / capture resolution of one camera or "all". Returns (roles to rebuild, error)."""
        slots = self._targets(role)
        if not slots:
            return [], "unknown camera"
        if preview is not None and preview not in C.PREVIEW_CHOICES:
            return [], "invalid preview resolution"
        if capture is not None and capture not in C.CAPTURE_CHOICES:
            return [], "invalid capture resolution"
        changed = []
        for slot in slots:
            dirty = False
            if preview is not None and parse_size(preview) != slot.preview_size:
                slot.preview_size, dirty = parse_size(preview), True
            if capture is not None and parse_size(capture) != slot.capture_size:
                slot.capture_size, dirty = parse_size(capture), True
            if dirty:
                slot.request_restart()
                changed.append(slot.role)
        if changed:
            self.save_settings()
            broadcast({"key": "camera_config", "roles": changed})
        return changed, None

    def set_stream(self, role, on):
        slots = self._targets(role)
        if not slots:
            return "unknown camera"
        for slot in slots:
            slot.stream_on = bool(on)
        self.save_settings()
        broadcast({"key": "stream", "role": role or "all", "on": bool(on)})
        return None

    def set_rotation(self, role, rotation):
        """Rotate one camera's view (or "all") to 0/90/180/270 degrees clockwise. Applies at once,
        no restart; the camera's background reference turns with it. Returns an error or None."""
        slots = self._targets(role)
        if not slots:
            return "unknown camera"
        try:
            rotation = int(rotation) % 360
        except (TypeError, ValueError):
            return "invalid rotation"
        if rotation not in ROTATIONS:
            return "invalid rotation"
        for slot in slots:
            delta = (rotation - slot.rotation) % 360
            if delta:
                get_background(slot.role).rotate(delta)
                slot.rotation = rotation
        self.save_settings()
        broadcast({"key": "rotation", "role": role or "all", "value": rotation})
        return None

    def set_zoom(self, role, zoom, x=None, y=None):
        """Zoom one camera's view (or every camera but the reference one for "all"): factor 1..4, center
        x/y of the zoomed part in the rotated view (0..1; missing = keep). Applies at once, no restart.
        The reference camera cannot be zoomed: its detections are in whole-view coordinates.
        Returns an error or None."""
        slots = self._targets(role)
        if not slots:
            return "unknown camera"
        try:
            zoom = clamp_zoom(zoom)
            x = None if x is None else clamp_center(x)
            y = None if y is None else clamp_center(y)
        except (TypeError, ValueError):
            return "invalid zoom"
        if role in (None, "", "all"):
            slots = [s for s in slots if not s.is_trigger]
        elif slots[0].is_trigger and zoom > ZOOM_MIN:
            return "the reference camera cannot be zoomed (it detects the shoe in the whole view)"
        for slot in slots:
            cx = slot.zoom_cx if x is None else x
            cy = slot.zoom_cy if y is None else y
            if (zoom, cx, cy) != (slot.zoom, slot.zoom_cx, slot.zoom_cy):
                slot.zoom, slot.zoom_cx, slot.zoom_cy = zoom, cx, cy
                slot.clear_frames()   # the frames in the ring were cropped differently
        self.save_settings()
        broadcast({"key": "zoom", "role": role or "all"})
        return None

    # ---- persistence (survives restarts; overrides the .env defaults) --------------------

    def save_settings(self):
        data = {
            "reference": self.reference_role,
            "cameras": {r: {"preview": fmt_size(s.preview_size), "capture": fmt_size(s.capture_size),
                            "stream": s.stream_on, "rotation": s.rotation,
                            "zoom": {"factor": s.zoom, "x": s.zoom_cx, "y": s.zoom_cy},
                            "params": s.snapshot_params()}
                        for r, s in self._slots.items()},
        }
        with self._settings_lock:
            try:
                tmp = C.SETTINGS_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, C.SETTINGS_PATH)
            except OSError as e:
                print(f"[settings] could not save {C.SETTINGS_PATH}: {e}")

    def load_settings(self):
        try:
            data = json.loads(C.SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        ref = data.get("reference")
        if ref in self._slots and ref != self.reference_role:
            self.trigger.is_trigger = False
            self.reference_role = ref
            self.trigger.is_trigger = True
        for role, cfg in (data.get("cameras") or {}).items():
            slot, cfg = self._slots.get(role), cfg if isinstance(cfg, dict) else {}
            if slot is None:
                continue
            if cfg.get("preview") in C.PREVIEW_CHOICES:
                slot.preview_size = parse_size(cfg["preview"])
            if cfg.get("capture") in C.CAPTURE_CHOICES:
                slot.capture_size = parse_size(cfg["capture"])
            if isinstance(cfg.get("stream"), bool):
                slot.stream_on = cfg["stream"]
            if cfg.get("rotation") in ROTATIONS:
                slot.rotation = cfg["rotation"]
            zoom = cfg.get("zoom")
            if isinstance(zoom, dict) and not slot.is_trigger:
                try:
                    slot.zoom = clamp_zoom(zoom.get("factor", 1.0))
                    slot.zoom_cx = clamp_center(zoom.get("x", 0.5))
                    slot.zoom_cy = clamp_center(zoom.get("y", 0.5))
                except (TypeError, ValueError):
                    pass
            saved = cfg.get("params")
            for name, value in (saved.items() if isinstance(saved, dict) else ()):
                spec = CAM_PARAMS.get(name)
                if spec is None:
                    continue
                if spec["type"] == "int" and isinstance(value, int) and not isinstance(value, bool):
                    slot.params[name] = max(spec["min"], min(spec["max"], value))
                elif spec["type"] == "enum" and value in spec["values"]:
                    slot.params[name] = value


registry = Registry(C.CAMERAS, C.TRIGGER_CAMERA)
