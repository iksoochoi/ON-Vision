"""Product presets (backend/storage/presets.py): the camera settings of every camera + the lights under a name.

    GET  /api/presets                 {"active", "presets": [...], "lights_enabled"}
    POST /api/presets/save            {"name", "lights": true|false}   what runs now (every camera + the lights)
    POST /api/presets/apply           {"name"}                         to every camera in it + the lights in it
    POST /api/presets/delete          {"name"}"""

from ..camera.registry import registry
from ..core import config as C
from ..core.events import broadcast
from ..light import lpw
from ..storage import presets


def _lights_now():
    """{ch: value} of ON_LIGHT_CHANNELS as the controller reads them, or (None, reason)."""
    if not C.LIGHT_HOST:
        return None, "no lighting controller (ON_LIGHT_HOST is empty)"
    try:
        values = lpw.read_values()
    except lpw.LightError as e:
        return None, str(e)
    presets.note_lights(values)
    return {ch: values[ch - 1] for _, ch in C.LIGHT_CHANNELS}, None


class PresetRoutes:
    def _get_presets(self):
        data = presets.load()
        data["lights_enabled"] = bool(C.LIGHT_HOST)
        data["roles"] = [s.role for s in registry.slots()]
        data["light_names"] = {str(ch): name for name, ch in C.LIGHT_CHANNELS}
        self._send_json(data)

    def _save_preset(self, data):
        name = (data.get("name") or "").strip()
        cameras = {s.role: s.snapshot_params() for s in registry.slots()}
        lights = None
        if data.get("lights", True) and C.LIGHT_HOST:
            lights, err = _lights_now()
            if lights is None:
                self.send_error(502, f"the lights could not be read ({err}): save without the lights, or try again")
                return
        err = presets.save(name, cameras, lights)
        if err:
            self.send_error(400, err)
            return
        print(f"[preset] saved '{name}': {len(cameras)} cameras" + (f", lights {lights}" if lights else ", no lights")
              + f"  (from {self.client_address[0]})")
        broadcast({"key": "preset"})
        self._send_json(dict(presets.load(), saved=name))

    def _apply_preset(self, data):
        """The camera values at once (each camera's worker sends them on its next frame; the settings guard checks
        that the pictures follow), then the lights, saved in the controller (kept after a power off)."""
        name = (data.get("name") or "").strip()
        p = presets.get(name)
        if p is None:
            self.send_error(404, "no such preset")
            return
        applied, skipped = [], []
        for role, params in (p.get("cameras") or {}).items():
            slot = registry.get(role)
            if slot is None:
                skipped.append(role)   # a camera of the preset that this station does not have (any more)
                continue
            for k, v in presets.clean_params(params).items():
                slot.set_param(k, v)
            applied.append(role)
        registry.save_settings()   # remembered across restarts, like a change by hand
        light_error, lights_set = None, {}
        if p.get("lights"):
            if not C.LIGHT_HOST:
                light_error = "no lighting controller (ON_LIGHT_HOST is empty)"
            else:
                known = {ch for _, ch in C.LIGHT_CHANNELS}
                try:
                    by_value = {}
                    for ch, v in p["lights"].items():
                        if int(ch) in known:
                            by_value.setdefault(int(v), []).append(int(ch))
                    for v, chs in by_value.items():
                        lpw.set_value(chs, v)
                        lights_set.update({ch: v for ch in chs})
                    lpw.save()
                    presets.note_lights(lpw.read_values())   # as the controller has them now
                except (lpw.LightError, ValueError) as e:
                    light_error = str(e)
                    if lights_set:
                        presets.note_lights(lights_set)
        presets.set_active(name)
        if light_error:
            presets.mark_modified()   # the cameras are the preset's, the lights may not be
        print(f"[preset] applied '{name}': cameras {', '.join(applied) or '-'}"
              + (f" (not here: {', '.join(skipped)})" if skipped else "")
              + (f", lights {lights_set}" if lights_set else "")
              + (f", LIGHTS FAILED: {light_error}" if light_error else "") + f"  (from {self.client_address[0]})")
        broadcast({"key": "preset", "applied": name})
        self._send_json(dict(presets.load(), applied=name, cameras=applied, skipped=skipped,
                             lights=lights_set, light_error=light_error))

    def _delete_preset(self, data):
        err = presets.delete((data.get("name") or "").strip())
        if err:
            self.send_error(404 if err == "not found" else 400, err)
            return
        broadcast({"key": "preset"})
        self._send_json(presets.load())
