#!/usr/bin/env python3
"""PANIC BUTTON. Cut the motors so the arm goes limp and stops singing.

    ./relax.py          # wireless (Bluetooth), the way the hunt runs
    ./relax.py --usb    # wired

That tone is the servo alarm. It means under-voltage or heat, and holding
the pose through it is how a servo gets cooked. This unloads every joint,
which is gentler than pulling the power and leaves the board talking.

THE ARM GOES SLACK, so put a hand under it first if it is holding
anything out over an edge.
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
import sys

if "--usb" in sys.argv:
    import xarm

    xarm.Controller("USB").servoOff()
    print("unloaded over USB — the arm is limp.")
else:
    from ble_arm import BleArm

    arm = BleArm()
    arm.unload()
    print("unloaded over Bluetooth — the arm is limp.")
    arm.close()
