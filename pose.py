#!/usr/bin/env python3
"""Dial in a pose one joint at a time. Each key nudges ONE joint by STEP
degrees in a quick move; after every change the pose is printed as a line
you can paste straight into gloom.py.

    ./pose.py            # start from POSE_COIL_DEG
    ./pose.py point      # start from POSE_POINT_DEG
    ./pose.py --usb      # wired

      1 / 2   shoulder (servo 5)  - / +
      4 / 5   elbow    (servo 4)  - / +
      7 / 8   wrist    (servo 3)  - / +
      - / =   step size down / up      q   quit (arm stays where it is)

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
import sys

if sys.platform == "win32":  # POSIX terminal control does not exist there
    import msvcrt
else:
    import termios
    import tty

from angles import NAMES, clamp_deg

STEP_DEG = 5.0   # per keypress; - / = adjust
MOVE_MS = 250    # quick nudge

KEYS = {"1": (5, -1), "2": (5, +1), "4": (4, -1), "5": (4, +1), "7": (3, -1), "8": (3, +1)}

args = sys.argv[1:]
usb = "--usb" in args
dry = "--dry" in args
start = "point" if "point" in args else "coil"

from gloom import POSE_COIL_DEG, POSE_POINT_DEG, Backend, DryBackend  # noqa: E402

pose = dict(POSE_POINT_DEG if start == "point" else POSE_COIL_DEG)
for sid in (5, 4, 3):
    pose.setdefault(sid, 0.0)


def show(step: float) -> None:
    line = ", ".join(f"{sid}: {pose[sid]:.1f}" for sid in (5, 4, 3))
    print(f"POSE_COIL_DEG = {{{line}}}    (step {step:g} deg)")


def getch() -> str:
    """One keypress, no Enter. Falls back to line-at-a-time when stdin is
    piped (tests), and uses msvcrt on Windows where termios does not exist."""
    if not sys.stdin.isatty():  # piped keys (tests): one char per line
        line = sys.stdin.readline()
        return line.strip()[:1] if line else "q"
    if sys.platform == "win32":
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # arrow/function key: swallow the second byte
            msvcrt.getwch()
            return ""
        return ch
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


arm = DryBackend() if dry else Backend(usb)
print(f"moving to the {start} pose...")
arm.send({sid: pose[sid] for sid in (5, 4, 3)}, 1500)
print("1/2 shoulder  4/5 elbow  7/8 wrist  -/= step  q quit")
step = STEP_DEG
show(step)
while True:
    ch = getch()
    if ch == "q":
        break
    if ch == "-":
        step = max(1.0, step / 2)
        show(step)
    elif ch == "=":
        step = min(20.0, step * 2)
        show(step)
    elif ch in KEYS:
        sid, sign = KEYS[ch]
        pose[sid] = clamp_deg(sid, pose[sid] + sign * step)
        arm.send({sid: pose[sid]}, MOVE_MS)  # this joint only
        show(step)
print("done — paste the last POSE_COIL_DEG line into gloom.py")
