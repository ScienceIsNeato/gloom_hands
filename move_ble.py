#!/usr/bin/env python3
"""Wireless single-servo move, in degrees:

    move_ble.py <servo 1-6> <degrees -120..120> [duration_ms]
    move_ble.py --units <servo> <units 0-1000> [duration_ms]   # raw escape hatch

0 deg = the servo's midpoint; positive = toward unit 1000. Soft limits from
angles.LIMITS_DEG apply. Servo map: 1 gripper · 2 wrist roll · 3 wrist bend ·
4 elbow · 5 shoulder · 6 base
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

from angles import servo_units
from ble_arm import BleArm

args = [a for a in sys.argv[1:] if a != "--units"]
raw = "--units" in sys.argv
if len(args) < 2:
    raise SystemExit(__doc__)
sid = int(args[0])
dur = int(args[2]) if len(args) > 2 else 1500
units = int(args[1]) if raw else servo_units(sid, float(args[1]))

arm = BleArm()
arm.set_position(sid, units, dur)
time.sleep(dur / 1000 + 0.2)
what = f"{args[1]} units" if raw else f"{float(args[1]):+.1f} deg (unit {units})"
print(f"sent: servo {sid} -> {what} over {dur} ms")
arm.close()
