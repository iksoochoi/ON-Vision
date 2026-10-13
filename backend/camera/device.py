"""Finding and claiming OAK devices on USB: which device belongs to which camera slot."""

import threading
import time

import depthai as dai

from .registry import registry

_claim_lock = threading.Lock()
_claimed = {}                   # role -> device id currently opened by this process


class DeviceNotFound(RuntimeError):
    """No free OAK for this camera (yet)."""


NOT_FOUND_RETRY_SEC = 15     # a camera that never had an OAK: look again rarely (USB scans can stall running cameras)
KNOWN_DEVICE_RETRY_SEC = 3   # a camera whose OAK is known (restart, brief unplug): it is probably back in a moment
KNOWN_DEVICE_TRIES = 4       # ... for this many tries, then fall back to the slow rhythm
DISCOVERY_TTL_SEC = 3.0      # threads share one USB scan for this long
SLOW_USB = ("LOW", "FULL", "HIGH")   # USB1 / USB2 links (SUPER = USB3)

_discover_lock = threading.Lock()
_discovered = {"t": -1e9, "infos": []}


def _device_id(info):
    try:
        return str(info.getDeviceId())
    except AttributeError:
        return str(info.deviceId)


def _discover():
    """OAKs visible on USB. Scanning is slow and can disturb cameras that are streaming, so every
    camera thread shares one result for a few seconds."""
    with _discover_lock:
        if time.monotonic() - _discovered["t"] > DISCOVERY_TTL_SEC:
            _discovered["infos"] = list(dai.Device.getAllAvailableDevices())
            _discovered["t"] = time.monotonic()
        return list(_discovered["infos"])


def claim_device(slot):
    """Pick the OAK for this slot.

    A configured id (or a unique suffix of it) must match. Without one (ON_CAMERAS=auto) the slot
    keeps the OAK it used before, so restarts never swap the physical positions; only a slot that
    has never had a device takes any free OAK that no other slot has used."""
    with _claim_lock:
        free = [(info, _device_id(info)) for info in _discover()
                if _device_id(info) not in _claimed.values()]
        fixed = (slot.device_id or "").lower()
        if fixed:
            for info, did in free:
                if did.lower() == fixed or did.lower().endswith(fixed):
                    _claimed[slot.role] = did
                    return info, did
        else:
            mine = (slot.actual_id or "").lower()
            others = {(s.actual_id or "").lower() for s in registry.slots() if s is not slot}
            for info, did in free:
                if did.lower() == mine or (not mine and did.lower() not in others):
                    _claimed[slot.role] = did
                    return info, did
    seen = ", ".join(did for _, did in free) or "none"
    wanted = slot.device_id or (f"{slot.actual_id} (used before)" if slot.actual_id else "(any)")
    raise DeviceNotFound(f"device {wanted} not found; free OAKs: {seen}")


def usb_speed(device):
    """'SUPER' (USB3), 'HIGH' (USB2), ... or None when the API does not tell."""
    try:
        speed = device.getUsbSpeed()
        return getattr(speed, "name", str(speed))
    except Exception:
        return None


def print_assignment():
    """Which OAK took which role so far, and the .env line that pins it (matters when roles were
    not given a device id: the assignment then follows the USB enumeration order)."""
    pairs = [(s.role, s.actual_id) for s in registry.slots() if s.online and s.actual_id]
    print("Assignment: " + ", ".join(f"{r}={i}" for r, i in pairs))
    if any(s.device_id is None for s in registry.slots()):
        print("  keep this mapping: ON_CAMERAS=" + ",".join(f"{r}:{i}" for r, i in pairs))


def release_device(role):
    """The camera of `role` let go of its OAK: free it for the other slots and force a fresh USB scan."""
    with _claim_lock:
        _claimed.pop(role, None)
    _discovered["t"] = -1e9   # the OAK that was just released must show up in the very next scan
