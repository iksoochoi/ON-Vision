"""Light page: read and set the brightness of the lights (backend/light/lpw.py)."""

from ..core import config as C
from ..light import lpw


def _payload(values):
    return {"enabled": True, "host": C.LIGHT_HOST, "port": C.LIGHT_PORT,
            "channels": [{"ch": ch, "name": name, "value": values[ch - 1]} for name, ch in C.LIGHT_CHANNELS]}


class LightRoutes:
    def _get_light(self):
        """GET /api/light -> {"enabled", "host", "port", "channels": [{"ch", "name", "value"}]}"""
        if not C.LIGHT_HOST:
            self._send_json({"enabled": False, "channels": []})
            return
        try:
            values = lpw.read_values()
        except lpw.LightError as e:
            self.send_error(502, str(e))
            return
        self._send_json(_payload(values))

    def _set_light(self, data):
        """POST /api/light {"ch": [1, 2] or 1, "value": 0..255}: sets them, then answers what the unit reads back."""
        chs = data.get("ch")
        chs = chs if isinstance(chs, list) else [chs]
        value = data.get("value")
        known = {ch: name for name, ch in C.LIGHT_CHANNELS}
        if not chs or not all(isinstance(c, int) and c in known for c in chs):
            self.send_error(400, f"ch: one or more of the channels {sorted(known)} (ON_LIGHT_CHANNELS)")
            return
        if not isinstance(value, int) or not 0 <= value <= 255:
            self.send_error(400, "value: 0..255")
            return
        try:
            lpw.set_value(chs, value)
            values = lpw.read_values()
        except lpw.LightError as e:
            print(f"[light] setting {', '.join(known[c] for c in chs)} to {value} failed: {e}")
            self.send_error(502, str(e))
            return
        print(f"[light] {', '.join(f'{known[c]} (CH{c})' for c in chs)} -> {value}  (from {self.client_address[0]})")
        self._send_json(_payload(values))
