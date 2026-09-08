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
  4. LOCK   — the halloween_tracker package finds the person in the webcam
              frame and works out their bearing from the BASE PIVOT (the
              camera can sit anywhere: see CAMERA below). The base snaps to
              them and follows; when nobody has moved for LOST_AFTER_S the
              sweep resumes from wherever the base is.

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
_venv_py = _os.path.join(_venv_dir, "bin", "python")
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
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

# ---- the hunt -------------------------------------------------------- #
SWEEP_LO_DEG, SWEEP_HI_DEG = -53.0, 53.0  # base range of the search
SWEEP_PERIOD = 14.0                       # seconds per full left-right-left pass
TICK = 0.22                               # seconds between command packets

# ---- the writhe (degrees of travel around the pose) ------------------ #
GROPE_DEPTH = 22.0   # gripper flex
ROLL_DEPTH = 14.0    # wrist roll writhe
NOD_DEPTH = 11.0     # wrist bend searching nods

# ---- the twitch ------------------------------------------------------ #
FREEZE_CHANCE = 0.012   # per tick: freeze mid-sweep...
FREEZE_S = (0.8, 2.2)   # ...for this long
SNAP_CHANCE = 0.010     # per tick: sudden fast snap to a new heading
SNAP_MS = 280           # how fast the snap lands (small ms = violent)

# ---- the eyes (--eyes) ----------------------------------------------- #
# Where the camera sits, measured from the BASE PIVOT, looking down from
# above. +x = the direction the hand points at base 0 deg, +y = LEFT of
# that, yaw = which way the lens points relative to +x (left = positive).
# A single camera gives bearing but not range, so the offset only bites
# once range is estimated from the person's height in the frame; with a
# zero offset it does not matter at all. Dial these in with:
#   python -m halloween_tracker.preview --hfov .. --cam-x .. --cam-y .. --cam-yaw ..
CAMERA = dict(x_m=0.0, y_m=0.0, yaw_deg=0.0, hfov_deg=60.0, mirrored=False)
PERSON_HEIGHT_M = 1.7
VIDEO_SRC = "0"
CAPTURE_SIZE = (1280, 720)
# "background": static camera, learns the empty scene, anything that differs is the person (default, cheap)
# "motion": legacy frame differencing, only sees movement · "face": Haar, works up close · "person": HOG, whole bodies far away
DETECTOR = "background"
DETECT_ROI = (0.0, 1.0)  # motion detector: fraction of the frame rows to watch (top, bottom)
BASE_SIGN = +1.0        # +1 if POSITIVE servo-6 degrees turn the base LEFT; -1 if right. Verify on the arm.
LOCK_SMOOTH = 4         # readings averaged while locked (at TICK rate; small = twitchy)
TRACK_COAST_S = 1.5     # a track survives this long unconfirmed, coasting on its last motion
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
        else:
            self._arm.set_position(list(units.items()), duration_ms=dur_ms)


class DryBackend:
    """No arm: print the base heading so the eyes can be tested anywhere."""

    def send(self, moves_deg: dict[int, float], dur_ms: int) -> None:
        if 6 in moves_deg:
            print(f"  base -> {moves_deg[6]:+6.1f} deg over {dur_ms} ms")


class Eyes:
    """Webcam -> bearing of the victim from the base pivot, in centered
    degrees for servo 6 (sign applied). A background thread keeps the
    newest frame so the hunt loop never reads a stale one. Detections go
    through halloween_tracker's Tracker, so the victim is followed, not
    rediscovered, frame to frame."""

    def __init__(self, video_src: str, detector: str, show: bool = False) -> None:
        import cv2
        from halloween_tracker import CameraPose, Locator, Smoother, make_tracked

        self.kind = detector
        self._show = show

        try:
            src: int | str = int(video_src)
        except ValueError:
            src = video_src
        self._cv2 = cv2
        self._cam = cv2.VideoCapture(src)
        if not self._cam.isOpened():
            raise SystemExit(f"could not open camera {video_src!r}")
        self._cam.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_SIZE[0])
        self._cam.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_SIZE[1])
        # Tracker on top of the detector: a person, once found, is followed
        # across frames (coasting on their last motion when the detector
        # blinks) instead of being rediscovered from scratch every tick.
        self._det = make_tracked(detector, {"motion": {"roi": DETECT_ROI}}, coast_s=TRACK_COAST_S)
        self._loc = Locator(CameraPose(**CAMERA), person_height_m=PERSON_HEIGHT_M)
        self._smooth = Smoother(window=LOCK_SMOOTH)
        self._frame = None
        self._ended = False
        self._lock = threading.Lock()
        # A video file is played back in real time so the hunt sees it as
        # the camera would; a live camera is read as fast as it delivers.
        fps = self._cam.get(cv2.CAP_PROP_FPS) if isinstance(src, str) else 0.0
        self._pace = 1.0 / fps if fps and fps > 0 else 0.0
        threading.Thread(target=self._pump, daemon=True).start()

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
        deg = BASE_SIGN * t.bearing_deg
        if first:
            self._smooth.reset(deg)  # snap straight there, no lag from the old average
        return self._smooth.push(deg), t

    def _draw(self, d, t, ms: float) -> None:  # noqa: ANN001
        from halloween_tracker.preview import draw

        view = self._det.last_frame.copy()
        draw(view, d, t, self._det.roi_y0, ms, self.kind)
        self._cv2.imshow("gloom eyes", view)
        if (self._cv2.waitKey(1) & 0xFF) == 27:
            raise KeyboardInterrupt

    def close(self) -> None:
        self._cam.release()
        if self._show:
            self._cv2.destroyAllWindows()


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
    p.add_argument("--detector", default=DETECTOR, choices=("background", "motion", "face", "person"),
                   help="background subtraction (default), frame differencing, Haar face, or HOG person")
    p.add_argument("--show", action="store_true", help="open a window showing what the eyes see")
    a = p.parse_args()

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
                        arm.send({6: base}, SNAP_MS)
                        time.sleep(SNAP_MS / 1000 + 0.1)
                        continue
                    last_seen = now
                elif locked and now - last_seen > LOST_AFTER_S:
                    print("lost it... resuming the hunt")
                    locked = False
                    heading_offset = rephase(now, base)

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

            # WRITHE: independent slow oscillators so nothing ever repeats.
            moves = {
                6: clamp_deg(6, base),
                1: clamp_deg(1, POSE_POINT_DEG[1] + GROPE_DEPTH * math.sin(1.9 * now + 1.0)
                             + 6.0 * math.sin(6.3 * now)),
                2: clamp_deg(2, POSE_POINT_DEG[2] + ROLL_DEPTH * math.sin(0.7 * now)),
                3: clamp_deg(3, POSE_POINT_DEG[3] + NOD_DEPTH * math.sin(1.3 * now + 2.1)),
            }
            arm.send(moves, int(TICK * 1000) + 80)
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nreleasing...")
        arm.send(POSE_REST_DEG, 2500)
        time.sleep(2.7)
        if eyes is not None:
            eyes.close()


if __name__ == "__main__":
    main()
