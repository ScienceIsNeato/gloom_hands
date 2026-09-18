#!/usr/bin/env python3
"""Motion budgets: is the writhe sane to command and safe to run?

    ./test_motion.py

The arm is driven by WAYPOINTS now, not a stream: each joint is sent to its
sine's next turning point and draws the line there itself. So the old rule
here — enough command updates per cycle to render a curve — no longer
applies; there are exactly two per cycle by construction. What still matters:

  CURRENT   depth * rate is how fast a joint is being asked to move, and the
            supply is known to sag. The heavy joints get small, slow sways.
  RATE      two commands per cycle means a very fast oscillator would still
            pester its servo, and every command restarts the servo's move.
  LIMITS    the sway must not push any joint through its soft stop, in any
            posture the strike can leave it in.
  SENSE     the arm must turn toward the victim, not away.

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
from gloom import (BASE_SIGN, CAMERA, CAPTURE_SIZE, POSE_COIL_DEG, POSE_POINT_DEG,
                   WRITHE_TERMS, reach_pose)

MAX_JOINT_SLEW = 70.0       # deg/s of continuous demand on any one joint
MAX_JOINT_CMD_HZ = 1.2      # commands per second any one joint should need


def main() -> int:
    bad = []
    per_joint: dict[int, float] = {}
    print(f"  {'joint':22s} {'sway':>7s} {'period':>8s} {'cmds/s':>8s} {'slew':>9s}")
    for sid, label, depth, omega, _coiled in WRITHE_TERMS:
        period = 2 * math.pi / omega
        hz = 2.0 / period          # two waypoints per cycle
        slew = depth * omega
        per_joint[sid] = per_joint.get(sid, 0.0) + slew
        ok = hz <= MAX_JOINT_CMD_HZ
        print(f"  {sid} {NAMES[sid]:10s} {label:8s} {depth:5.1f}d {period:7.1f}s "
              f"{hz:8.2f} {slew:7.1f}d/s  {'ok' if ok else 'TOO CHATTY'}")
        if not ok:
            bad.append(f"servo {sid} {label}: {hz:.2f} commands/s, over {MAX_JOINT_CMD_HZ}")

    print()
    for sid, slew in sorted(per_joint.items()):
        ok = slew <= MAX_JOINT_SLEW
        print(f"  servo {sid} {NAMES[sid]:11s} total demand {slew:5.1f} deg/s  "
              f"{'ok' if ok else 'TOO FAST'}")
        if not ok:
            bad.append(f"servo {sid}: {slew:.1f} deg/s exceeds {MAX_JOINT_SLEW:.0f}")

    # the sway must not push a joint through a soft stop, in either posture
    for sid, label, depth, _, coiled_frac in WRITHE_TERMS:
        for posture, home_of, frac in (("reaching", reach_pose, 1.0),
                                       ("coiled", lambda: POSE_COIL_DEG, coiled_frac)):
            home = home_of().get(sid, POSE_POINT_DEG.get(sid))
            if home is None:
                continue
            lo, hi = LIMITS_DEG[sid]
            for edge in (home - depth * frac, home + depth * frac):
                if not lo - 1e-6 <= edge <= hi + 1e-6:
                    bad.append(f"servo {sid} {label} reaches {edge:.1f} deg while {posture}, "
                               f"outside {lo}..{hi}")

    # --- tracking sense: the arm must turn TOWARD the victim -------------- #
    # A webcam looking at you puts your right on the left of its frame. So a
    # face left-of-centre means you stepped to YOUR right, which is the arm's
    # LEFT, which must drive the base POSITIVE (before BASE_SIGN, which only
    # describes how the servo is bolted on).
    import math as _m

    import gloom as _g
    from vision import CameraPose, Detection, Locator
    from vision.geometry import focal_px

    pose = CameraPose(**dict(CAMERA, x_m=0.0, y_m=0.0, yaw_deg=0.0))

    class _E:
        _loc = Locator(pose, person_height_m=1.7)
        _reach = 45.0
        _bearing_at = _g.Eyes._bearing_at
        _map_to_sweep = _g.Eyes._map_to_sweep

    w, h = CAPTURE_SIZE
    hpx = focal_px(w, pose.hfov_deg) * 0.2 / 1.5
    def base_for(x_frac):
        t = _E._loc.locate(Detection(x=w * x_frac, top=0.0, bottom=hpx,
                                     frame_w=w, frame_h=h, real_height_m=0.2))
        return _E._map_to_sweep(_E(), t)

    left_of_frame, right_of_frame = base_for(0.25), base_for(0.75)
    print(f"\n  face left of centre  -> base {left_of_frame:+6.1f} "
          f"(must be positive: that is the arm's left)")
    print(f"  face right of centre -> base {right_of_frame:+6.1f} (must be negative)")
    if left_of_frame <= 0 or right_of_frame >= 0:
        bad.append("tracking sense inverted: the arm would turn away from the victim")
    if abs(left_of_frame + right_of_frame) > 1.0:
        bad.append("tracking is not symmetric about the centre of the frame")
    print(f"  BASE_SIGN {BASE_SIGN:+.0f} "
          f"({'positive degrees swing the arm to its left' if BASE_SIGN > 0 else 'inverted for this arm'})")

    if bad:
        print("\nFAILED:")
        for b in dict.fromkeys(bad):
            print(f"  - {b}")
        return 1
    print("\nmotion: all budgets ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
