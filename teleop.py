#!/usr/bin/env python3
"""Keyboard teleop for the xArm 1S, in degrees. Run in a real terminal:

    ./teleop.py           # wired (USB)
    ./teleop.py --ble     # wireless (Bluetooth LE)

  q/a  base left/right          w/s  shoulder up/down
  e/d  elbow up/down            r/f  wrist bend up/down
  t/g  wrist roll               y/h  gripper close/open
  c    center everything (slowly)    x    quit

Each keypress nudges the joint by STEP_DEG degrees over STEP_MS ms.
Angles are centered: 0.0 deg = midpoint, soft-limited per angles.LIMITS_DEG.
USB reads the arm's actual pose at startup; BLE can't read positions with
this simple write-only link, so it gently centers everything first and
tracks from there.
"""
import curses
import sys

from angles import NAMES, clamp_deg, servo_units, units_to_deg

STEP_DEG = 6.0     # degrees per keypress
STEP_MS = 300      # movement duration per nudge

KEYMAP = {
    "q": (6, +1), "a": (6, -1),
    "w": (5, +1), "s": (5, -1),
    "e": (4, +1), "d": (4, -1),
    "r": (3, +1), "f": (3, -1),
    "t": (2, +1), "g": (2, -1),
    "y": (1, +1), "h": (1, -1),
}


class UsbBackend:
    label = "USB"

    def __init__(self) -> None:
        import xarm

        self._xarm = xarm
        self._arm = xarm.Controller("USB")

    def start_angles(self) -> dict[int, float]:
        return {sid: units_to_deg(self._arm.getPosition(sid)) for sid in range(1, 7)}

    def set(self, sid: int, deg: float, dur: int) -> None:
        self._arm.setPosition(sid, servo_units(sid, deg), dur, wait=False)

    def set_all(self, angles: dict[int, float], dur: int) -> None:
        servos = [self._xarm.Servo(sid, servo_units(sid, d)) for sid, d in angles.items()]
        self._arm.setPosition(servos, dur, wait=True)


class BleBackend:
    label = "BLE"

    def __init__(self) -> None:
        from ble_arm import BleArm

        self._arm = BleArm()

    def start_angles(self) -> dict[int, float]:
        # Write-only link: establish a known pose instead of reading one.
        self.set_all({sid: 0.0 for sid in range(1, 7)}, 2000)
        return {sid: 0.0 for sid in range(1, 7)}

    def set(self, sid: int, deg: float, dur: int) -> None:
        self._arm.set_angle(sid, deg, dur)

    def set_all(self, angles: dict[int, float], dur: int) -> None:
        self._arm.set_angle(list(angles.items()), duration_ms=dur)


def main(scr: "curses.window") -> None:
    curses.cbreak()
    scr.nodelay(False)
    backend = BleBackend() if "--ble" in sys.argv else UsbBackend()
    ang = backend.start_angles()

    def draw() -> None:
        scr.erase()
        scr.addstr(0, 0, f"xArm teleop [{backend.label}] — q/a w/s e/d r/f t/g y/h · c center · x quit")
        for sid in range(1, 7):
            scr.addstr(sid + 1, 2, f"servo {sid} {NAMES[sid]:10s} {ang[sid]:+7.1f} deg")
        scr.refresh()

    draw()
    while True:
        ch = scr.getkey()
        if ch == "x":
            break
        if ch == "c":
            for sid in ang:
                ang[sid] = 0.0
            backend.set_all(ang, 2000)
            draw()
            continue
        if ch in KEYMAP:
            sid, sign = KEYMAP[ch]
            ang[sid] = clamp_deg(sid, ang[sid] + sign * STEP_DEG)
            backend.set(sid, ang[sid], STEP_MS)
            draw()


if __name__ == "__main__":
    curses.wrapper(main)
