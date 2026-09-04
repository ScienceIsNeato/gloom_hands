#!/usr/bin/env python3
"""First-contact check for the Hiwonder/LewanSoul xArm 1S over USB.

Plug the arm's controller board into the Mac with a USB cable, power the
arm from its own supply (USB alone cannot drive the servos), flip the
power switch ON, then:

    .venv/bin/python probe.py
"""
import hid
import xarm

# The xArm controller enumerates as an STM-based HID device.
XARM_VID, XARM_PID = 0x0483, 0x5750

matches = [d for d in hid.enumerate() if d["vendor_id"] == XARM_VID and d["product_id"] == XARM_PID]
if not matches:
    print("No xArm controller found on USB.")
    print("  - cable plugged into the CONTROLLER board's USB port?")
    print("  - arm power supply connected and switch ON?")
    print("  - try a different cable (some are charge-only)")
    raise SystemExit(1)

for d in matches:
    print(f"Found: {d['product_string']} (serial {d['serial_number']})")

arm = xarm.Controller("USB", debug=True)
print("Battery voltage:", arm.getBatteryVoltage(), "V")

# Read where every joint currently sits (units 0-1000, ~0.24 deg/unit).
NAMES = {1: "gripper", 2: "wrist roll", 3: "wrist bend", 4: "elbow", 5: "shoulder", 6: "base"}
for sid in range(1, 7):
    pos = arm.getPosition(sid)
    print(f"  servo {sid} ({NAMES[sid]:10s}): {pos}")

print("\nAll good — try:  .venv/bin/python move.py 6 600   (base, gently right)")
