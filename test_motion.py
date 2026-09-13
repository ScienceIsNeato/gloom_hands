#!/usr/bin/env python3
"""Motion budgets: can the arm actually render what the writhe asks for?

    ./test_motion.py

Two ways a writhe oscillator goes wrong, both invisible in the source:

  RENDER   commands go out once per TICK, so a component needs about eight
           of them per cycle or it steps instead of moving. A 1 Hz tremor
           at a 4.5 Hz command rate gets 4.5 steps and looks like jitter.
  CURRENT  a servo draws roughly in proportion to how fast it is told to
           move, and depth * rate is that demand. With the supply already
           browning out on the coil, every deg/s of continuous oscillation
           is worth having a reason for.

No hardware needed.
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
import math

from angles import LIMITS_DEG, NAMES
from gloom import POSE_COIL_DEG, POSE_POINT_DEG, TICK, WRITHE_TERMS, writhe_deg

MIN_STEPS_PER_CYCLE = 8.0   # below this a sine reads as stepping
MAX_JOINT_SLEW = 70.0       # deg/s of continuous demand on any one joint


def main() -> int:
    rate = 1.0 / TICK
    ceiling_hz = rate / MIN_STEPS_PER_CYCLE
    print(f"command rate {rate:.1f} Hz -> smooth up to {ceiling_hz:.2f} Hz "
          f"({ceiling_hz * 2 * math.pi:.1f} rad/s)\n")

    bad = []
    per_joint: dict[int, float] = {}
    for sid, label, depth, omega in WRITHE_TERMS:
        hz = omega / (2 * math.pi)
        steps = rate / hz
        slew = depth * omega
        per_joint[sid] = per_joint.get(sid, 0.0) + slew
        ok = steps >= MIN_STEPS_PER_CYCLE
        print(f"  servo {sid} {NAMES[sid]:11s} {label:7s} "
              f"+/-{depth:4.1f} deg at {hz:4.2f} Hz  "
              f"{steps:5.1f} steps/cycle  {slew:5.1f} deg/s  {'ok' if ok else 'STEPPY'}")
        if not ok:
            bad.append(f"servo {sid} {label}: {steps:.1f} steps/cycle, needs {MIN_STEPS_PER_CYCLE:.0f}")

    print()
    for sid, slew in sorted(per_joint.items()):
        ok = slew <= MAX_JOINT_SLEW
        print(f"  servo {sid} {NAMES[sid]:11s} total continuous demand {slew:5.1f} deg/s  "
              f"{'ok' if ok else 'TOO FAST'}")
        if not ok:
            bad.append(f"servo {sid}: {slew:.1f} deg/s exceeds {MAX_JOINT_SLEW:.0f}")

    # the writhe must never drive a joint through its soft limit
    for coiled in (False, True):
        for t in [i * 0.05 for i in range(400)]:
            for sid, deg in writhe_deg(t, 0.0, coiled=coiled).items():
                lo, hi = LIMITS_DEG[sid]
                if not lo - 1e-6 <= deg <= hi + 1e-6:
                    bad.append(f"servo {sid} reaches {deg:.1f} deg, outside {lo}..{hi}")

    # the writhe's resting point must match the pose it is decorating
    close = writhe_deg(0.0, 0.0)
    assert set(close) == {1, 2, 3, 6}, close
    for sid in (1, 2):
        span = abs(close[sid] - POSE_POINT_DEG[sid])
        if span > 40:
            bad.append(f"servo {sid} starts {span:.0f} deg from its pose")
    assert abs(writhe_deg(0.0, 0.0, coiled=True)[3] - POSE_COIL_DEG[3]) < 12, "coiled wrist drifts"

    if bad:
        print("\nFAILED:")
        for b in dict.fromkeys(bad):
            print(f"  - {b}")
        return 1
    print("\nmotion: all budgets ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
