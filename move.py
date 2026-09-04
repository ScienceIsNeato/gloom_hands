#!/usr/bin/env python3
"""Move one servo:  move.py <servo 1-6> <position 0-1000> [duration_ms]

Positions are servo units (~0.24 degrees each, 500 = center). Durations
default to a calm 1500 ms — never command a big jump with a tiny duration.
Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
"""
import sys
import xarm

if len(sys.argv) < 3:
    raise SystemExit(__doc__)
sid, pos = int(sys.argv[1]), int(sys.argv[2])
dur = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
if not (1 <= sid <= 6 and 0 <= pos <= 1000):
    raise SystemExit("servo must be 1-6, position 0-1000")

arm = xarm.Controller("USB")
arm.setPosition(sid, pos, dur, wait=True)
print(f"servo {sid} -> {pos} ({dur} ms), now at {arm.getPosition(sid)}")
