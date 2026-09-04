#!/usr/bin/env python3
"""Gloom hand mode (Tears of the Kingdom): the arm points toward you and
sweeps left <-> right, searching for victims.

    ./gloom.py            # wireless (BLE)
    ./gloom.py --usb      # wired

All values are CENTERED DEGREES (0.0 = servo midpoint, +/-120 span,
soft-limited per angles.LIMITS_DEG). Three layers of creep, tunable below:
  1. HUNT   — slow uneven base sweep between SWEEP_LO_DEG and SWEEP_HI_DEG
  2. WRITHE — continuous small motion: gripper gropes, wrist rolls and nods
  3. TWITCH — random freezes ("...did it hear something?") and sudden fast
              snaps to a new heading

POSE_POINT_DEG is the "arm extended toward the viewer" posture. Every arm
is assembled slightly differently — pre-flight it joint by joint with
move_ble.py before the first hunt, and back off anything that touches or
buzzes. Ctrl-C exits through a calm return to rest.

Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
"""
import math
import random
import sys
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


def main() -> None:
    arm = Backend("--usb" in sys.argv)
    print("assuming the pose...")
    arm.send({**POSE_POINT_DEG, 6: (SWEEP_LO_DEG + SWEEP_HI_DEG) / 2}, 2500)
    time.sleep(2.7)
    print("hunting. Ctrl-C to release the victim.")

    t0 = time.monotonic()
    heading_offset = 0.0  # phase shift accumulated by snaps
    try:
        while True:
            now = time.monotonic() - t0

            if random.random() < FREEZE_CHANCE:
                time.sleep(random.uniform(*FREEZE_S))  # ...it heard something
                continue

            # HUNT: uneven sinusoid — jittered phase makes the pace lurch.
            phase = 2 * math.pi * (now / SWEEP_PERIOD) + heading_offset
            sweep = 0.5 * (1 + math.sin(phase + 0.35 * math.sin(2.7 * now)))
            base = SWEEP_LO_DEG + sweep * (SWEEP_HI_DEG - SWEEP_LO_DEG)

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
        print("\nreleasing...")
        arm.send(POSE_REST_DEG, 2500)
        time.sleep(2.7)


if __name__ == "__main__":
    main()
