"""Temporal glue over the per-frame detectors.

A detector answers "is there a person in THIS frame?" with no memory, so
its output flickers whenever a face turns or a blob splits. That is not
how people move. ``Tracker`` keeps one track alive across frames:

* NOTHING starts a track on one frame. A detection has to keep appearing,
  in roughly the same place, for CONFIRM_S before a track opens at all.
  Anything shorter is a blip: counted, reported, and otherwise ignored.
* a confirmed run from the primary detector STARTS the track, and so does the
  secondary when the primary has come up empty — a face at the edge of a
  wide lens is stretched and turned away, and a face detector simply will
  not see it, but the blob detector sees anything person-shaped anywhere in
  the frame. Noticing someone at all beats knowing exactly where their
  face is;
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
    proven: bool = True   # has the PRIMARY detector ever confirmed this?


class Tracker:
    def __init__(
        self,
        primary,
        secondary=None,
        coast_s: float = 2.5,
        # How long a detection must keep turning up, in roughly one place,
        # before it is allowed to open a track. One frame of anything is not
        # a person: a face detector will occasionally find a face in wallpaper
        # or a shadow, but it will not find the same one there half a second
        # later. This is the single biggest lever on false positives, and it
        # costs only that half second of response.
        confirm_s: float = 0.5,
        confirm_gate_frac: float = 0.15,    # how far a candidate may wander and still count
        acquire_on_secondary: bool = True,
        # What the blob detector must look like before it is allowed to open a
        # track. Letting it in on size alone was a mistake: a webcam adjusting
        # its exposure makes the WHOLE FRAME differ from the learned
        # background, which arrives as one enormous blob and gets treated as a
        # person standing very close. So: tall, not too tall, not too wide, and
        # still there several frames later.
        min_secondary_frac: float = 0.25,   # of frame height — smaller is not a person
        max_secondary_frac: float = 0.92,   # of frame height — bigger is the whole picture
        max_secondary_width: float = 0.45,  # of frame width — people are not that wide
        secondary_aspect: float = 1.3,      # at least this much taller than wide
        secondary_confirm: int = 4,         # consecutive frames agreeing before it counts
        # A blob may raise the alarm; only a face keeps it up. A track the
        # primary has never confirmed is dropped after this long, so a
        # lighting change or a shifted chair can wake the arm briefly but
        # cannot hold it awake all night — which is what stopped an empty
        # room from ever getting to sleep.
        prove_by_s: float = 5.0,
        gate_frac: float = 0.2,
        size_alpha: float = 0.3,
        pos_alpha: float = 0.75,   # how much of each new reading to take; higher = less lag
        vel_alpha: float = 0.1,
        switch_after: int = 3,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.coast_s = coast_s
        self.confirm_s = confirm_s
        self.confirm_gate_frac = confirm_gate_frac
        self.acquire_on_secondary = acquire_on_secondary
        self.min_secondary_frac = min_secondary_frac
        self.max_secondary_frac = max_secondary_frac
        self.max_secondary_width = max_secondary_width
        self.secondary_aspect = secondary_aspect
        self.secondary_confirm = secondary_confirm
        self.prove_by_s = prove_by_s
        self._cand = 0
        self._cand_x = None
        self._pend: dict[str, object] | None = None   # the candidate being watched
        self.blips = 0            # detections that never lasted long enough
        self.last_blip = ""       # and what the most recent one was
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
        self._cand, self._cand_x = 0, None
        self._pend = None
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

    def _candidate(self, d: Detection, now: float, source: str) -> bool:
        """Has this detection been turning up in one place for long enough?

        Returns True exactly once, on the reading that completes the run.
        A detection somewhere else, or after a gap, starts the count again
        and the abandoned one is recorded as a blip.
        """
        gate = self.confirm_gate_frac * max(1, d.frame_w)
        p = self._pend
        fresh = (p is None or p["source"] != source
                 or abs(d.x - float(p["x"])) > gate
                 or now - float(p["last"]) > 0.6)
        if fresh:
            if p is not None and float(p["last"]) - float(p["since"]) < self.confirm_s:
                self.blips += 1
                self.last_blip = (f"{p['source']} at x={float(p['x']):.0f} for "
                                  f"{float(p['last']) - float(p['since']):.2f}s")
            self._pend = {"since": now, "x": d.x, "last": now, "n": 1, "source": source}
            return False
        p["x"], p["last"], p["n"] = d.x, now, int(p["n"]) + 1
        return (now - float(p["since"])) >= self.confirm_s and int(p["n"]) >= 2

    def _forget_candidate(self, now: float) -> None:
        p = self._pend
        if p is not None:
            if float(p["last"]) - float(p["since"]) < self.confirm_s:
                self.blips += 1
                self.last_blip = (f"{p['source']} at x={float(p['x']):.0f} for "
                                  f"{float(p['last']) - float(p['since']):.2f}s")
            self._pend = None

    def person_shaped(self, d: Detection) -> bool:
        """Could this blob be a person standing there?

        Rejects the two things that are not: a smear too small to be anybody,
        and the frame-filling flash that an exposure change produces. Between
        those, people are taller than they are wide.
        """
        if not d.frame_h or not d.frame_w:
            return False
        tall = d.height_px / d.frame_h
        wide = (d.width_px or 0.0) / d.frame_w
        if not self.min_secondary_frac <= tall <= self.max_secondary_frac:
            return False
        if wide > self.max_secondary_width:
            return False
        return not d.width_px or d.height_px >= self.secondary_aspect * d.width_px

    def _rescale(self, d: Detection) -> Detection:
        """A secondary detection expressed in the primary's pixel frame, since
        the two may be running at different scales."""
        if not d.frame_w or not self.primary.frame_w or d.frame_w == self.primary.frame_w:
            return d
        k = self.primary.frame_w / d.frame_w
        return Detection(
            x=d.x * k, top=d.top * k, bottom=d.bottom * k,
            frame_w=self.primary.frame_w, frame_h=self.primary.frame_h,
            weight=d.weight, real_height_m=d.real_height_m,
            width_px=(d.width_px * k if d.width_px else None),
        )

    def _start(self, d: Detection, now: float, proven: bool = True) -> None:
        self._track = _Track(d.x, d.top, d.bottom, d.width_px, d.real_height_m,
                             0.0, now, now, 1, proven)
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
                if not self._candidate(hit, now, "face"):
                    self.last_source = ""
                    return None      # seen, but not for long enough to believe
                self._pend = None
                self._start(hit, now)
                self.last_source = "primary"
                return self._report(now)
            # The primary is the precise one and it has blind spots. Let the
            # secondary open a track when the primary found nothing at all,
            # provided the blob is big enough to be a person rather than a
            # curtain. Requiring a face to START tracking meant anyone the face
            # detector could not resolve was invisible, however obvious they
            # were to look at.
            if self.secondary is not None and self.acquire_on_secondary:
                s = self.secondary.detect(frame_bgr)
                if s is not None and self.person_shaped(s):
                    if self._candidate(s, now, "blob"):
                        self._pend = None
                        self._start(self._rescale(s), now, proven=False)
                        self.last_source = "secondary"
                        return self._report(now)
                else:
                    self._forget_candidate(now)
            if hit is None and (self.secondary is None or not self.acquire_on_secondary):
                self._forget_candidate(now)
            self.last_source = ""
            return None

        # A track the primary has never vouched for does not get to stay.
        if not t.proven and now - t.born > self.prove_by_s:
            self._track = None
            self.last_source = ""
            return None

        pred = self._predict(dt)
        gate = self._gate_px()

        if hit is not None:
            if abs(hit.x - pred) <= gate:
                self._update(hit.x, hit.top, hit.bottom, hit.width_px, now, dt)
                self._far_hits = 0
                t.proven = True
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
