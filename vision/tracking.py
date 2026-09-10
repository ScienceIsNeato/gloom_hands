"""Temporal glue over the per-frame detectors.

A detector answers "is there a person in THIS frame?" with no memory, so
its output flickers whenever a face turns or a blob splits. That is not
how people move. ``Tracker`` keeps one track alive across frames:

* a hit from the primary detector STARTS the track;
* later primary hits near where the track is expected UPDATE it (position,
  size, velocity); hits far away are ignored unless they persist;
* when the primary misses, a hit from the secondary detector (for a face
  or body primary that is the background blob) near the prediction keeps
  the track alive and moves it, keeping the primary's size for distance;
* when nothing confirms it, the track COASTS: it HOLDS its last position
  (a static room: someone who vanished from the detector is almost
  certainly still where they were, having turned their head), marked
  ``coasting`` so callers can show it differently. It never extrapolates
  velocity, which only ever slid jittery tracks off the side of the frame;
* it ENDS when it drifts out of the frame or has gone unconfirmed for
  ``coast_s`` seconds.

Quacks like a detector: ``detect(frame) -> Detection | None`` plus
``last_frame`` / ``roi_y0`` for drawing, so it drops in anywhere.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .detector import Detection


@dataclass
class _Track:
    x: float
    top: float
    bottom: float
    width: float | None
    real_height_m: float | None
    vx: float = 0.0
    last_hit: float = 0.0
    born: float = 0.0
    hits: int = 0


class Tracker:
    def __init__(
        self,
        primary,
        secondary=None,
        coast_s: float = 2.5,
        gate_frac: float = 0.2,
        size_alpha: float = 0.3,
        pos_alpha: float = 0.5,
        vel_alpha: float = 0.1,
        switch_after: int = 3,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.coast_s = coast_s
        self.gate_frac = gate_frac
        self.size_alpha = size_alpha
        self.pos_alpha = pos_alpha
        self.vel_alpha = vel_alpha
        self.switch_after = switch_after
        self._track: _Track | None = None
        self._far_hits = 0
        self._last_t: float | None = None
        self.last_source = ""  # "primary" / "secondary" / "coast" / ""

    # -- detector-like surface ------------------------------------------ #
    @property
    def last_frame(self):  # noqa: ANN201
        return self.primary.last_frame

    @property
    def roi_y0(self) -> int:
        return self.primary.roi_y0

    @property
    def last(self) -> dict:
        return self.primary.last

    @property
    def frame_w(self) -> int:
        return self.primary.frame_w

    @property
    def frame_h(self) -> int:
        return self.primary.frame_h

    @property
    def tracking(self) -> bool:
        return self._track is not None

    def reset(self) -> None:
        self.primary.reset()
        if self.secondary is not None:
            self.secondary.reset()
        self._track = None
        self._far_hits = 0
        self._last_t = None

    # -- the work --------------------------------------------------------- #
    def _gate_px(self) -> float:
        t = self._track
        base = self.gate_frac * max(1, self.primary.frame_w)
        return max(base, 2.0 * t.width) if t is not None and t.width else base

    def _predict(self, dt: float) -> float:
        # Velocity only widens the gate for a walking person; position is
        # never extrapolated when unconfirmed.
        t = self._track
        return t.x if t is None else t.x + t.vx * min(dt, 0.5)

    def _start(self, d: Detection, now: float) -> None:
        self._track = _Track(d.x, d.top, d.bottom, d.width_px, d.real_height_m, 0.0, now, now, 1)
        self._far_hits = 0

    def _update(self, x: float, top: float | None, bottom: float | None, width: float | None,
                now: float, dt: float) -> None:
        t = self._track
        if dt > 0:
            v = (x - t.x) / dt
            t.vx = (1 - self.vel_alpha) * t.vx + self.vel_alpha * v  # heavily smoothed: jitter is not motion
        # Smooth the position too so a still person's box does not twitch
        # with the detector; a real move (bigger than the box) is taken whole.
        big = t.width is not None and abs(x - t.x) > t.width
        t.x = x if big else (1 - self.pos_alpha) * t.x + self.pos_alpha * x
        if top is not None and bottom is not None:
            a = self.size_alpha
            t.top = (1 - a) * t.top + a * top
            t.bottom = (1 - a) * t.bottom + a * bottom
        if width is not None:
            t.width = width if t.width is None else (1 - self.size_alpha) * t.width + self.size_alpha * width
        t.last_hit = now
        t.hits += 1

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        now = time.monotonic()
        dt = 0.0 if self._last_t is None else max(1e-3, now - self._last_t)
        self._last_t = now

        hit = self.primary.detect(frame_bgr)
        t = self._track

        if t is None:
            if hit is not None:
                self._start(hit, now)
                self.last_source = "primary"
                return self._report(now)
            self.last_source = ""
            return None

        pred = self._predict(dt)
        gate = self._gate_px()

        if hit is not None:
            if abs(hit.x - pred) <= gate:
                self._update(hit.x, hit.top, hit.bottom, hit.width_px, now, dt)
                self._far_hits = 0
                self.last_source = "primary"
                return self._report(now)
            # A confident hit somewhere else: only switch if it keeps happening.
            self._far_hits += 1
            if self._far_hits >= self.switch_after:
                self._start(hit, now)
                self.last_source = "primary"
                return self._report(now)

        if self.secondary is not None:
            s = self.secondary.detect(frame_bgr)
            if s is not None and self.secondary.frame_w:
                k = self.primary.frame_w / self.secondary.frame_w  # into the primary's frame
                sx = s.x * k
                if abs(sx - pred) <= gate:
                    # keep the primary's size (distance) but follow the blob sideways
                    self._update(sx, None, None, None, now, dt)
                    self.last_source = "secondary"
                    return self._report(now)

        # coast: hold position, decay the velocity estimate
        if now - t.last_hit > self.coast_s:
            self._track = None
            self.last_source = ""
            return None
        t.vx *= 0.5
        self.last_source = "coast"
        return self._report(now, coasting=True)

    def _report(self, now: float, coasting: bool = False) -> Detection:
        t = self._track
        age = now - t.last_hit
        conf = max(0.0, 1.0 - age / self.coast_s) if coasting else 1.0
        return Detection(
            x=t.x, top=t.top, bottom=t.bottom,
            frame_w=self.primary.frame_w, frame_h=self.primary.frame_h,
            weight=conf, real_height_m=t.real_height_m, width_px=t.width, coasting=coasting,
        )


def make_tracked(kind: str, scale_kw: dict | None = None, **tracker_kw):  # noqa: ANN201
    """A Tracker around detector ``kind``; face/person get the background
    blob as their secondary cue (a static camera makes that cheap and
    reliable)."""
    from .detector import BackgroundDetector, make_detector

    scale_kw = scale_kw or {}
    primary = make_detector(kind, **scale_kw.get(kind, {}))
    secondary = None
    if kind in ("face", "yunet", "haar", "person"):
        secondary = BackgroundDetector(**scale_kw.get("background", {}))
    return Tracker(primary, secondary, **tracker_kw)
