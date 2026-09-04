#!/usr/bin/env python3
"""Wireless single-servo move:  move_ble.py <servo 1-6> <position 0-1000> [duration_ms]

Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
"""
import sys
import time

from ble_arm import BleArm

if len(sys.argv) < 3:
    raise SystemExit(__doc__)
sid, pos = int(sys.argv[1]), int(sys.argv[2])
dur = int(sys.argv[3]) if len(sys.argv) > 3 else 1500

arm = BleArm()
arm.set_position(sid, pos, dur)
time.sleep(dur / 1000 + 0.2)
print(f"sent: servo {sid} -> {pos} over {dur} ms")
arm.close()
