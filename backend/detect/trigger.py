"""Center-crossing trigger. Pure Python (no camera dependency) so it can be tested offline.

A belt carries shoes across the field of view. For every tracked object we remember every
detection and the one that came closest to the frame center. When the object has moved away
from the center again (it crossed the center line) or has left the view, the trigger fires and
reports up to three shots around the center:

    before   the object was ~spread away from the center, on the way in
    center   the closest approach to the center
    after    the object was ~spread away from the center, on the way out

Direction is not needed: "before" and "after" are simply the observations before / after the
closest approach. With shots=1 only the center shot is reported (fires as soon as the object
has moved ``away_hyst`` past the center). The caller fetches, for each shot, the full-size frame
whose timestamp is closest to the shot.

Coordinates are normalized to the frame (0..1); timestamps are seconds on the device clock."""

import math
from dataclasses import dataclass, field

MAX_HISTORY = 600


@dataclass
class Det:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    conf: float
    label: str
    target: bool = True

    @property
    def cx(self):
        return (self.xmin + self.xmax) / 2

    @property
    def cy(self):
        return (self.ymin + self.ymax) / 2

    @property
    def area(self):
        """Part of the frame the bbox covers (0..1)."""
        return max(0.0, self.xmax - self.xmin) * max(0.0, self.ymax - self.ymin)


@dataclass
class Shot:
    kind: str       # "before" | "center" | "after"
    ts: float
    det: Det
    dist: float


@dataclass
class Sample:
    ts: float
    det: Det
    dist: float
    track_id: int
    n_obs: int
    shots: list = field(default_factory=list)   # time-ordered; always contains the "center" shot
    history: list = field(default_factory=list)  # (ts, det, dist) of every observation of the track


class _Track:
    def __init__(self, track_id, det, ts):
        self.id = track_id
        self.cx, self.cy = det.cx, det.cy
        self.last_ts = ts
        self.n_obs = 0
        self.best = None
        self.fired = False
        self.history = []   # (ts, det, dist) of every observation, oldest first

    def observe(self, ts, det, dist):
        self.history.append((ts, det, dist))
        if len(self.history) > MAX_HISTORY:
            del self.history[0]


