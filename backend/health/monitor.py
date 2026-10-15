"""The monitor: looks at the cameras every few seconds and reports what it sees. It only watches and reports;
it changes nothing (no restarts), so that a problem stays as it is while it is being studied.

  CAMERA_STALLED   a camera is ONLINE but has sent no new full-size picture for ON_HEALTH_STALL_SEC seconds. The
                   log line and the alert carry the state of every camera at that moment (fps, USB speed, age of
                   the last picture) and say which other cameras were silent at the same time: cameras that
                   stall together usually share a USB hub or controller.
  CAMERA_DOWN      a camera has been offline for ON_HEALTH_OFFLINE_SEC seconds (not found, unplugged, ...).
  CAMERA_RECOVERED the stream is back / the camera is online again (sent only if the problem was sent).
  STATUS           every ON_HEALTH_STATUS_SEC seconds: one line with every camera's fps and picture age, plus the
                   load of the Mac, so the minutes before a stall can be read from the log.

No depthai here: it only reads the camera slots, so it runs and is tested without hardware."""

import os
import threading
import time

from ..core import config as C
from . import alerts, collect


class HealthMonitor(threading.Thread):
    def __init__(self, registry, stop, clock=time.monotonic):
        super().__init__(name="health-monitor", daemon=True)
        self.registry = registry
        self.stop_event = stop
        self.clock = clock
        self.started = clock()
        self._state = {}          # role -> {"offline_since", "down", "stalled", "stalled_since"}
        self._next_status = clock() + C.HEALTH_STATUS_SEC
        alerts.set_status_provider(self.snapshot)

    # ---- readings ----------------------------------------------------------------------------------

    def picture_age(self, slot, now):
        """Seconds since the camera's last full-size picture (or since it came online, if none yet). None when
        it has neither."""
        ref = max(slot.last_full or 0.0, slot.online_since or 0.0)
        return now - ref if ref else None

    def camera_lines(self, now):
        lines = []
        for slot in self.registry.slots():
            d = slot.describe()
            age = self.picture_age(slot, now)
            state = "ONLINE " if d["online"] else "OFFLINE"
            lines.append(f"{d['role']:<6} {state} usb={d['usb'] or '?'} fps full={d['fps']['full']} preview={d['fps']['preview']} "
                         f"last picture {'-' if age is None else f'{age:.1f}s'} ago capture={d['capture']}"
                         + ("" if d["online"] else f" reason={d['error']}"))
        return lines

    def system_line(self, now):
        parts = [f"app up {int(now - self.started)}s", f"threads={threading.active_count()}"]
        if hasattr(os, "getloadavg"):
            try:
                parts.insert(0, "load " + "/".join(f"{x:.1f}" for x in os.getloadavg()))
            except OSError:
                pass
        try:
            from ..capture import disk_guard
            parts.append(f"disk {disk_guard.status()['used_pct']}% used")
        except Exception:
            pass
        return ", ".join(parts)

    def snapshot(self, now=None):
        """Text: the state of every camera and of the machine right now."""
        now = self.clock() if now is None else now
        return "\n".join(self.camera_lines(now) + [self.system_line(now)])

    # ---- one look ----------------------------------------------------------------------------------

    def _st(self, role):
        return self._state.setdefault(role, {"offline_since": None, "down": False, "stalled": False, "stalled_since": None})

    def check(self, now=None):
        """One look at every camera; reports changes. Returns the events' kinds (for tests)."""
        now = self.clock() if now is None else now
        kinds = []
        slots = self.registry.slots()
        ages = {s.role: self.picture_age(s, now) for s in slots if s.online}
        for slot in slots:
            st = self._st(slot.role)
            if not slot.online:
                if st["offline_since"] is None:
                    st["offline_since"] = now
                elif not st["down"] and now - st["offline_since"] >= C.HEALTH_OFFLINE_SEC:
                    st["down"] = True
                    kinds.append("CAMERA_DOWN")
                    collect.emit("CAMERA_DOWN", "ERROR", f"camera has been offline for {int(now - st['offline_since'])} s: "
                                 f"{slot.error or 'unknown'}", camera=slot.role, snapshot=self.snapshot(now),
                                 usb=slot.usb_speed or "?")
                continue
            if st["offline_since"] is not None:
                was_down, since = st["down"], st["offline_since"]
                st["offline_since"], st["down"] = None, False
                if was_down:
                    kinds.append("CAMERA_RECOVERED")
                    collect.emit("CAMERA_RECOVERED", "INFO", f"camera is online again after {int(now - since)} s",
                                 camera=slot.role, recovers=[("CAMERA_DOWN", slot.role)])
            age = ages.get(slot.role)
            if age is None:
                continue
            if not st["stalled"] and age >= C.HEALTH_STALL_SEC:
                st["stalled"], st["stalled_since"] = True, now - age
                silent = sorted(r for r, a in ages.items() if r != slot.role and a is not None and a >= 2.0)
                kinds.append("CAMERA_STALLED")
                collect.emit("CAMERA_STALLED", "ERROR",
                             f"camera is online but sent no picture for {age:.1f} s"
                             + (f"; silent at the same time: {', '.join(silent)}" if silent else "; no other camera is silent"),
                             camera=slot.role, snapshot=self.snapshot(now), usb=slot.usb_speed or "?",
                             fps=f"{slot.fps().get('full')}/{slot.fps().get('preview')}")
            elif st["stalled"] and age < min(1.0, C.HEALTH_STALL_SEC):
                lasted = now - (st["stalled_since"] or now)
                st["stalled"], st["stalled_since"] = False, None
                kinds.append("CAMERA_RECOVERED")
                collect.emit("CAMERA_RECOVERED", "INFO", f"pictures are coming again after about {int(lasted)} s",
                             camera=slot.role, recovers=[("CAMERA_STALLED", slot.role)])
        if C.HEALTH_STATUS_SEC and now >= self._next_status:
            self._next_status = now + C.HEALTH_STATUS_SEC
            cams = " | ".join(
                f"{s.role} {'up' if s.online else 'OFF'} {s.fps().get('full')}fps "
                f"age {('-' if self.picture_age(s, now) is None else format(self.picture_age(s, now), '.1f'))}s"
                for s in slots)
            collect.emit("STATUS", "INFO", cams + " | " + self.system_line(now), alert=False)
        return kinds

    def run(self):
        while not self.stop_event.wait(C.HEALTH_INTERVAL_SEC):
            try:
                self.check()
            except Exception as e:  # the monitor must not die on one odd reading
                print(f"[health] check failed: {type(e).__name__}: {e}")


def start(registry, stop):
    """Start the monitor thread. Returns it."""
    monitor = HealthMonitor(registry, stop)
    monitor.start()
    return monitor
