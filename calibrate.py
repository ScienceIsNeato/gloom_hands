#!/usr/bin/env python3
"""Which way does each joint actually turn? Three questions, three answers.

    ./calibrate.py

Everything about holding the hand steady depends on knowing where each joint
sits in space, and that cannot be inferred from the tuned poses — I have
tried, and the poses can be read several contradictory ways. So: move one
joint at a time from straight up, by a large and obvious amount, and look.

FORWARD means toward whoever the arm is pointing at. BACKWARD is the other
way. Nothing here needs the camera.
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

from gloom import Backend, DryBackend

ZERO = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0}
STEPS = (
    (5, "SHOULDER", "the whole arm"),
    (4, "ELBOW", "the forearm and everything above it"),
    (3, "WRIST", "the hand"),
)


def main() -> None:
    arm = DryBackend() if "--dry" in sys.argv else Backend("--usb" in sys.argv)
    try:
        print("\nstanding the arm straight up — all joints at zero.")
        arm.send(ZERO, 3000)
        time.sleep(3.5)
        input("  is it straight up? [enter] ")

        for sid, name, what in STEPS:
            print(f"\n{name} (servo {sid}) to +45, everything else back to zero.")
            arm.send({**ZERO, sid: 45.0}, 2500)
            time.sleep(3.0)
            print(f"  watch {what}.")
            ans = input(f"  did +45 move it FORWARD or BACKWARD? [f/b] ").strip().lower()
            print(f"  -> servo {sid}: +45 is {'FORWARD' if ans.startswith('f') else 'BACKWARD'}")
            arm.send(ZERO, 2000)
            time.sleep(2.2)

        print("\nthat is everything. Tell me the three answers.")
    except (KeyboardInterrupt, EOFError):
        print("\ninterrupted")
    finally:
        arm.send(ZERO, 2000)
        time.sleep(2.2)
        arm.relax()
        print("servos off.")


if __name__ == "__main__":
    main()