class CenterTrigger:
    def __init__(self, axis="xy", center_tol=0.30, away_hyst=0.03, lost_sec=0.4,
                 min_obs=3, edge_margin=0.01, match_gate=0.35, hold_sec=0.8,
                 shots=1, spread=0.15, log=None):
        self.hold_sec = hold_sec
        self.shots = 3 if shots >= 3 else 1
        self.spread = spread
        self._log = log or (lambda msg: None)
        self.axis = axis
        self.center_tol = center_tol
        self.away_hyst = away_hyst
        self.lost_sec = lost_sec
        self.min_obs = min_obs
        self.edge_margin = edge_margin
        self.match_gate = match_gate
        self.reset()

    def reset(self):
        self._tracks = []
        self._next_id = 1

    def _dist(self, det):
        dx, dy = abs(det.cx - 0.5), abs(det.cy - 0.5)
        if self.axis == "x":
            return dx
        if self.axis == "y":
            return dy
        return math.hypot(dx, dy)

    def _touches_edge(self, det):
        m = self.edge_margin
        return det.xmin < m or det.ymin < m or det.xmax > 1 - m or det.ymax > 1 - m

    def _eligible(self, track):
        return (not track.fired and track.best is not None
                and track.n_obs >= self.min_obs
                and track.best.dist <= self.center_tol)

    def _moved_away(self, track, dist):
        """The object is far enough past its closest approach to fire."""
        if dist <= track.best.dist + self.away_hyst:
            return False
        return self.shots == 1 or dist >= self.spread

    def _why_not(self, track):
        if track.n_obs < self.min_obs:
            return f"seen only {track.n_obs}x (min {self.min_obs})"
        if track.best is None:
            return "always touching the frame edge (see ON_EDGE_MARGIN)"
        return f"closest approach {track.best.dist:.3f} > center tolerance {self.center_tol}"

    # ---- shot selection -------------------------------------------------------

    def _pick_before(self, track):
        """Observation before the closest approach whose distance is nearest to ``spread``."""
        best = track.best
        cands = [h for h in track.history if h[0] < best.ts - 1e-9 and h[2] >= self.spread * 0.5]
        return min(cands, key=lambda h: abs(h[2] - self.spread)) if cands else None

    def _pick_after(self, track):
        """First observation past the closest approach that reached ``spread`` (else the farthest)."""
        best = track.best
        cands = [h for h in track.history
                 if h[0] > best.ts + 1e-9 and h[2] >= best.dist + self.away_hyst]
        if not cands:
            return None
        reached = [h for h in cands if h[2] >= self.spread]
        return reached[0] if reached else max(cands, key=lambda h: h[2])

    def _fire(self, track, reason, after=None):
        track.fired = True
        b = track.best
        shots = []
        if self.shots >= 3:
            before = self._pick_before(track)
            if before:
                shots.append(Shot("before", *before))
        shots.append(Shot("center", b.ts, b.det, b.dist))
        if self.shots >= 3:
            after = after or self._pick_after(track)
            if after:
                shots.append(Shot("after", *after))
        self._log(f"[trigger] track {track.id} FIRE ({reason}): dist={b.dist:.3f} obs={track.n_obs} "
                  f"shots={'+'.join(s.kind for s in shots)}")
        return Sample(ts=b.ts, det=b.det, dist=b.dist, track_id=track.id, n_obs=track.n_obs, shots=shots,
                      history=list(track.history))

    # ---- main entry -----------------------------------------------------------

    def update(self, ts, dets):
        """Feed one detection message (call it for every frame, even with no detections).
        Returns the Samples whose capture should happen now."""
        out = []
        pairs = sorted(
            (math.hypot(d.cx - t.cx, d.cy - t.cy), ti, di)
            for ti, t in enumerate(self._tracks) for di, d in enumerate(dets)
        )
        used_t, used_d = set(), set()
        for gap, ti, di in pairs:
            if gap > self.match_gate:
                break
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            track, det = self._tracks[ti], dets[di]
            track.cx, track.cy, track.last_ts = det.cx, det.cy, ts
            track.n_obs += 1
            dist = self._dist(det)
            track.observe(ts, det, dist)
            if not self._touches_edge(det) and (track.best is None or dist < track.best.dist):
                track.best = Sample(ts, det, dist, track.id, 0)
            elif (self._eligible(track) and ts > track.best.ts
                  and self._moved_away(track, dist)):
                out.append(self._fire(track, "moved past center", after=(ts, det, dist)))
            # Object stopped (or crawling): its best sample has not improved for hold_sec.
            if self._eligible(track) and ts - track.best.ts >= self.hold_sec:
                out.append(self._fire(track, f"no closer approach for {self.hold_sec}s"))

        for di, det in enumerate(dets):
            if di not in used_d:
                track = _Track(self._next_id, det, ts)
                self._next_id += 1
                track.n_obs = 1
                dist = self._dist(det)
                track.observe(ts, det, dist)
                if not self._touches_edge(det):
                    track.best = Sample(ts, det, dist, track.id, 0)
                self._log(f"[trigger] track {track.id} new: {det.label} {det.conf:.2f}"
                          + (" (touching frame edge, not a capture candidate)"
                             if track.best is None else ""))
                self._tracks.append(track)

        alive = []
        for ti, track in enumerate(self._tracks):
            if ti not in used_t and ts - track.last_ts > self.lost_sec:
                if self._eligible(track):
                    out.append(self._fire(track, "left view"))
                elif not track.fired:
                    self._log(f"[trigger] track {track.id} dropped without capture: "
                              + self._why_not(track))
                continue
            alive.append(track)
        self._tracks = alive
        return out
