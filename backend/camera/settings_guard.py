"""Does a camera really follow the exposure it was told to use?

The settings of a camera (manual exposure and ISO, or an automatic exposure with a limit) are sent to the OAK once, when
they change and when the camera starts. Twice already a camera went on with the automatic exposure at 30 ms (the
default) although the settings said 8 ms: after it had dropped out and come back (10-24 12:23: LEFT, 2.5 hours of
blurred pictures, no QR code of the left shoes read), and once after the start of the app. Nothing noticed it, because
the app believes what it sent.

The truth is in every picture: the frame knows the exposure time and the ISO it was taken with. This module compares
those with the settings and says what to do (pure logic, no depthai: it is tested without a camera):

    guard = SettingsGuard()
    guard.sent()                                        # the settings were just sent: give the camera time
    action = guard.check(params, exposure_us, iso)      # "ok" | "resend" | "escalate" | "restored"

What is checked:
  * manual exposure / ISO (one of them set): the picture says about what was asked (ON_SETTINGS_GUARD_TOL),
  * automatic exposure with a limit (AE limit): the picture does not say much more than the limit,
  * automatic exposure without a limit: there is nothing to compare with.
A difference that lasts BAD_SEC seconds is a "resend" (every RESEND_EVERY_SEC seconds, GIVE_UP_AFTER times); when
sending again does not help the answer is "escalate" (once): the camera has to be started again. When the picture
agrees again after a resend the answer is "restored"."""

import time

from ..core.config import DEFAULT_EXPOSURE_US, DEFAULT_ISO

TOLERANCE = 0.25      # the picture may differ this much from what was asked (the camera rounds the exposure time)
GRACE_SEC = 4.0       # after settings were sent the pictures need this long to show them
BAD_SEC = 3.0         # a difference must last this long before anything is done
RESEND_EVERY_SEC = 10.0
GIVE_UP_AFTER = 3     # sending again this many times without a change: the camera has to be restarted


def mismatch(params, exposure_us, iso, tol=TOLERANCE):
    """Text saying what differs between the settings and the picture ("" = nothing, or nothing to compare)."""
    if not exposure_us:
        return ""
    manual = (params.get("iso") or 0) > 0 or (params.get("exposure_ms") or 0) > 0
    if manual:
        want_exp = (params.get("exposure_ms") or 0) * 1000 or DEFAULT_EXPOSURE_US
        want_iso = params.get("iso") or DEFAULT_ISO
        problems = []
        if abs(exposure_us - want_exp) > tol * want_exp:
            problems.append(f"exposure {exposure_us / 1000:.1f} ms in the picture, {want_exp / 1000:.1f} ms asked")
        if iso and abs(iso - want_iso) > tol * want_iso:
            problems.append(f"ISO {iso} in the picture, {want_iso} asked")
        return "; ".join(problems)
    limit = (params.get("ae_limit_ms") or 0) * 1000
    if limit and exposure_us > limit * (1 + tol):
        return f"exposure {exposure_us / 1000:.1f} ms in the picture, an automatic exposure limited to {limit / 1000:.0f} ms"
    return ""


class SettingsGuard:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._grace_until = clock() + GRACE_SEC   # a camera that was just started
        self._bad_since = None
        self._last_resend = None
        self.resends = 0
        self.escalated = False
        self.reason = ""

    def sent(self):
        """The settings were just sent (a change, or a resend): the pictures show them after a moment."""
        self._grace_until = self._clock() + GRACE_SEC
        self._bad_since = None

    def check(self, params, exposure_us, iso):
        now = self._clock()
        self.reason = mismatch(params, exposure_us, iso)
        if not self.reason:
            self._bad_since = None
            if self.resends:
                self.resends, self._last_resend, self.escalated = 0, None, False
                return "restored"
            return "ok"
        if now < self._grace_until:
            return "ok"
        if self._bad_since is None:
            self._bad_since = now
        if now - self._bad_since < BAD_SEC:
            return "ok"
        if self._last_resend is not None and now - self._last_resend < RESEND_EVERY_SEC:
            return "ok"
        if self.resends >= GIVE_UP_AFTER:
            if self.escalated:
                return "ok"
            self.escalated = True
            return "escalate"
        self.resends += 1
        self._last_resend = now
        return "resend"
