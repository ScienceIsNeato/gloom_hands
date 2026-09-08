#!/usr/bin/env python3
"""Move several joints at once and hold, for finding poses by eye:

    ./pose.py 5=-12 4=80 3=12          # servo=degrees pairs, any joints
    ./pose.py point                    # gloom.py's POSE_POINT_DEG
    ./pose.py coil                     # gloom.py's POSE_COIL_DEG
    ./pose.py coil 4=60                # a named pose with overrides
    ./pose.py rest                     # everything to 0
    ./pose.py --usb ... / --ms 1200 ...

Angles are centered degrees, soft-limited per angles.LIMITS_DEG. Iterate
until it looks right, then paste the numbers into gloom.py.
Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, "bin", "python")
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import sys
import time

from angles import NAMES, clamp_deg

args = sys.argv[1:]
usb = "--usb" in args
ms = 1500
if "--ms" in args:
    i = args.index("--ms")
    ms = int(args[i + 1])
    del args[i:i + 2]
args = [a for a in args if a != "--usb"]
if not args:
    raise SystemExit(__doc__)

from gloom import POSE_COIL_DEG, POSE_POINT_DEG, POSE_REST_DEG, Backend  # noqa: E402

NAMED = {"point": POSE_POINT_DEG, "coil": POSE_COIL_DEG, "rest": POSE_REST_DEG}
moves: dict[int, float] = {}
for a in args:
    if a in NAMED:
        moves.update(NAMED[a])
    elif "=" in a:
        sid, deg = a.split("=", 1)
        moves[int(sid)] = float(deg)
    else:
        raise SystemExit(f"unrecognized {a!r}\n{__doc__}")
moves = {sid: clamp_deg(sid, d) for sid, d in moves.items()}

arm = Backend(usb)
arm.send(moves, ms)
for sid, d in sorted(moves.items()):
    print(f"  servo {sid} {NAMES[sid]:10s} -> {d:+6.1f} deg")
time.sleep(ms / 1000 + 0.3)
print("holding. Paste the numbers you like into gloom.py.")
