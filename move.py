#!/usr/bin/env python3
"""Wired (USB) single-servo move, in degrees:

    move.py <servo 1-6> <degrees -120..120> [duration_ms]
    move.py --units <servo> <units 0-1000> [duration_ms]   # raw escape hatch

0 deg = the servo's midpoint; positive = toward unit 1000. Soft limits from
angles.LIMITS_DEG apply. Servo map: 1 gripper · 2 wrist roll · 3 wrist bend ·
4 elbow · 5 shoulder · 6 base
"""
import sys

import xarm

from angles import servo_units, units_to_deg

args = [a for a in sys.argv[1:] if a != "--units"]
raw = "--units" in sys.argv
if len(args) < 2:
    raise SystemExit(__doc__)
sid = int(args[0])
dur = int(args[2]) if len(args) > 2 else 1500
units = int(args[1]) if raw else servo_units(sid, float(args[1]))

arm = xarm.Controller("USB")
arm.setPosition(sid, units, dur, wait=True)
now = arm.getPosition(sid)
print(f"servo {sid} -> unit {units}, now at {now} ({units_to_deg(now):+.1f} deg)")
