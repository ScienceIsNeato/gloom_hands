#!/usr/bin/env python3
"""Gloom hand mode (Tears of the Kingdom): the arm points toward you and
sweeps left <-> right, searching for victims. With eyes, it stops
searching when it finds one.

    ./gloom.py                 # wireless (BLE), blind hunt
    ./gloom.py --usb           # wired
    ./gloom.py --eyes          # webcam: lock the base onto whoever is there
    ./gloom.py --eyes --dry    # no arm: print what it would send (test the eyes)
    ./gloom.py --eyes --dry --show      # ...and open a window showing what it sees
    ./gloom.py --eyes --detector face   # Haar face detector instead of frame differencing
    ./gloom.py --eyes --video-src 1     # another camera, or a video file

All values are CENTERED DEGREES (0.0 = servo midpoint, +/-120 span,
soft-limited per angles.LIMITS_DEG). Three layers of creep, tunable below:
  1. HUNT   — slow uneven base sweep between SWEEP_LO_DEG and SWEEP_HI_DEG
  2. WRITHE — continuous small motion: gripper gropes, wrist rolls and nods
  3. TWITCH — random freezes ("...did it hear something?") and sudden fast
              snaps to a new heading
...and with --eyes a fourth:
  4. LOCK   — the vision package finds the person in the webcam
              frame and works out their bearing from the BASE PIVOT (the
              camera can sit anywhere: see CAMERA below). The base snaps to
              them and follows; when nobody has moved for LOST_AFTER_S the
              sweep resumes from wherever the base is.
  5. STRIKE — while locked, every so often the arm draws back into a coil
              (POSE_COIL_DEG), sits there still tracking you, then lurches
              back out to the pointing pose with an overshoot.

POSE_POINT_DEG is the "arm extended toward the viewer" posture. Every arm
is assembled slightly differently — pre-flight it joint by joint with
move_ble.py before the first hunt, and back off anything that touches or
buzzes. Ctrl-C exits through a calm return to rest.

Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, *(("Scripts", "python.exe") if _os.name == "nt" else ("bin", "python")))
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    if _os.name == "nt":
        # execv on Windows detaches from the console (the prompt returns while
        # output keeps coming), so spawn and pass the exit code through.
        import subprocess as _sp
        _sys.exit(_sp.call([_venv_py] + _sys.argv))
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import argparse
import math
import random
import threading
import time

from angles import clamp_deg, servo_units

# ---- the posture (TUNE THESE FIRST, in degrees) ---------------------- #
POSE_POINT_DEG = {
    5: 36.0,   # shoulder: leaned forward
    4: -48.0,  # elbow: bent back to bring the forearm level
    3: 12.0,   # wrist bend: aimed at the viewer
    2: 0.0,    # wrist roll: knuckles up
    1: 12.0,   # gripper: half-open, ready to grab
}
POSE_REST_DEG = {sid: 0.0 for sid in range(1, 7)}
# Drawn back like a snake about to strike: shoulder leaned well back,
# elbow folded hard, wrist curled. Dialed in on the arm with ./pose.py
# (which prints this line); re-run it if the arm is reassembled.
POSE_COIL_DEG = {5: -57.0, 4: -100.0, 3: 37.0}

# ---- the hunt -------------------------------------------------------- #
SWEEP_LO_DEG, SWEEP_HI_DEG = -53.0, 53.0  # base range of the search
SWEEP_PERIOD = 14.0                       # seconds per full left-right-left pass
TICK = 0.22                               # seconds between command packets

# ---- the writhe (degrees of travel around the pose) ------------------ #
# Each oscillator is a depth in degrees and a rate in radians/second. Two
# budgets constrain the rates, and both bite well below where you would
# expect:
#   RENDER  we only send a packet every TICK, so a component needs roughly
#           eight packets per cycle to read as motion rather than stepping.
#           At the current TICK that ceiling is about 0.57 Hz (3.6 rad/s).
#   CURRENT a servo's draw follows how fast it is being asked to move, and
#           depth * rate is that demand. The gripper used to spend half its
#           budget on a tremor worth a fifth of its travel.
# test_motion.py checks both. Run it after changing anything here.
GROPE_DEPTH, GROPE_RATE = 22.0, 1.9     # gripper: the slow opening and closing
TREMOR_DEPTH, TREMOR_RATE = 6.0, 3.1    # gripper: the shiver laid over it
ROLL_DEPTH, ROLL_RATE = 14.0, 0.7       # wrist roll writhe
NOD_DEPTH, NOD_RATE = 11.0, 1.3         # wrist bend searching nods

# ---- the twitch ------------------------------------------------------ #
FREEZE_CHANCE = 0.012   # per tick: freeze mid-sweep...
FREEZE_S = (0.8, 2.2)   # ...for this long
SNAP_CHANCE = 0.010     # per tick: sudden fast snap to a new heading
SNAP_MS = 280           # how fast the snap lands (small ms = violent)

# ---- the strike (only while locked on) ------------------------------- #
COIL_EVERY_S = (11.0, 24.0)  # seconds of TRACKING you before the next coil. The strike is
                             # punctuation; following the victim is the sentence.
COIL_MS = 2400               # how slowly it draws back (menacing = slow, and cheaper in current)
COIL_STAGGER_S = 0.6         # forearm folds first, THEN the shoulder leans back. Moving both
                             # at once was the biggest current draw in the whole routine — the
                             # coil lifts the arm against gravity, where the lurch falls with it.
COIL_HOLD_S = (2.0, 5.0)     # how long it stays coiled, still tracking
LURCH_MS = 500               # how fast it comes out at you (small = violent; below ~450 the
                             # servos are flat out and the supply sag can drop the Bluetooth link)
LURCH_OVERSHOOT_DEG = 10.0   # shoulder past the point pose at the end of the lurch...
SETTLE_MS = 450              # ...then settles back over this long

# ---- the eyes (--eyes) ----------------------------------------------- #
# Where the camera sits, measured from the BASE PIVOT, looking down from
# above. +x = the direction the hand points at base 0 deg, +y = LEFT of
# that, yaw = which way the lens points relative to +x (left = positive).
# A single camera gives bearing but not range, so the offset only bites
# once range is estimated from the person's height in the frame; with a
# zero offset it does not matter at all. Dial these in with:
#   ./eyes.py --hfov .. --cam-x .. --cam-y .. --cam-yaw ..
# DEFAULT: the camera sits on the robot's front face, looking where the arm
# looks. That is the sane rig and the one to aim for — the offset maths only
# earns its keep when the lens genuinely cannot live there. To say otherwise,
# measure from the pivot: a camera two feet to the arm's LEFT is y_m=+0.61,
# one a room away facing back at the arm is x_m=4.57, yaw_deg=180.
CAMERA = dict(x_m=0.0, y_m=0.0, yaw_deg=0.0, hfov_deg=60.0, mirrored=False)
PERSON_HEIGHT_M = 1.7
VIDEO_SRC = "0"
CAPTURE_SIZE = (1280, 720)
# "face": YuNet face detection (default; Haar cascades if the model is missing) · "background": static camera,
# learns the empty scene, anything that differs is the person (cheapest that keeps a still person) ·
# "motion": legacy frame differencing, only sees movement · "person": HOG, whole bodies far away
DETECTOR = "face"
DETECT_ROI = (0.0, 1.0)  # motion detector: fraction of the frame rows to watch (top, bottom)
# Which way servo 6 turns for a positive angle: an assembly fact, not a
# preference. +1 means positive degrees swing the arm to ITS left, which is
# YOUR right when you are standing in front of it. Settle it in ten seconds
# without the camera: stand in front, run  ./move_ble.py 6 30  and watch.
#   hand goes toward your right -> +1 (this default)
#   hand goes toward your left  -> -1
# ./gloom.py --eyes --flip tries the other sign for one run without editing.
BASE_SIGN = +1.0
LOCK_SMOOTH = 4         # readings averaged while locked (at TICK rate; small = twitchy)
TRACK_COAST_S = 1.5     # a track survives this long unconfirmed, coasting on its last motion
# TRACKING IS THE POINT, and pointing at where the victim truly stands does
# not deliver it. That angle is honest but useless at both ends: with the
# camera 4.6 m from the arm, someone beside the lens crossing the entire
# frame moves the base about 18 degrees, while someone standing next to the
# arm swings it through 90 for a single step. Range comes from apparent face
# height and is rough, so the second case is jittery as well as violent.
#
# Instead the victim's position ACROSS THE FRAME is mapped onto the arm's
# sweep. The mapping runs through the same offset geometry, evaluated at the
# victim's own distance, so the camera pose and yaw are still honoured and
# the response stays even whether they are at the lens or at the arm.
TRACK_FILL = 0.85       # of SWEEP_LO..SWEEP_HI that a full frame crossing uses
# Following the victim is now worth 90 degrees of base travel instead of 18,
# and a face that jumps across the frame between two ticks would ask for that
# in a fifth of a second — over 400 deg/s, six times the busiest writhe joint,
# on a supply already known to brown out. So the base is allowed to CHASE at a
# bounded rate rather than teleport. It still gets there, just not all at once,
# and a head that swings round smoothly is more menacing than one that snaps.
TRACK_SLEW_DPS = 75.0   # how fast the base may follow, degrees per second
SNAP_SLEW_DPS = 200.0   # ...and how fast the first lunge of attention may be. Faster than
                        # tracking, because noticing you should look like noticing you, but
                        # still bounded: a 90 degree snap in SNAP_MS would be 320 deg/s.
TRACK_NOMINAL_M = 1.2   # a typical standing distance, for the startup message
LOST_AFTER_S = 4.0      # nobody seen for this long -> back to the hunt


class Backend:
    def __init__(self, use_usb: bool) -> None:
        if use_usb:
            import xarm

            self._arm = xarm.Controller("USB")
            self._servo = xarm.Servo
        else:
            from ble_arm import BleArm

            self._arm = BleArm()
            self._servo = None

    def send(self, moves_deg: dict[int, float], dur_ms: int) -> None:
        units = {sid: servo_units(sid, d) for sid, d in moves_deg.items()}
        if self._servo is not None:
            self._arm.setPosition(
                [self._servo(sid, pos) for sid, pos in units.items()], dur_ms, wait=False
            )
            return
        try:
            self._arm.set_position(list(units.items()), duration_ms=dur_ms)  # reconnects itself once
        except Exception as err:  # noqa: BLE001 - keep hunting; the next tick retries
            print(f"arm: move skipped ({err})")

    def relax(self) -> None:
        """Cut the motors. Parking at rest still leaves every servo holding;
        a cantilevered joint will draw current until it sings."""
        try:
            if self._servo is not None:
                self._arm.servoOff()
            else:
                self._arm.unload()
        except Exception as err:  # noqa: BLE001 - we are shutting down anyway
            print(f"arm: could not unload ({err})")


class DryBackend:
    """No arm: print the base heading (and any elbow/shoulder move) so the
    eyes and the strike can be tested anywhere."""

    def relax(self) -> None:
        print("  (relax)")

    def send(self, moves_deg: dict[int, float], dur_ms: int) -> None:
        if 4 in moves_deg or 5 in moves_deg:
            joints = " ".join(f"s{sid}={d:+.0f}" for sid, d in sorted(moves_deg.items()))
            print(f"  pose -> {joints} over {dur_ms} ms")
        elif 6 in moves_deg:
            print(f"  base -> {moves_deg[6]:+6.1f} deg over {dur_ms} ms")


class Eyes:
    """Webcam -> bearing of the victim from the base pivot, in centered
    degrees for servo 6 (sign applied). A background thread keeps the
    newest frame so the hunt loop never reads a stale one. Detections go
    through vision.tracking.Tracker, so the victim is followed, not
    rediscovered, frame to frame."""

    def __init__(self, video_src: str, detector: str, show: bool = False) -> None:
        import cv2
        from vision import CameraPose, Locator, Smoother, make_tracked
        from vision.capture import as_source, open_capture

        self.kind = detector
        self._show = show

        src = as_source(video_src)
        self._cv2 = cv2
        self._cam = open_capture(src, *CAPTURE_SIZE)
        # Tracker on top of the detector: a person, once found, is followed
        # across frames (coasting on their last motion when the detector
        # blinks) instead of being rediscovered from scratch every tick.
        self._det = make_tracked(detector, {"motion": {"roi": DETECT_ROI}}, coast_s=TRACK_COAST_S)
        pose = CameraPose(**CAMERA)
        # If the camera looks back toward the pivot, nobody it can see is
        # beyond the pivot; but a distance estimate that overshoots the
        # camera-to-pivot gap would put them there and flip the bearing to
        # the soft limit. Cap the range just short of the pivot.
        to_pivot = (-pose.x_m, -pose.y_m)
        axis = (math.cos(math.radians(pose.yaw_deg)), math.sin(math.radians(pose.yaw_deg)))
        gap = math.hypot(*to_pivot)
        facing_pivot = gap > 0.5 and (to_pivot[0] * axis[0] + to_pivot[1] * axis[1]) / gap > 0.5
        max_range = 0.9 * gap if facing_pivot else 10.0
        self._loc = Locator(pose, person_height_m=PERSON_HEIGHT_M, range_clamp_m=(0.4, max_range))
        if facing_pivot:
            print(f"eyes: camera faces the arm {gap:.1f} m away; distance capped at {max_range:.1f} m")
        self._reach = (SWEEP_HI_DEG - SWEEP_LO_DEG) / 2 * TRACK_FILL
        print(f"eyes: crossing the frame swings the base {2 * self._reach:.0f} deg "
              f"(true angle at {TRACK_NOMINAL_M:.1f} m would be {self._frame_span():.0f})")
        self._smooth = Smoother(window=LOCK_SMOOTH)
        self._last_base, self._last_at = 0.0, time.monotonic()
        self._frame = None
        self._ended = False
        self._lock = threading.Lock()
        # A video file is played back in real time so the hunt sees it as
        # the camera would; a live camera is read as fast as it delivers.
        fps = self._cam.get(cv2.CAP_PROP_FPS) if isinstance(src, str) else 0.0
        self._pace = 1.0 / fps if fps and fps > 0 else 0.0
        threading.Thread(target=self._pump, daemon=True).start()

    def _frame_span(self) -> float:
        """Base travel, in degrees, for a victim crossing the frame at the
        nominal distance — before any gain is applied."""
        from vision import Detection
        from vision.geometry import focal_px

        w, h = CAPTURE_SIZE
        face_m = 0.2
        hpx = focal_px(w, CAMERA["hfov_deg"]) * face_m / TRACK_NOMINAL_M
        edges = [
            self._loc.locate(Detection(x=x, top=0.0, bottom=hpx, frame_w=w, frame_h=h,
                                       real_height_m=face_m)).bearing_deg
            for x in (w * 0.05, w * 0.95)
        ]
        return abs(edges[1] - edges[0])

    def _bearing_at(self, x_px: float, range_m: float, face_m: float = 0.2) -> float:
        """Pivot bearing of someone at this column of the frame and this range."""
        from vision import Detection
        from vision.geometry import focal_px

        w, h = CAPTURE_SIZE
        hpx = focal_px(w, CAMERA["hfov_deg"]) * face_m / max(0.05, range_m)
        return self._loc.locate(
            Detection(x=x_px, top=0.0, bottom=hpx, frame_w=w, frame_h=h, real_height_m=face_m)
        ).bearing_deg

    def _map_to_sweep(self, t) -> float:  # noqa: ANN001 - a vision.Target
        """Where the victim sits across the frame, as a base angle.

        The frame edges are re-evaluated at the victim's OWN distance every
        time, so this is the real geometry rather than a fudge factor, and it
        cannot saturate the way a flat gain does when they come close."""
        w = CAPTURE_SIZE[0]
        left = self._bearing_at(w * 0.02, t.cam_range_m)
        right = self._bearing_at(w * 0.98, t.cam_range_m)
        # left edge of the frame is the arm's LEFT, which is a POSITIVE bearing,
        # so the span runs left-minus-right. Getting this backwards silently
        # inverts the whole system and looks exactly like a wrong BASE_SIGN.
        centre, half = (left + right) / 2, (left - right) / 2
        if abs(half) < 1e-3:
            return 0.0
        frac = max(-1.0, min(1.0, (t.bearing_deg - centre) / half))
        return frac * self._reach

    def _pump(self) -> None:
        while True:
            ok, frame = self._cam.read()
            if not ok:
                self._ended = True
                return
            with self._lock:
                self._frame = frame
            if self._pace:
                time.sleep(self._pace)

    @property
    def ended(self) -> bool:
        return self._ended

    def look(self, first: bool) -> tuple[float, "object"] | None:
        """(base degrees, Target) of the victim, or None if nobody was seen.
        The Target carries the camera-relative distance and angle too."""
        with self._lock:
            frame, self._frame = self._frame, None
        if frame is None:
            return None
        t0 = time.perf_counter()
        d = self._det.detect(frame)
        t = self._loc.locate(d) if d is not None else None
        if self._show:
            self._draw(d, t, 1000 * (time.perf_counter() - t0))
        if t is None:
            return None
        deg = BASE_SIGN * self._map_to_sweep(t)
        if first:
            self._smooth.reset(deg)  # snap straight there, no lag from the old average
            self._last_base, self._last_at = deg, time.monotonic()
            return deg, t
        deg = self._smooth.push(deg)
        # Chase, do not teleport: cap how fast the base is allowed to follow.
        now = time.monotonic()
        budget = TRACK_SLEW_DPS * max(1e-3, now - self._last_at)
        deg = max(self._last_base - budget, min(self._last_base + budget, deg))
        self._last_base, self._last_at = deg, now
        return deg, t

    def _draw(self, d, t, ms: float) -> None:  # noqa: ANN001
        from vision.preview import draw

        view = self._det.last_frame.copy()
        draw(view, d, t, self._det.roi_y0, ms, self.kind)
        self._cv2.imshow("gloom eyes", view)
        if (self._cv2.waitKey(1) & 0xFF) == 27:
            raise KeyboardInterrupt

    def close(self) -> None:
        self._cam.release()
        if self._show:
            self._cv2.destroyAllWindows()


def writhe_deg(now: float, base: float, coiled: bool = False) -> dict[int, float]:
    """WRITHE: independent slow oscillators so nothing ever repeats — the
    gripper gropes, the wrist rolls and nods, the base holds its heading.
    While coiled the wrist stays curled and only quivers.

    Shared with servo_watch.py so the diagnostic soaks the servos with
    exactly the motion the hunt uses, not an approximation of it."""
    wrist_home, nod = (POSE_COIL_DEG[3], NOD_DEPTH * 0.3) if coiled else (POSE_POINT_DEG[3], NOD_DEPTH)
    return {
        6: clamp_deg(6, base),
        1: clamp_deg(1, POSE_POINT_DEG[1]
                     + GROPE_DEPTH * math.sin(GROPE_RATE * now + 1.0)
                     + TREMOR_DEPTH * math.sin(TREMOR_RATE * now)),
        2: clamp_deg(2, POSE_POINT_DEG[2] + ROLL_DEPTH * math.sin(ROLL_RATE * now)),
        3: clamp_deg(3, wrist_home + nod * math.sin(NOD_RATE * now + 2.1)),
    }


#: Every writhe oscillator, as (servo, label, depth_deg, rate_rad_s). Used by
#: test_motion.py to police the render and current budgets above.
WRITHE_TERMS = [
    (1, "grope", GROPE_DEPTH, GROPE_RATE),
    (1, "tremor", TREMOR_DEPTH, TREMOR_RATE),
    (2, "roll", ROLL_DEPTH, ROLL_RATE),
    (3, "nod", NOD_DEPTH, NOD_RATE),
]


def sweep_deg(now: float, heading_offset: float) -> float:
    """HUNT: uneven sinusoid — jittered phase makes the pace lurch."""
    phase = 2 * math.pi * (now / SWEEP_PERIOD) + heading_offset
    sweep = 0.5 * (1 + math.sin(phase + 0.35 * math.sin(2.7 * now)))
    return SWEEP_LO_DEG + sweep * (SWEEP_HI_DEG - SWEEP_LO_DEG)


def rephase(now: float, base_deg: float) -> float:
    """Heading offset that makes the sweep pass through base_deg right now,
    so resuming the hunt after a lock does not jump."""
    frac = (base_deg - SWEEP_LO_DEG) / (SWEEP_HI_DEG - SWEEP_LO_DEG)
    frac = max(0.0, min(1.0, frac))
    return math.asin(2 * frac - 1) - 0.35 * math.sin(2.7 * now) - 2 * math.pi * (now / SWEEP_PERIOD)


def main() -> None:
    p = argparse.ArgumentParser(description="gloom hand hunt")
    p.add_argument("--usb", action="store_true", help="wired instead of Bluetooth")
    p.add_argument("--dry", action="store_true", help="no arm: print base headings")
    p.add_argument("--eyes", action="store_true", help="track people with the webcam")
    p.add_argument("--video-src", default=VIDEO_SRC, help="camera index or video file")
    p.add_argument("--detector", default=DETECTOR, choices=("background", "motion", "face", "yunet", "haar", "person"),
                   help="background subtraction (default), frame differencing, face (YuNet, or Haar without its model), or HOG person")
    p.add_argument("--show", action="store_true", help="open a window showing what the eyes see")
    p.add_argument("--flip", action="store_true",
                   help="invert BASE_SIGN for this run — use it to settle which way servo 6 turns")
    a = p.parse_args()

    if a.flip:
        globals()["BASE_SIGN"] = -BASE_SIGN
        print(f"eyes: BASE_SIGN flipped to {BASE_SIGN:+.0f} for this run")
    eyes = Eyes(a.video_src, a.detector, show=a.show) if a.eyes else None
    arm = DryBackend() if a.dry else Backend(a.usb)
    print("assuming the pose...")
    base = (SWEEP_LO_DEG + SWEEP_HI_DEG) / 2
    arm.send({**POSE_POINT_DEG, 6: base}, 2500)
    time.sleep(2.7)
    print("hunting" + (f" with eyes open ({eyes.kind})" if eyes else "") + ". Ctrl-C to release the victim.")

    t0 = time.monotonic()
    heading_offset = 0.0  # phase shift accumulated by snaps
    frozen_until = 0.0
    last_seen = -1e9      # when the eyes last saw someone
    locked = False
    last_report = -1e9    # when we last showed the tracking on stdout
    prev_base = base      # where the base was before the latest tracked update
    coil = "out"          # "out" (pointing) / "coiling" / "coiled" — the strike cycle
    coil_at = 0.0         # when the next phase of the strike cycle happens
    try:
        while True:
            now = time.monotonic() - t0

            # LOCK: the eyes keep watching even mid-freeze
            if eyes is not None:
                if eyes.ended:
                    print("video ended")
                    break
                seen = eyes.look(first=not locked)
                if seen is not None:
                    base, t = seen
                    base = clamp_deg(6, base)
                    if not locked:
                        print(
                            f"victim spotted: camera sees {t.cam_range_m:.1f} m at "
                            f"{t.cam_bearing_deg:+.1f} deg -> base {base:+.1f} deg — snapping"
                        )
                        locked = True
                        last_seen = now
                        coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)
                        # Fast, but never faster than SNAP_SLEW_DPS: a snap right
                        # across the sweep at a fixed SNAP_MS is a current spike.
                        travel = abs(base - prev_base)
                        dur = max(SNAP_MS, int(1000 * travel / SNAP_SLEW_DPS))
                        arm.send({6: base}, dur)
                        time.sleep(dur / 1000 + 0.1)
                        prev_base = base
                        last_report = now
                        continue
                    last_seen = now
                    prev_base = base
                    if now - last_report >= 1.5:  # show that it is still following
                        state = "coiled" if coil != "out" else "tracking"
                        print(f"  {state}: face {t.cam_bearing_deg:+5.1f} deg at "
                              f"{t.cam_range_m:.1f} m -> base {base:+6.1f} deg")
                        last_report = now
                elif locked and now - last_seen > LOST_AFTER_S:
                    print("lost it... resuming the hunt")
                    locked = False
                    heading_offset = rephase(now, base)
                    if coil != "out":
                        arm.send({sid: POSE_POINT_DEG[sid] for sid in (5, 4, 3)}, 1500)
                        coil = "out"

            if now < frozen_until:
                time.sleep(TICK)
                continue
            if random.random() < FREEZE_CHANCE:
                frozen_until = now + random.uniform(*FREEZE_S)  # ...it heard something
                continue

            if not locked:
                base = sweep_deg(now, heading_offset)
                if random.random() < SNAP_CHANCE:
                    heading_offset += random.uniform(-2.2, 2.2)
                    arm.send({6: clamp_deg(6, base)}, SNAP_MS)  # violent re-aim
                    time.sleep(SNAP_MS / 1000 + 0.1)
                    continue
            elif now >= coil_at:
                # STRIKE cycle: out -> folding (forearm) -> coiling (shoulder
                # leans back) -> coiled (hold, still tracking) -> LURCH -> out.
                # The two halves are deliberately staggered: folding the elbow
                # and leaning the shoulder together is what browned out the
                # supply, and a creature drawing back does it in that order
                # anyway.
                if coil == "out":
                    print("...drawing back")
                    arm.send({sid: POSE_COIL_DEG[sid] for sid in (4, 3)}, COIL_MS)
                    coil, coil_at = "folding", now + COIL_STAGGER_S
                elif coil == "folding":
                    arm.send({5: POSE_COIL_DEG[5], 6: clamp_deg(6, base)}, COIL_MS)
                    coil, coil_at = "coiling", now + COIL_MS / 1000
                elif coil == "coiling":
                    coil, coil_at = "coiled", now + random.uniform(*COIL_HOLD_S)
                else:
                    print("LURCH")
                    strike = {sid: POSE_POINT_DEG[sid] for sid in (5, 4, 3)}
                    strike[5] = clamp_deg(5, strike[5] + LURCH_OVERSHOOT_DEG)
                    arm.send({**strike, 6: clamp_deg(6, base)}, LURCH_MS)
                    time.sleep(LURCH_MS / 1000)
                    arm.send({5: POSE_POINT_DEG[5]}, SETTLE_MS)
                    coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)
                    continue

            # WRITHE: independent slow oscillators so nothing ever repeats.
            arm.send(writhe_deg(now, base, coiled=coil != "out"), int(TICK * 1000) + 80)
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nreleasing...")
        try:
            arm.send(POSE_REST_DEG, 2500)
            time.sleep(2.7)
            arm.relax()  # rest is a POSE, not a rest: unload or it holds all night
        except Exception as err:  # noqa: BLE001
            print(f"arm: could not park it ({err}); power-cycle it to relax")
        if eyes is not None:
            eyes.close()


if __name__ == "__main__":
    main()
