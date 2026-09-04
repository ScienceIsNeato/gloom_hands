#!/usr/bin/env python3
"""Gloom hand mode (Tears of the Kingdom): the arm points toward you and
sweeps left <-> right, searching for victims.

    ./gloom.py            # wireless (BLE)
    ./gloom.py --usb      # wired

Three layers of creep, all tunable below:
  1. HUNT   — slow uneven base sweep between SWEEP_LO and SWEEP_HI
  2. WRITHE — continuous small motion: gripper gropes, wrist rolls and nods
  3. TWITCH — random freezes ("...did it hear something?") and sudden fast
              snaps to a new heading

POSE_POINT is the "arm extended toward the viewer" posture. Every arm is
assembled slightly differently — if the arm points at the ceiling or the
table, adjust shoulder (5), elbow (4), and wrist bend (3) there first,
one at a time, with move.py.

Ctrl-C exits through a calm return to rest. Positions are units 0-1000,
500 = center. Servo map: 1 gripper · 2 wrist roll · 3 wrist bend ·
4 elbow · 5 shoulder · 6 base.
"""
import math
import random
import sys
import time

# ---- the posture (TUNE THESE FIRST) --------------------------------- #
POSE_POINT = {
    5: 650,  # shoulder: leaned forward
    4: 300,  # elbow: bent back to bring the forearm level
    3: 550,  # wrist bend: aimed at the viewer
    2: 500,  # wrist roll: knuckles up
    1: 550,  # gripper: half-open, ready to grab
}
POSE_REST = {1: 500, 2: 500, 3: 500, 4: 500, 5: 500, 6: 500}

# ---- the hunt -------------------------------------------------------- #
SWEEP_LO, SWEEP_HI = 280, 720   # base range of the search
SWEEP_PERIOD = 14.0             # seconds for a full left-right-left pass
TICK = 0.22                     # seconds between command packets

# ---- the writhe ------------------------------------------------------ #
GROPE_DEPTH = 90        # gripper flex range around its pose value
ROLL_DEPTH = 60         # wrist roll writhe
NOD_DEPTH = 45          # wrist bend searching nods

# ---- the twitch ------------------------------------------------------ #
FREEZE_CHANCE = 0.012   # per tick: freeze mid-sweep...
FREEZE_S = (0.8, 2.2)   # ...for this long
SNAP_CHANCE = 0.010     # per tick: sudden fast snap to a random heading
SNAP_MS = 280           # how fast the snap lands (small ms = violent)


def clamp(x: float) -> int:
    return int(max(60, min(940, x)))


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

    def send(self, moves: dict[int, int], dur_ms: int) -> None:
        if self._servo is not None:
            self._arm.setPosition(
                [self._servo(sid, pos) for sid, pos in moves.items()], dur_ms, wait=False
            )
        else:
            self._arm.set_position(list(moves.items()), duration_ms=dur_ms)


def main() -> None:
    arm = Backend("--usb" in sys.argv)
    print("assuming the pose...")
    arm.send({**POSE_POINT, 6: (SWEEP_LO + SWEEP_HI) // 2}, 2500)
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
            base = SWEEP_LO + sweep * (SWEEP_HI - SWEEP_LO)

            if random.random() < SNAP_CHANCE:
                heading_offset += random.uniform(-2.2, 2.2)
                arm.send({6: clamp(base)}, SNAP_MS)  # violent re-aim
                time.sleep(SNAP_MS / 1000 + 0.1)
                continue

            # WRITHE: independent slow oscillators so nothing ever repeats.
            moves = {
                6: clamp(base),
                1: clamp(POSE_POINT[1] + GROPE_DEPTH * math.sin(1.9 * now + 1.0)
                         + 25 * math.sin(6.3 * now)),
                2: clamp(POSE_POINT[2] + ROLL_DEPTH * math.sin(0.7 * now)),
                3: clamp(POSE_POINT[3] + NOD_DEPTH * math.sin(1.3 * now + 2.1)),
            }
            arm.send(moves, int(TICK * 1000) + 80)
            time.sleep(TICK)
    except KeyboardInterrupt:
        print("\nreleasing...")
        arm.send(POSE_REST, 2500)
        time.sleep(2.7)


if __name__ == "__main__":
    main()
