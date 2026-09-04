#!/usr/bin/env python3
"""Keyboard teleop for the xArm 1S. Run in a real terminal:

    .venv/bin/python teleop.py           # wired (USB)
    .venv/bin/python teleop.py --ble     # wireless (Bluetooth LE)

  q/a  base left/right          w/s  shoulder up/down
  e/d  elbow up/down            r/f  wrist bend up/down
  t/g  wrist roll               y/h  gripper close/open
  c    center everything (slowly)    x    quit

Each keypress nudges the joint by STEP units over STEP_MS milliseconds.
USB reads the arm's actual pose at startup; BLE can't read positions with
this simple write-only link, so it gently centers everything first and
tracks from there.
"""
import curses
import sys

STEP = 25          # units per keypress (~6 degrees)
STEP_MS = 300      # movement duration per nudge
LIMITS = {i: (50, 950) for i in range(1, 7)}  # soft limits, stay off the stops

KEYMAP = {
    "q": (6, +1), "a": (6, -1),
    "w": (5, +1), "s": (5, -1),
    "e": (4, +1), "d": (4, -1),
    "r": (3, +1), "f": (3, -1),
    "t": (2, +1), "g": (2, -1),
    "y": (1, +1), "h": (1, -1),
}
NAMES = {1: "gripper", 2: "wrist roll", 3: "wrist bend", 4: "elbow", 5: "shoulder", 6: "base"}


class UsbBackend:
    label = "USB"

    def __init__(self) -> None:
        import xarm

        self._xarm = xarm
        self._arm = xarm.Controller("USB")

    def start_positions(self) -> dict[int, int]:
        return {sid: self._arm.getPosition(sid) for sid in range(1, 7)}

    def set(self, sid: int, pos: int, dur: int) -> None:
        self._arm.setPosition(sid, pos, dur, wait=False)

    def set_all(self, positions: dict[int, int], dur: int) -> None:
        servos = [self._xarm.Servo(sid, p) for sid, p in positions.items()]
        self._arm.setPosition(servos, dur, wait=True)


class BleBackend:
    label = "BLE"

    def __init__(self) -> None:
        from ble_arm import BleArm

        self._arm = BleArm()

    def start_positions(self) -> dict[int, int]:
        # Write-only link: establish a known pose instead of reading one.
        self.set_all({sid: 500 for sid in range(1, 7)}, 2000)
        return {sid: 500 for sid in range(1, 7)}

    def set(self, sid: int, pos: int, dur: int) -> None:
        self._arm.set_position(sid, pos, dur)

    def set_all(self, positions: dict[int, int], dur: int) -> None:
        self._arm.set_position(list(positions.items()), duration_ms=dur)


def main(scr: "curses.window") -> None:
    curses.cbreak()
    scr.nodelay(False)
    backend = BleBackend() if "--ble" in sys.argv else UsbBackend()
    pos = backend.start_positions()

    def draw() -> None:
        scr.erase()
        scr.addstr(0, 0, f"xArm teleop [{backend.label}] — q/a w/s e/d r/f t/g y/h · c center · x quit")
        for sid in range(1, 7):
            scr.addstr(sid + 1, 2, f"servo {sid} {NAMES[sid]:10s} {pos[sid]:4d}")
        scr.refresh()

    draw()
    while True:
        ch = scr.getkey()
        if ch == "x":
            break
        if ch == "c":
            for sid in pos:
                pos[sid] = 500
            backend.set_all(pos, 2000)
            draw()
            continue
        if ch in KEYMAP:
            sid, sign = KEYMAP[ch]
            lo, hi = LIMITS[sid]
            pos[sid] = max(lo, min(hi, pos[sid] + sign * STEP))
            backend.set(sid, pos[sid], STEP_MS)
            draw()


if __name__ == "__main__":
    curses.wrapper(main)
