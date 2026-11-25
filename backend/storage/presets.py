"""Product presets (2026-11-25): the camera settings of every camera and the brightness of the lights, saved under a
name (one per product, e.g. ON1GR / ON1WH) and applied in one go on the Live page (Controls > Preset) when the
production changes over (about every 10 days).

    <captures>/.presets.json   {"active": {"name", "applied_at", "modified"} | null,
                                "presets": {name: {"saved_at", "cameras": {role: params}, "lights": {ch: value} | null}}}

What a preset holds: the CAM_PARAMS of each camera (exposure, ISO, AE limit, WB, focus, sharpness, denoise, AE comp,
brightness, contrast, saturation, flicker). Not the resolutions, rotation, zoom, stream or the reference camera: those
are the set-up of the station, not of the product. The lights: the value of each channel of ON_LIGHT_CHANNELS.
"active" is the preset applied last; "modified" turns true when a camera value or a light is changed by hand after it
(the sets saved then are not exactly the preset). Every saved set records it (meta "preset").

The lights (2026-11-25): the app does not ask the controller for every set (a TCP request per set); it keeps the
values it last read or set - on the Light tab / page, a preset applied or saved, and once at the start of the app - in
"lights_now" {"values": {name: value}, "read_at"} of the same file, and every saved set records that (meta "lights").
A change made on the controller itself (not through the app) is not seen until the app reads the lights again."""

import json
import os
import re
import threading
import time

from ..core import config as C

PATH = C.CAPTURES_DIR / ".presets.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()+-]{0,39}$")
_lock = threading.Lock()


def _read():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("presets"), dict):
        data["presets"] = {}
    if not isinstance(data.get("active"), dict):
        data["active"] = None
    if not isinstance(data.get("lights_now"), dict):
        data["lights_now"] = None
    return data


def _write(data):
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def clean_params(params):
    """Only known CAM_PARAMS, values clamped to their range (a preset file edited by hand stays safe)."""
    out = {}
    for name, value in (params or {}).items():
        spec = C.CAM_PARAMS.get(name)
        if spec is None:
            continue
        if spec["type"] == "int" and isinstance(value, (int, float)) and not isinstance(value, bool):
            v = max(spec["min"], min(spec["max"], int(value)))
            if name == "iso" and v != 0:
                v = max(100, v)
            out[name] = v
        elif spec["type"] == "enum" and value in spec["values"]:
            out[name] = value
    return out


def load():
    """{"active", "presets": [{"name", "saved_at", "cameras", "lights"}] sorted by name, "lights_now"}"""
    with _lock:
        data = _read()
    items = [dict(p, name=n) for n, p in data["presets"].items() if isinstance(p, dict)]
    return {"active": data["active"], "presets": sorted(items, key=lambda p: p["name"].lower()),
            "lights_now": data["lights_now"]}


def get(name):
    with _lock:
        p = _read()["presets"].get(name)
    return dict(p, name=name) if isinstance(p, dict) else None


def save(name, cameras, lights):
    """Save (or overwrite) a preset. cameras: {role: params}; lights: {ch: value} or None. Returns an error or None."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        return "name: 1-40 letters, digits, space or _ . ( ) + - (e.g. ON1WH)"
    preset = {"saved_at": _now(), "cameras": {r: clean_params(p) for r, p in cameras.items()},
              "lights": {str(int(ch)): max(0, min(255, int(v))) for ch, v in lights.items()} if lights else None}
    with _lock:
        data = _read()
        data["presets"][name] = preset
        a = data["active"]
        if a and a.get("name") == name:   # saved from what runs now: the station is exactly this preset again
            a["modified"] = False
        _write(data)
    return None


def delete(name):
    with _lock:
        data = _read()
        if name not in data["presets"]:
            return "not found"
        del data["presets"][name]
        if data["active"] and data["active"].get("name") == name:
            data["active"] = None
        _write(data)
    return None


def set_active(name):
    with _lock:
        data = _read()
        data["active"] = {"name": name, "applied_at": _now(), "modified": False}
        _write(data)


def mark_modified():
    """A camera value or a light was changed by hand: the station no longer runs the active preset exactly."""
    with _lock:
        data = _read()
        a = data["active"]
        if not a or a.get("modified"):
            return
        a["modified"] = True
        _write(data)


def note_lights(values):
    """The lights as the app just read or set them: values = the controller's 16 values (list) or {ch: value}.
    Kept as {name: value} of ON_LIGHT_CHANNELS; written only when they changed (or the last read is old)."""
    if values is None:
        return
    get = (lambda ch: values[ch - 1]) if isinstance(values, (list, tuple)) else (lambda ch: values.get(ch, values.get(str(ch))))
    named = {}
    for name, ch in C.LIGHT_CHANNELS:
        v = get(ch)
        if v is not None:
            named[name] = int(v)
    if not named:
        return
    with _lock:
        data = _read()
        old = data.get("lights_now") or {}
        data["lights_now"] = {"values": named, "read_at": _now()}
        if old.get("values") == named and (old.get("read_at") or "")[:13] == data["lights_now"]["read_at"][:13]:
            return   # the same values, already noted this hour: no write
        _write(data)


_meta_cache = {"stamp": None, "preset": None, "lights": None}


def _meta():
    """(preset, lights) for the sets, read again only when the file changed (called for every set)."""
    try:
        st = PATH.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None, None
    if stamp != _meta_cache["stamp"]:
        with _lock:
            data = _read()
        a, lights = data["active"], data["lights_now"]
        _meta_cache.update(stamp=stamp, preset={"name": a["name"], "modified": bool(a.get("modified"))} if a else None,
                           lights=dict(lights) if lights and lights.get("values") else None)
    return _meta_cache["preset"], _meta_cache["lights"]


def for_meta():
    """What a saved set records as "preset": {"name", "modified"} of the active preset, or None."""
    p = _meta()[0]
    return dict(p) if p else None


def lights_for_meta():
    """What a saved set records as "lights": {"values": {name: 0..255}, "read_at"} as the app last knew them, or None."""
    lights = _meta()[1]
    return {"values": dict(lights["values"]), "read_at": lights.get("read_at")} if lights else None
