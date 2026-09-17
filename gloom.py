#!/usr/bin/env python3
"""Gloom hand mode (Tears of the Kingdom): the arm points toward you and
sweeps left <-> right, searching for victims. With eyes, it stops
searching when it finds one.

    ./gloom.py                 # wireless (BLE), blind hunt
    ./gloom.py --usb           # wired
    ./gloom.py --eyes          # dormant until a face shows up, then hunts
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
  0. SLACK  — with --eyes the arm spends most of its life with every servo
              switched off: no current, no holding torque, limp enough to
              move by hand. It searches for SEARCH_S at startup and after
              losing anyone; come up empty and it stands upright and cuts
              the power. A face held for WAKE_AFTER_S brings it back.
              Everything below only happens while it is awake.
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
import datetime as _dt
import math
import random
import sys
import threading
import time

from angles import clamp_deg, servo_units, units_to_deg

# ---- the posture (TUNE THESE FIRST, in degrees) ---------------------- #
POSE_POINT_DEG = {
    5: 36.0,   # shoulder: leaned forward
    # The elbow was at -48, which left the hand hanging about 20 degrees below
    # horizontal at the end of a lurch: overextended to look at, and the worst
    # case for the shoulder, which is why it sometimes tripped the alarm right
    # there. Raising it brings the hand flat AND pulls the mass inboard, so it
    # is both the cosmetic fix and the load fix. (Negative bends the forearm
    # down; less negative raises it.)
    4: -28.0,  # elbow: forearm level, hand straight out
    3: 12.0,   # wrist bend: aimed at the viewer
    2: 0.0,    # wrist roll: knuckles up
    1: 12.0,   # gripper: half-open, ready to grab
}
POSE_REST_DEG = {sid: 0.0 for sid in range(1, 7)}
# Where it stands when the power is cut. Vertical and balanced over the base,
# measured on the real arm, so that letting go barely moves it. This is a
# place to STOP, not a pose to hold: a moment after reaching it every servo
# is switched off and the arm can be pushed around by hand.
POSE_SLACK_DEG = {5: -5.0, 4: 0.0, 3: 0.0, 2: 0.0, 1: 0.0, 6: 0.0}
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
# The heavy joints get a slow sway of their own, so the arm breathes while it
# holds you rather than locking rigid from the elbow in. Small and slow on
# purpose: these two carry the weight, and every degree of travel here costs
# far more current than the same degree at the wrist. The rates share no
# common factor with each other or with the wrist, so the three never fall
# into step and the motion never looks like a loop.
ELBOW_DEPTH, ELBOW_RATE = 5.0, 1.15     # forearm drifts up and down
SHOULDER_DEPTH, SHOULDER_RATE = 3.5, 0.83  # the whole arm sways with it

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
# The overshoot that makes the lunge land hard used to be on the SHOULDER,
# driving the heaviest joint 10 degrees PAST a pose that is already maximum
# cantilever, at the end of a fast move, and then reversing it. That is the
# worst instant in the whole routine and it is exactly when the alarm sounds.
# The WRIST carries only the hand, so it can snap for free and still reads as
# the lunge landing. Put it back on servo 5 if you want the old violence and
# have the power budget for it.
# HOW FAR THE STRIKE REACHES, as a fraction of the way from the coil to the
# full point pose. The alarm has always fired at the END of a lurch, which is
# the instant the arm arrives at its longest and has to hold there: maximum
# moment at the shoulder, right after a fast move. Landing short of full
# extension cuts that holding torque and is the one dial that acts on exactly
# the moment that fails. 1.0 is the old behaviour.
LURCH_REACH = 0.90
LURCH_OVERSHOOT_JOINT = 3    # 3 = wrist (cheap), 5 = shoulder (what used to sing)
LURCH_OVERSHOOT_DEG = 14.0   # past the point pose as the lunge lands...
SETTLE_MS = 450              # ...then settles back over this long
GREET_HOLD_S = 3.0           # the first sighting: rise into the coil, writhe this long,
                             # then strike. An entrance, rather than just switching on.
SHOULDER_SETTLE = False      # only needed when the overshoot is on the shoulder

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
# hfov_deg is the lens's HORIZONTAL field of view. Take it from the webcam's
# spec sheet — wide-angle ones are commonly 90 to 120, and leaving it at 60
# does not break tracking (the frame is mapped onto the sweep either way) but
# it does make every reported distance wrong. --hfov overrides it for a run.
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
# Averaging readings costs lag directly: an N-sample mean delays by about
# (N-1)/2 ticks. Worse, the smoother rejects readings that sit far from the
# window mean, and a person moving quickly looks exactly like that, so it was
# throwing away the very samples that carry the movement. YuNet's face boxes
# are clean enough not to need much of either.
LOCK_SMOOTH = 2         # readings averaged while locked (at TICK rate; small = twitchy)
# ASLEEP 99% OF THE TIME. This thing lives in a corner of a house, so the
# resting state is genuinely off: parked upright and completely unpowered,
# drawing nothing, silent, cool, and out of the supply's way.
#
#   start  -> SEARCH for SEARCH_S. A face at any point starts TRACKING.
#   TRACK  -> follow. Losing the face drops back to SEARCH after LOST_AFTER_S.
#   SEARCH -> SEARCH_S with nobody seen and it parks upright and goes slack.
#   SLACK  -> every servo switched OFF. No current, no holding torque, the
#             arm limp enough to reposition by hand. A face held for
#             WAKE_AFTER_S powers it back up into TRACK.
#
# Every sighting restarts the SEARCH_S clock, so a room with people in it
# keeps the arm awake and an empty one lets it go within SEARCH_S.
SEARCH_S = 30.0         # seconds hunting with nobody in sight before it lets go
WAKE_AFTER_S = 2.0      # continuous face needed to wake it (a debounce; 0 to disable)
WAKE_MS = 2200          # how gently it rises — it has been hanging slack
# RUNNING FOR DAYS. Two things accumulate while the arm is busy and neither
# shows up in a short test: heat in the servos that are holding weight, and
# whatever it is in the supply that sags. So activity is rationed. However
# popular the room is, the arm takes a break after ACTIVE_MAX_S and will not
# be woken again for COOLDOWN_S. The break is the same slack state as an
# empty room: upright, unpowered, cooling.
ACTIVE_MAX_S = 240.0    # longest unbroken stretch of being awake
COOLDOWN_S = 90.0       # enforced slack afterwards, faces or no faces
# Wired only: the controller can report its supply. Under this, stop striking;
# stay under it and go slack before the servos raise the alarm themselves.
BROWNOUT_V = 7.0
BASE_STEP_DEG = 3.0     # re-aim the base only once the victim has moved this far...
BASE_EVERY_S = 0.50     # ...and never more often than this. Each command restarts the
                        # servo's move, so fewer and longer beats more and finer.
BASE_OVERLAP = 1.7      # the base's travel time as a multiple of that interval. Over 1.0
                        # the move is always still running when the next one lands, so the
                        # base never comes to rest between commands — a stop and a restart
                        # is precisely the twitch we are trying to remove. The writhe does
                        # not need this: its waypoints ARE the sine's turning points, where
                        # the motion is genuinely meant to pause.
VOLTS_EVERY_S = 1.0     # how often to ask (each read is a USB round trip)
UNREACHABLE_S = 8.0     # unresponsive this long and we call it: the servos have latched
PARK_MS = 2500          # and how gently it goes back down
SLACK_REASSERT_S = 60.0 # re-send the unload this often while asleep. BLE writes are
                        # never acknowledged, so this is the cheap insurance against
                        # the one packet that matters going missing.
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
# The base is a YAW joint: it turns about a vertical axis, so gravity never
# opposes it and it only has to beat inertia and friction. It is the cheapest
# joint on the arm to move, and throttling it hard to protect the supply was
# aiming at the wrong target — the shoulder and elbow are what lift weight.
# 75 deg/s meant 1.2 s to cross the sweep, which is most of the lag you feel.
TRACK_SLEW_DPS = 220.0  # how fast the base may follow, degrees per second
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
        self.released = False   # True once the current is off and, on BLE, the link dropped
        self._last_errors = 0   # write-error count at the previous health check
        self._last_send = None  # when the previous packet went out
        self.interval_ms = TICK * 1000.0  # measured spacing between packets
        self._sent_units: dict[int, int] = {}  # last value actually commanded per servo

    def delivered_hz(self) -> float:
        """Packets per second the arm is ACTUALLY hearing — the slower of what
        the loop offers and what the link carries."""
        link = getattr(self._arm, "write_interval_ms", 0.0) if self._servo is None else 0.0
        gap = max(self.interval_ms, link)
        return (1000.0 / gap) if gap > 0 else (1.0 / TICK)

    def stream(self, moves_deg: dict[int, float]) -> None:
        """Send a posture as part of the continuous stream.

        Two things keep this smooth, and the loop cannot know either of them.

        The MOVE DURATION has to outlast the gap to the next packet. A servo
        told to travel over 300 ms, when the next command is 440 ms away,
        arrives early and then sits perfectly still for 140 ms. Move, stop,
        move, stop — which is exactly what a tremor looks like, and why the
        board's light blinks once per twitch. So the duration is measured
        from the real spacing, with margin, and the servo is still moving
        when the next target lands.

        And a joint whose target has not MOVED is left out. Re-commanding a
        shoulder that is holding a load, several times a second, makes it
        hunt around its setpoint for no reason, and every servo in the packet
        costs bytes on a link that is already the limiting factor.
        """
        now = time.monotonic()
        if self._last_send is not None:
            gap = (now - self._last_send) * 1000.0
            if gap < 2000:  # ignore the pause either side of a sleep
                self.interval_ms = 0.7 * self.interval_ms + 0.3 * gap
        self._last_send = now
        # Whichever is slower: how often the loop offers a packet, or how
        # often the link actually delivers one. A packet superseded before it
        # went out never reached the arm, so the loop's cadence alone would
        # understate the real gap and we would be back to arriving early.
        link = getattr(self._arm, "write_interval_ms", 0.0) if self._servo is None else 0.0
        # 1.6x: comfortably past the next packet even when the link stutters
        dur = int(max(TICK * 1000.0, self.interval_ms * 1.6, link * 1.6))

        units = {sid: servo_units(sid, d) for sid, d in moves_deg.items()}
        fresh = {sid: u for sid, u in units.items()
                 if abs(u - self._sent_units.get(sid, -9999)) >= 2}
        if not fresh:
            return
        self._sent_units.update(fresh)
        self.send({sid: units_to_deg(u) for sid, u in fresh.items()}, dur, _measured=True)

    def send(self, moves_deg: dict[int, float], dur_ms: int, _measured: bool = False) -> None:
        self.released = False
        if not _measured:
            self._sent_units.clear()  # a one-off move invalidates what we think it holds
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

    def voltage(self) -> float | None:
        """Supply volts, or None when we cannot know.

        Bluetooth is write-only here, so wireless runs are blind to the one
        number that predicts the alarm. That alone is a reason to deploy on
        the cable."""
        if self._servo is None:
            return None
        try:
            return self._arm.getBatteryVoltage()
        except Exception:  # noqa: BLE001 - a dropped read is not an emergency
            return None

    def health(self) -> tuple[bool, str]:
        """Is the arm still listening? Returns (ok, why not).

        When a servo raises its alarm it latches, stops obeying, and takes the
        board's radio down with it — and nothing in a stream of write-only
        position commands notices. Wired, a silent controller is the tell.
        Wireless, it is a link that will not come back and writes that keep
        failing.
        """
        if self._servo is not None:
            return (self.voltage() is not None), "no reply from the controller"
        errors = getattr(self._arm, "write_errors", 0)
        growing, self._last_errors = errors > self._last_errors, errors
        if not self._arm.connected:
            return False, "Bluetooth link down and not coming back"
        if growing:
            return False, "writes are failing"
        return True, ""

    def prewarm(self) -> None:
        """Start reconnecting in the background.

        Called the instant a face appears while asleep, so the link is up by
        the time the wake threshold passes rather than costing seconds after
        it. Failures are silent: the wake's first send retries anyway.
        """
        if self._servo is not None or not self.released:
            return

        def go() -> None:
            try:
                self._arm.reconnect()
            except Exception:  # noqa: BLE001 - the wake will try again
                pass

        threading.Thread(target=go, daemon=True).start()

    def relax(self, quiet: bool = False, drop_link: bool = True) -> None:
        """Take the current off every servo, so the arm is limp and can be
        moved by hand.

        Over Bluetooth this also DROPS THE LINK. The unload command alone
        was observed not to release this board — the arm only went limp when
        the process exited and the connection died with it. So the link is
        what actually does it, and the next command reconnects on its own.
        """
        self.released = True
        self._sent_units.clear()
        try:
            if self._servo is not None:
                self._arm.servoOff()
                return
            self._arm.unload()
            if drop_link:
                self._arm.disconnect()
        except Exception as err:  # noqa: BLE001 - we are shutting down anyway
            if not quiet:
                print(f"arm: could not unload ({err})")


class DryBackend:
    """No arm: print what would have been sent, so the eyes, the strike and
    the sleep cycle can all be exercised anywhere."""

    released = False
    interval_ms = TICK * 1000.0

    def delivered_hz(self) -> float:
        return 1.0 / TICK

    def voltage(self) -> float | None:
        return None

    def health(self) -> tuple[bool, str]:
        return True, ""

    def prewarm(self) -> None:
        pass

    def stream(self, moves_deg: dict[int, float]) -> None:
        self.send(moves_deg, int(TICK * 1000))

    def relax(self, quiet: bool = False, drop_link: bool = True) -> None:
        self.released = True
        if not quiet:
            print("  (servos off, link dropped, limp)")

    def send(self, moves_deg: dict[int, float], dur_ms: int, _measured: bool = False) -> None:
        self.released = False
        if {3, 4, 5} & moves_deg.keys():
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


class Log:
    """Append-only flight recorder.

    The failure we care about takes the power with it, so nothing that is
    still sitting in a buffer survives to be read. Every line is flushed as
    it is written, and the file is opened in append mode, so days of runs
    accumulate and the last line before a death is always on disk.

    One line per event, tab separated: a wall-clock stamp, seconds since the
    run began, a kind, then key=value fields.
    """

    def __init__(self, path: str | None) -> None:
        self.fh = None
        if not path:
            return
        self.fh = open(path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 - lives for the run
        self.fh.write(f"# ---- run started {_dt.datetime.now().isoformat(timespec='seconds')}\n")

    def event(self, now: float, kind: str, **fields: object) -> None:
        if self.fh is None:
            return
        bits = " ".join(f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}"
                        for k, v in fields.items())
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self.fh.write(f"{stamp}\t{now:9.1f}\t{kind}\t{bits}\n")

    def close(self) -> None:
        if self.fh is not None:
            self.fh.write("# ---- run ended cleanly\n")
            self.fh.close()
            self.fh = None


class Keys:
    """Non-blocking keyboard, so Esc can stop the hunt with no window open.

    Only the preview window could take a keypress before, which left the
    blind hunt reachable by Ctrl-C alone. This puts the terminal in cbreak
    so keys arrive without Enter, and restores it on the way out. Ctrl-C
    keeps working throughout: cbreak leaves signal generation enabled,
    unlike raw mode. A terminal that is not a tty (piped input, a service)
    simply reports no keys rather than failing.
    """

    def __init__(self) -> None:
        self._fd = None
        self._saved = None
        self._termios = None
        if not sys.stdin.isatty():
            return
        if sys.platform == "win32":
            self._fd = "win32"
            return
        import termios
        import tty

        self._termios = termios
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def pressed(self) -> str:
        """Everything typed since the last call, without blocking."""
        if self._fd is None:
            return ""
        if self._fd == "win32":
            import msvcrt

            out = ""
            while msvcrt.kbhit():
                out += msvcrt.getwch()
            return out
        import select

        out = ""
        while select.select([sys.stdin], [], [], 0)[0]:
            out += sys.stdin.read(1)
        return out

    def quit_requested(self) -> bool:
        """Esc or q. An arrow key also starts with Esc but carries a '['
        right behind it, so ignore anything that looks like a sequence."""
        chunk = self.pressed()
        if not chunk:
            return False
        if "q" in chunk.lower():
            return True
        return "\x1b" in chunk and "[" not in chunk

    def close(self) -> None:
        if self._saved is not None and self._termios is not None:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
            self._saved = None


class Animator:
    """Eases the arm's posture toward a target, a tick at a time.

    The coil and the lurch used to be a command followed by a sleep, which
    is exactly why they played like cut scenes: nothing else could happen
    until they finished, so anyone moving during one was simply not
    followed. Here each joint gets its own leg of travel with a start time
    and a duration, and pose_at() only reports where things are right now.
    The loop never blocks, so the base keeps tracking straight through a
    coil, a strike and a recovery.
    """

    def __init__(self, pose: dict[int, float]) -> None:
        self.cur = {sid: float(v) for sid, v in pose.items()}
        self._legs: dict[int, tuple[float, float, float, float]] = {}

    def to(self, target: dict[int, float], dur_ms: float, now: float, delay_s: float = 0.0) -> None:
        """Start moving these joints toward `target`. Later calls override
        earlier ones from wherever the joint has actually reached, so a
        change of mind mid-move blends instead of snapping."""
        for sid, v in target.items():
            self._legs[sid] = (self.cur.get(sid, float(v)), float(v), now + delay_s, dur_ms / 1000.0)

    def pose_at(self, now: float) -> dict[int, float]:
        for sid, (a, b, t0, dur) in self._legs.items():
            if now < t0:
                continue
            f = 1.0 if dur <= 0 else min(1.0, (now - t0) / dur)
            f = f * f * (3.0 - 2.0 * f)  # smoothstep: no corner at either end
            self.cur[sid] = a + (b - a) * f
        return dict(self.cur)

    def settled(self, now: float) -> bool:
        return all(now >= t0 + dur for _, _, t0, dur in self._legs.values())


class Waypoints:
    """Tell each joint where to go and how long to take, then LEAVE IT ALONE.

    Measured on the arm: one four-second command produces a perfectly smooth
    sweep, and the same arc sent as a stream of small steps gets rougher the
    more steps you use. These servos do not blend a new command into the move
    they are already making — they restart from wherever they are, so every
    packet is a fresh little acceleration. Streaming a pose several times a
    second was therefore not refining the motion, it was chopping it up, and
    the board's light blinking in time with the shudder was the arm reporting
    exactly that, one flash per interruption.

    So each joint is given a destination and a duration and then left to get
    on with it. Nothing is sent to a joint that is still usefully travelling.
    Joints that happen to come due together are batched into one packet.
    """

    def __init__(self) -> None:
        self.until: dict[int, float] = {}    # when each joint's current move ends
        self.target: dict[int, float] = {}   # where it was last told to go
        # duration -> {servo: degrees}. The protocol carries ONE duration per
        # packet, so joints travelling for different lengths of time cannot
        # share one: batching them would hand the base the writhe's four
        # seconds and vice versa.
        self._batch: dict[int, dict[int, float]] = {}

    def free(self, sid: int, now: float) -> bool:
        return now >= self.until.get(sid, -1e9)

    def go(self, sid: int, deg: float, dur_ms: float, now: float) -> None:
        """Queue a destination for this joint, to leave with the next flush."""
        self.target[sid] = deg
        self.until[sid] = now + dur_ms / 1000.0
        # round to 50 ms so joints that want near-identical times still share
        # a packet, without anyone's travel time being materially altered
        slot = max(50, int(round(dur_ms / 50.0) * 50))
        self._batch.setdefault(slot, {})[sid] = deg

    def flush(self, arm: "Backend") -> int:
        """Send whatever came due, one packet per travel time. Returns the
        number of packets sent."""
        sent = 0
        for dur, moves in sorted(self._batch.items()):
            arm.send(moves, dur)
            sent += 1
        self._batch = {}
        return sent

    def forget(self) -> None:
        self.until.clear()
        self.target.clear()
        self._batch = {}


def next_extreme(rate: float, phase: float, t: float) -> tuple[float, float]:
    """When a sine next reaches a peak or trough, and which one.

    Waypointing at the extremes is the fewest commands that still describes
    the motion: two per cycle, and the servo draws the line between them.
    Anything finer only adds interruptions, which is the thing that hurts.
    """
    k = math.ceil((rate * t + phase - math.pi / 2) / math.pi)
    when = (math.pi / 2 + k * math.pi - phase) / rate
    return when, (1.0 if k % 2 == 0 else -1.0)


#: Each writhe joint as (servo, depth, rate, phase). The gripper's fast
#: tremor is gone: at two commands per cycle there is nothing to carry it,
#: and it was never renderable at any rate this link supports.
WRITHE_WAVES = (
    (1, GROPE_DEPTH, GROPE_RATE, 1.0),
    (2, ROLL_DEPTH, ROLL_RATE, 0.0),
    (3, NOD_DEPTH, NOD_RATE, 2.1),
    (4, ELBOW_DEPTH, ELBOW_RATE, 0.7),
    (5, SHOULDER_DEPTH, SHOULDER_RATE, 3.4),
)

#: Joints the strike also drives. Their writhe has to oscillate around
#: whatever posture the strike has put them in, not around a fixed pose.
POSED_JOINTS = (3, 4, 5)

#: Command updates per second the writhe was tuned for. Everything in
#: WRITHE_TERMS assumes roughly eight of them per cycle; fewer and a sine
#: arrives as a staircase.
DESIGN_HZ = 1.0 / TICK


def motion_scale(delivered_hz: float) -> float:
    """How much to slow the writhe down when the link cannot keep up.

    A Bluetooth link that only carries a packet or so a second cannot render
    a half-hertz tremor: the arm receives two or three targets per cycle and
    lunges between them, which reads as a regular shudder at exactly the
    packet rate. Rather than send motion that cannot arrive, slow the whole
    creature by the same factor. It squirms more languidly, which for this
    thing is no loss at all, and every command becomes a small step instead
    of a jump.
    """
    return max(0.2, min(1.0, delivered_hz / DESIGN_HZ))


def body_pose(now: float, base: float, home: dict[int, float],
              nod_scale: float = 1.0, amp: float = 1.0) -> dict[int, float]:
    """Everything the arm is doing this instant, as one posture.

    `home` is where the animator has the shoulder, elbow and wrist; the
    writhe oscillates on top of it rather than around a fixed pose, so the
    creature keeps squirming while it coils, strikes and recovers. One dict
    means one packet, so no joint waits its turn behind another.
    """
    return {
        6: clamp_deg(6, base),
        5: clamp_deg(5, home[5]),
        4: clamp_deg(4, home[4]),
        3: clamp_deg(3, home[3] + amp * nod_scale * NOD_DEPTH * math.sin(NOD_RATE * now + 2.1)),
        2: clamp_deg(2, POSE_POINT_DEG[2] + amp * ROLL_DEPTH * math.sin(ROLL_RATE * now)),
        1: clamp_deg(1, POSE_POINT_DEG[1] + amp * (
            GROPE_DEPTH * math.sin(GROPE_RATE * now + 1.0)
            + TREMOR_DEPTH * math.sin(TREMOR_RATE * now))),
    }


def reach_pose(frac: float = LURCH_REACH) -> dict[int, float]:
    """How far out the arm actually extends: `frac` of the way from the coil
    to the full point pose. This is what it lands in and then HOLDS, so it
    sets the standing torque on the shoulder, not just the look of the lunge."""
    return {
        sid: clamp_deg(sid, POSE_COIL_DEG[sid] + frac * (POSE_POINT_DEG[sid] - POSE_COIL_DEG[sid]))
        for sid in (5, 4, 3)
    }


def lurch_pose() -> dict[int, float]:
    """Where the strike lands: the reach pose, with one joint driven past it
    so the lunge arrives with a snap instead of a glide."""
    pose = reach_pose()
    sid = LURCH_OVERSHOOT_JOINT
    pose[sid] = clamp_deg(sid, pose[sid] + LURCH_OVERSHOOT_DEG)
    return pose


#: Every writhe oscillator, as (servo, label, depth_deg, rate_rad_s). Used by
#: test_motion.py to police the render and current budgets above.
WRITHE_TERMS = [
    (1, "grope", GROPE_DEPTH, GROPE_RATE),
    (2, "roll", ROLL_DEPTH, ROLL_RATE),
    (3, "nod", NOD_DEPTH, NOD_RATE),
    (4, "elbow sway", ELBOW_DEPTH, ELBOW_RATE),
    (5, "shoulder sway", SHOULDER_DEPTH, SHOULDER_RATE),
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
    p.add_argument("--tick", type=float, metavar="SECONDS",
                   help=f"seconds between command packets (default {TICK}); lower is smoother "
                        f"but sends more, so watch the supply")
    p.add_argument("--hfov", type=float, metavar="DEG",
                   help="camera horizontal field of view; wide-angle webcams are often 90-120")
    p.add_argument("--mirrored", action="store_true", help="the camera image is left-right flipped")
    p.add_argument("--log", default="gloom.log", metavar="FILE",
                   help="append a flight recorder to this file (default gloom.log; '' to disable)")
    p.add_argument("--flip", action="store_true",
                   help="invert BASE_SIGN for this run — use it to settle which way servo 6 turns")
    a = p.parse_args()

    if a.tick:
        globals()["TICK"] = a.tick
        print(f"eyes: {1 / a.tick:.1f} command packets per second")
    if a.hfov:
        CAMERA["hfov_deg"] = a.hfov
        print(f"eyes: camera field of view set to {a.hfov:.0f} deg")
    if a.mirrored:
        CAMERA["mirrored"] = True
    if a.flip:
        globals()["BASE_SIGN"] = -BASE_SIGN
        print(f"eyes: BASE_SIGN flipped to {BASE_SIGN:+.0f} for this run")
    eyes = Eyes(a.video_src, a.detector, show=a.show) if a.eyes else None
    arm = DryBackend() if a.dry else Backend(a.usb)
    base = (SWEEP_LO_DEG + SWEEP_HI_DEG) / 2

    # Always begin by looking: assume the pose and search. With eyes, a fruitless
    # search times out into slack; without them the blind hunt runs forever.
    print("assuming the pose...")
    arm.send({**POSE_POINT_DEG, **reach_pose(), 6: base}, 2500)
    time.sleep(2.7)
    awake = True
    keys = Keys()
    if eyes is None:
        print("hunting. Esc or q to stop (Ctrl-C works too).")
    else:
        print(f"searching ({eyes.kind}). Nothing within {SEARCH_S:.0f}s and it goes slack. "
              f"Esc or q to stop.")

    t0 = time.monotonic()
    log = Log(a.log)
    log.event(0.0, "start", eyes=(eyes.kind if eyes else "none"),
              link=("usb" if a.usb else ("dry" if a.dry else "ble")),
              search_s=SEARCH_S, active_max_s=ACTIVE_MAX_S)
    wp = Waypoints()
    heading_offset = 0.0  # phase shift accumulated by snaps
    frozen_until = 0.0
    last_seen = 0.0       # when the eyes last saw someone; also the SEARCH_S clock
    seen_since = None     # when the CURRENT unbroken run of sightings began
    locked = False
    greeted = False       # has it made its entrance since it last woke?
    warming = False       # a reconnect already started for this sighting
    last_report = -1e9    # when we last showed the tracking on stdout
    last_unload = -1e9    # when the unload was last re-sent while slack
    slept_at = 0.0        # when it last went slack, for the heartbeat
    coil = "out"          # strike phase: out / coiling / coiled / settling
    coil_at = 0.0         # when the next phase is due
    hold_override = None  # replaces the random coiled hold, for the entrance
    awake_since = 0.0     # start of the current unbroken stretch of being awake
    cooldown_until = 0.0  # refuse to wake before this, however many faces turn up
    volts = None          # last supply reading, wired runs only
    volts_at = -1e9
    low_since = None      # when the supply first went under BROWNOUT_V
    last_sample = -1e9    # when the recorder last took a routine reading
    slow_look = 0.0       # worst camera+detect time since the last log line, ms
    slow_send = 0.0       # worst time spent handing a packet to the arm, ms
    late = 0              # ticks that overran their slot
    last_base_cmd = -1e9  # when the base was last given a new heading
    packets = 0           # commands actually sent since the last log line
    health_at = -1e9      # when the arm was last asked whether it is alive
    unwell_since = None   # when it stopped answering
    declared_dead = False # so the diagnosis is printed once, not every tick

    def pace(due: float) -> None:
        """Sleep until the tick is actually due.

        Sleeping a whole TICK after the work made the loop period the tick
        PLUS however long the work took, which over Bluetooth meant fewer and
        unevenly spaced commands — the jerkiness. Now the cadence is the tick,
        and slow work eats its own slack rather than everyone else's.
        """
        left = due - (time.monotonic() - t0)
        if left > 0:
            time.sleep(left)

    def go_slack(now: float, reason: str, cooldown: float = 0.0) -> float:
        """Stand upright, cut the power, and say why. Returns the time before
        which waking is refused."""
        print(f"\n{reason} — standing up and cutting power.")
        log.event(now, "slack", reason=reason, cooldown_s=cooldown,
                  awake_for=now - awake_since, volts=volts if volts else "?")
        try:
            arm.send(POSE_SLACK_DEG, PARK_MS)
            time.sleep(PARK_MS / 1000 + 0.2)
        except Exception as err:  # noqa: BLE001 - park is best-effort; the unload matters
            log.event(now, "park_failed", err=type(err).__name__)
        arm.relax()
        wp.forget()
        return now + cooldown

    try:
        while True:
            now = time.monotonic() - t0
            tick_due = now + TICK
            if keys.quit_requested():
                print("\nstopping.")
                log.event(now, "quit", by="key")
                break

            # SUPPLY WATCH. Wired only, and the whole argument for running on
            # the cable: the alarm is preceded by the voltage falling, so this
            # is the one signal that lets us back off BEFORE the servos do it
            # for us — at which point they latch and only a power cycle helps.
            if now - volts_at >= VOLTS_EVERY_S:
                volts_at = now
                v = arm.voltage()
                if v is not None:
                    volts = v
                    if v < BROWNOUT_V:
                        if low_since is None:
                            low_since = now
                            print(f"\n!! supply {v:.2f} V, under {BROWNOUT_V:.1f} — easing off.")
                            log.event(now, "brownout", volts=v, coil=coil, awake=awake)
                    elif low_since is not None:
                        log.event(now, "recovered", volts=v, low_for=now - low_since)
                        low_since = None

            # IS THE ARM STILL THERE? A latched alarm stops the servos obeying
            # and takes the radio with it, and a stream of write-only position
            # commands sails on regardless, which is why this could run for
            # hours against an arm that had been dead since the first lurch.
            if now - health_at >= 2.0:
                health_at = now
                ok, why = arm.health()
                if ok:
                    if unwell_since is not None:
                        print(f"\narm responding again after {now - unwell_since:.0f}s.")
                        log.event(now, "arm_back", down_for=now - unwell_since)
                        unwell_since = None
                elif unwell_since is None:
                    unwell_since = now
                    log.event(now, "arm_quiet", why=why, coil=coil,
                              volts=(volts if volts else "?"))
                elif now - unwell_since > UNREACHABLE_S and not declared_dead:
                    declared_dead = True
                    print(f"\n!! THE ARM HAS STOPPED RESPONDING ({why}), {now - unwell_since:.0f}s now.")
                    print("   This is what the alarm looks like from here: the servos latch,")
                    print("   stop obeying, and drop the radio. Software cannot clear it —")
                    print("   the supply has to be cycled. Still watching, in case it returns.")
                    log.event(now, "arm_dead", why=why, since=unwell_since,
                              last_coil=coil, volts=(volts if volts else "?"))
                if ok:
                    declared_dead = False

            if now - last_sample >= 5.0:
                last_sample = now
                log.event(now, "tick",
                          state=("slack" if not awake else ("track" if locked else "search")),
                          coil=coil, base=base, volts=(volts if volts is not None else "?"),
                          awake_for=(now - awake_since if awake else 0.0),
                          look_ms=slow_look, send_ms=slow_send, late=late,
                          gap_ms=arm.interval_ms,
                          pkts_per_s=packets / 5.0)
                packets = 0
                slow_look = slow_send = 0.0
                late = 0

            if eyes is not None:
                if eyes.ended:
                    print("video ended")
                    break
                t_look = time.monotonic()
                seen = eyes.look(first=not locked)
                look_ms = (time.monotonic() - t_look) * 1000
                slow_look = max(slow_look, look_ms)
                if seen is None:
                    seen_since = None
                elif seen_since is None:
                    seen_since = now

                if not awake:
                    # --- SLACK: unpowered, just watching ------------------- #
                    held = 0.0 if seen_since is None else now - seen_since
                    # Start reconnecting the moment a face appears, so the link
                    # is up by the time the wake threshold passes instead of
                    # costing several seconds after it.
                    if held > 0 and not warming:
                        arm.prewarm()
                        warming = True
                    elif held == 0:
                        warming = False
                    if now < cooldown_until:
                        if held > 0 and now - last_report >= 5.0:
                            print(f"  resting — {cooldown_until - now:.0f}s before it will "
                                  f"wake, face or no face", end="\r", flush=True)
                            last_report = now
                        pace(tick_due)
                        continue
                    if held < WAKE_AFTER_S:
                        if not arm.released and now - last_unload >= SLACK_REASSERT_S:
                            arm.relax(quiet=True)
                            last_unload = now
                        if held > 0:
                            print(f"  asleep — face held {held:.1f}s of {WAKE_AFTER_S:.1f}s "
                                  f"needed to wake", end="\r", flush=True)
                            last_report = now
                        elif now - last_report >= 15.0:
                            print(f"  asleep {now - slept_at:.0f}s, servos off, nobody in sight")
                            last_report = now
                        pace(tick_due)
                        continue
                    # THE ENTRANCE: rise out of slack straight into the coil,
                    # writhe there for a beat, then strike. Not a power-on.
                    base = clamp_deg(6, seen[0])
                    print(f"\nsomething is there ({held:.1f}s of face) — waking up.")
                    wp.forget()  # it is wherever we left it hanging
                    for _sid, _deg in POSE_COIL_DEG.items():
                        wp.go(_sid, _deg, WAKE_MS, now)
                    awake, locked, greeted, warming = True, True, True, False
                    awake_since = now
                    log.event(now, "wake", held=held, base=base, volts=(volts if volts else "?"))
                    last_seen, last_report = now, now
                    coil, coil_at, hold_override = "coiling", now + WAKE_MS / 1000, GREET_HOLD_S
                    frozen_until = 0.0
                    continue

                # --- three ways to end up slack ---------------------------- #
                reason, cooldown = None, 0.0
                if now - last_seen > SEARCH_S:
                    reason = f"nothing for {SEARCH_S:.0f}s"
                elif now - awake_since > ACTIVE_MAX_S:
                    # However busy the room is, it stops. Heat and supply sag
                    # both build over minutes, and nothing else in this loop
                    # would ever choose to stop while people keep arriving.
                    reason, cooldown = f"awake {ACTIVE_MAX_S:.0f}s — taking a break", COOLDOWN_S
                elif low_since is not None and now - low_since > 3.0:
                    reason, cooldown = f"supply stayed under {BROWNOUT_V:.1f} V", COOLDOWN_S
                if reason:
                    cooldown_until = go_slack(now, reason, cooldown)
                    print("servos off — no current, no holding torque. Push it by hand.")
                    if cooldown:
                        print(f"  (resting {cooldown:.0f}s before it will wake again)")
                    awake, locked, greeted, coil = False, False, False, "out"
                    seen_since, last_unload, slept_at, last_report = None, now, now, now
                    low_since = None
                    continue

                if seen is not None:
                    base, t = seen
                    base = clamp_deg(6, base)
                    last_seen = now
                    if not locked:
                        locked = True
                        print(f"victim spotted: {t.cam_range_m:.1f} m at "
                              f"{t.cam_bearing_deg:+.1f} deg -> base {base:+.1f} deg")
                        if not greeted:
                            greeted = True
                            for _sid, _deg in POSE_COIL_DEG.items():
                                wp.go(_sid, _deg, COIL_MS, now)
                            coil = "coiling"
                            coil_at, hold_override = now + COIL_MS / 1000, GREET_HOLD_S
                        else:
                            coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)
                        last_report = now
                    elif now - last_report >= 1.5:  # show that it is still following
                        phase = coil if coil != "out" else "tracking"
                        print(f"  {phase}: face {t.cam_bearing_deg:+5.1f} deg at "
                              f"{t.cam_range_m:.1f} m -> base {base:+6.1f} deg")
                        last_report = now
                elif locked and now - last_seen > LOST_AFTER_S:
                    print(f"lost it — searching for {SEARCH_S - (now - last_seen):.0f}s more")
                    log.event(now, "lost", base=base)
                    locked = False
                    heading_offset = rephase(now, base)
                    if coil != "out":
                        for _sid, _deg in reach_pose().items():
                            wp.go(_sid, _deg, 1500, now)
                        coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)

            # FREEZE: it goes utterly still — but never stops watching you.
            # Stopping the writhe is the creepy part; stopping the tracking
            # just looked broken.
            if now < frozen_until:
                amp = 0.0
            else:
                amp = 1.0
                if random.random() < FREEZE_CHANCE:
                    frozen_until = now + random.uniform(*FREEZE_S)

            if not locked:
                base = sweep_deg(now, heading_offset)
                if random.random() < SNAP_CHANCE:
                    heading_offset += random.uniform(-2.2, 2.2)  # a sudden re-aim
            elif now >= coil_at and now >= frozen_until and low_since is None:
                # THE STRIKE, as scheduling rather than choreography. Each phase
                # only hands the animator a target and a time; the tick below
                # keeps sending, so the base follows the victim all the way
                # through the draw, the lunge and the recovery.
                if coil == "out":
                    print("...drawing back")
                    log.event(now, "coil", base=base, volts=(volts if volts else "?"))
                    wp.go(4, POSE_COIL_DEG[4], COIL_MS, now)
                    wp.go(3, POSE_COIL_DEG[3], COIL_MS, now)
                    coil, coil_at = "folding", now + COIL_STAGGER_S
                elif coil == "folding":
                    wp.go(5, POSE_COIL_DEG[5], COIL_MS, now)
                    coil, coil_at = "coiling", now + COIL_MS / 1000
                elif coil == "coiling":
                    coil = "coiled"
                    coil_at = now + (hold_override or random.uniform(*COIL_HOLD_S))
                    hold_override = None
                elif coil == "coiled":
                    print("LURCH")
                    log.event(now, "lurch", base=base, volts=(volts if volts else "?"))
                    for _sid, _deg in lurch_pose().items():
                        wp.go(_sid, _deg, LURCH_MS, now)
                    coil, coil_at = "settling", now + LURCH_MS / 1000
                else:
                    sid = LURCH_OVERSHOOT_JOINT
                    wp.go(sid, reach_pose()[sid], SETTLE_MS, now)
                    coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)

            # ONE packet per tick, carrying everything at once: the animated
            # posture, the writhe laid over it, and the freshest base heading.
            # --- WAYPOINTS --------------------------------------------- #
            # Give a joint somewhere to go and how long to take, then leave it
            # alone until it gets there. Nothing is sent per tick.

            # The base re-aims only once the victim has actually moved, and
            # never faster than BASE_EVERY_S. Every command restarts the
            # servo's move, so a coarse stream of long travels beats a fine
            # stream of short ones — which is the whole lesson here.
            aim = clamp_deg(6, base)
            if (abs(aim - wp.target.get(6, 1e9)) >= BASE_STEP_DEG
                    and now - last_base_cmd >= BASE_EVERY_S):
                travel = abs(aim - wp.target.get(6, aim))
                wp.go(6, aim, max(BASE_EVERY_S * 1000 * BASE_OVERLAP,
                                  travel / TRACK_SLEW_DPS * 1000 * BASE_OVERLAP), now)
                last_base_cmd = now

            # Each writhe joint is sent to the sine's next peak or trough, and
            # draws the line there itself. Two commands a cycle, not fifteen.
            if amp > 0:
                for sid, depth, rate, phase in WRITHE_WAVES:
                    if not wp.free(sid, now):
                        continue  # still travelling; interrupting is the bug
                    when, sign = next_extreme(rate, phase, now)
                    if sid in POSED_JOINTS:
                        # sway around wherever the strike has left this joint,
                        # and more gently while drawn back than while reaching
                        coiled = coil in ("coiling", "coiled")
                        home = POSE_COIL_DEG[sid] if coiled else reach_pose()[sid]
                        depth *= 0.6 if coiled else 1.0
                    else:
                        home = POSE_POINT_DEG[sid]
                    wp.go(sid, clamp_deg(sid, home + sign * depth),
                          max(300.0, (when - now) * 1000), now)

            if time.monotonic() - t0 > tick_due:
                late += 1  # the work overran its slot; the cadence is slipping
            t_send = time.monotonic()
            packets += wp.flush(arm)
            send_ms = (time.monotonic() - t_send) * 1000
            slow_send = max(slow_send, send_ms)
            pace(tick_due)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nreleasing...")
        try:
            arm.send(POSE_SLACK_DEG, PARK_MS)
            time.sleep(PARK_MS / 1000 + 0.2)
            arm.relax()  # a pose is not a rest: unload, or it holds all night
        except Exception as err:  # noqa: BLE001
            print(f"arm: could not park it ({err}); power-cycle it to relax")
        keys.close()
        log.close()
        if eyes is not None:
            eyes.close()


if __name__ == "__main__":
    main()
