#!/usr/bin/env python3
"""Why is the motion jerky? One variable at a time, watched by eye.

    ./smoothtest.py            # wireless
    ./smoothtest.py --usb      # wired
    ./smoothtest.py --joint 5  # test the shoulder instead of the base

Everything about this problem points one way: the board's light flashes in
time with the jerk, and it is WORSE over USB, which is the faster link. That
is the opposite of a bandwidth problem. It says each packet is producing a
visible step, so the more packets, the more steps.

This sends the arm through the same slow sweep several times over, changing
only how the sweep is commanded, and asks you to watch. Nothing here reads
the camera or tracks anything.

  1 QUIET      no commands at all. Is it still when nobody is talking to it?
  2 ONE MOVE   a single command, four seconds long. THE CONTROL: if this is
               not smooth, the servo cannot make a smooth move and nothing
               about the command stream matters.
  3-6 STREAMED the same sweep, in steps, at 0.5 / 1 / 2 / 4.5 packets a
               second. If the jerking tracks the rate, each packet is a step
               and the fix is to send fewer, longer commands.
  7 CROWDED    4.5 a second again, but commanding five joints instead of one,
               in case it is packet size rather than packet rate.

Say which phases looked smooth and which did not. That is the whole result.
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, *(("Scripts", "python.exe") if _os.name == "nt" else ("bin", "python")))
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    if _os.name == "nt":
        import subprocess as _sp
        _sys.exit(_sp.call([_venv_py] + _sys.argv))
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import argparse
import math
import time

from angles import NAMES
from gloom import Backend, DryBackend, POSE_SLACK_DEG

SWEEP = 40.0        # degrees either side of centre
PERIOD = 8.0        # seconds for one there-and-back
PASSES = 1.5        # how much of the sweep each streamed phase gets


def sweep_at(t: float) -> float:
    return SWEEP * math.sin(2 * math.pi * t / PERIOD)


def banner(n: int, title: str, detail: str) -> None:
    print(f"\n{'=' * 64}\n  {n}. {title}\n     {detail}\n{'=' * 64}")


def main() -> None:
    p = argparse.ArgumentParser(description="isolate what makes the motion jerky")
    p.add_argument("--usb", action="store_true", help="wired instead of Bluetooth")
    p.add_argument("--dry", action="store_true", help="no arm; just show the command pattern")
    p.add_argument("--joint", type=int, default=6, help="which servo to sweep (default 6, the base)")
    a = p.parse_args()

    sid = a.joint
    arm = DryBackend() if a.dry else Backend(a.usb)
    others = [s for s in (5, 4, 3, 2, 1) if s != sid][:4]
    print(f"\nsweeping servo {sid} ({NAMES.get(sid, '?')}) "
          f"+/-{SWEEP:.0f} deg. Watch the arm, not the screen.")
    arm.send({sid: 0.0}, 1500)
    time.sleep(1.8)

    try:
        banner(1, "QUIET", "no commands for 6 s — it should be perfectly still")
        time.sleep(6.0)

        banner(2, "ONE MOVE", "a single command, 4 s long. THE CONTROL.")
        arm.send({sid: SWEEP}, 4000)
        time.sleep(4.5)
        arm.send({sid: -SWEEP}, 4000)
        time.sleep(4.5)
        arm.send({sid: 0.0}, 2000)
        time.sleep(2.2)

        for n, hz in ((3, 0.5), (4, 1.0), (5, 2.0), (6, 4.5)):
            gap = 1.0 / hz
            banner(n, f"STREAMED at {hz} packets/s",
                   f"same sweep, a command every {gap*1000:.0f} ms lasting {gap*1600:.0f} ms")
            t0 = time.monotonic()
            while time.monotonic() - t0 < PERIOD * PASSES:
                t = time.monotonic() - t0
                arm.send({sid: sweep_at(t)}, int(gap * 1600))
                time.sleep(gap)
            arm.send({sid: 0.0}, 1500)
            time.sleep(1.8)

        banner(7, "CROWDED", "4.5 packets/s again, but five joints per packet")
        t0 = time.monotonic()
        while time.monotonic() - t0 < PERIOD * PASSES:
            t = time.monotonic() - t0
            pose = {sid: sweep_at(t)}
            for i, s in enumerate(others):
                pose[s] = 6.0 * math.sin(2 * math.pi * t / PERIOD + i)
            arm.send(pose, 350)
            time.sleep(0.22)

        print("\n" + "=" * 64)
        print("  Done. Which phases were smooth?")
        print("    2 rough        -> the servo cannot make a smooth move; look at")
        print("                      the supply, the load, or that joint itself.")
        print("    2 fine, 3-6 get rougher as the rate rises")
        print("                   -> each packet is a step. Send fewer, longer.")
        print("    6 rough but 7 no worse -> rate, not packet size.")
        print("    all fine       -> it is something the hunt adds, not the stream.")
        print("=" * 64)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        arm.send(POSE_SLACK_DEG, 2000)
        time.sleep(2.2)
        arm.relax()
        print("servos off.")


if __name__ == "__main__":
    main()
