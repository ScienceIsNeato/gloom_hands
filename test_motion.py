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
from gloom import BASE_SIGN, NOD_DEPTH, CAMERA, CAPTURE_SIZE, POSE_COIL_DEG, POSE_POINT_DEG, TICK, WRITHE_TERMS, body_pose

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

    # the writhe must never drive a joint through its soft limit, in any
    # posture the animator can put the arm in
    for home, nod in ((POSE_POINT_DEG, 1.0), (POSE_COIL_DEG, 0.6)):
        for t in [i * 0.05 for i in range(400)]:
            pose = body_pose(t, 0.0, home, nod)
            assert set(pose) == {1, 2, 3, 4, 5, 6}, pose
            for sid, deg in pose.items():
                lo, hi = LIMITS_DEG[sid]
                if not lo - 1e-6 <= deg <= hi + 1e-6:
                    bad.append(f"servo {sid} reaches {deg:.1f} deg, outside {lo}..{hi}")

    # the oscillators decorate the animated posture rather than replacing it
    for home in (POSE_POINT_DEG, POSE_COIL_DEG):
        pose = body_pose(0.0, 0.0, home, 1.0)
        for sid in (4, 5):
            if abs(pose[sid] - home[sid]) > 1e-6:
                bad.append(f"servo {sid} drifted off the animated pose")
        if abs(pose[3] - home[3]) > NOD_DEPTH + 1e-6:
            bad.append("wrist nod exceeds its depth")

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
