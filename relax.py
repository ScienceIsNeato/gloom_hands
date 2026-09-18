#!/usr/bin/env python3
"""PANIC BUTTON. Take the current off the servos so the arm goes limp.

    ./relax.py            # unload AND drop the Bluetooth link (what works)
    ./relax.py --stay     # unload but HOLD the link open — the experiment
    ./relax.py --usb      # wired

That tone is the servo alarm: under-voltage or heat, and holding the pose
through it is how a servo gets cooked.

Two things can take the load off over Bluetooth, and on this board they are
not equally effective. The CMD_SERVO_STOP unload is the documented way and
appears to do nothing here. Dropping the LINK does release it — the arm has
only ever gone limp when the process exited and the connection died with it.
So the default does both, link last.

--stay is how to tell them apart. It sends the unload, then sits there
holding the connection open. Push the arm while it waits:
  limp      -> the unload command works after all
  stiff     -> only the disconnect releases it, which is what we assume
Press Ctrl-C and it disconnects, which should go limp if the assumption holds.

THE ARM GOES SLACK, so support it first if it is holding out over an edge.
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
import time

if "--usb" in sys.argv:
    import xarm

    xarm.Controller("USB").servoOff()
    print("unloaded over USB — the arm should be limp.")
    raise SystemExit

from ble_arm import BleArm  # noqa: E402

arm = BleArm()
arm.unload()
print("unload command sent (CMD_SERVO_STOP, all six servos).")

if "--stay" not in sys.argv:
    arm.disconnect()
    print("Bluetooth link dropped. The arm should be limp now — push it and see.")
    raise SystemExit

print("\nHOLDING the link open on purpose.")
print("Push the arm around NOW and watch what it does:")
print("  goes limp  -> the unload command works on its own")
print("  stays firm -> only dropping the link releases it")
print("\nCtrl-C to disconnect (which should then release it).")
try:
    while True:
        time.sleep(1.0)
except KeyboardInterrupt:
    arm.disconnect()
    print("\nlink dropped. If it went limp only now, the link is what matters.")
