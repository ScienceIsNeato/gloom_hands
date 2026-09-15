#!/usr/bin/env python3
"""Geometry regression: hand-computed cases for the camera-offset trig.

    ./test_geometry.py

No hardware, no camera. Run it after touching vision/geometry.py.
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
import sys

sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from vision import CameraPose, Detection, Locator, focal_px  # noqa: E402


def det(x, w=640, h=360, hpx=100, real=None):
    return Detection(x=x, top=100, bottom=100 + hpx, frame_w=w, frame_h=h, real_height_m=real)


def close(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"{a} != {b}"


def main() -> int:
    L = Locator(CameraPose(hfov_deg=60))
    f = focal_px(640, 60)
    close(f, 320 / math.tan(math.radians(30)))

    # 1. dead centre is dead ahead
    close(L.locate(det(320)).bearing_deg, 0)
    # 2. right edge of a 60 deg camera is -30 deg; left edge is +30
    close(L.locate(det(640)).cam_bearing_deg, -30)
    close(L.locate(det(0)).cam_bearing_deg, +30)
    # 3. mirrored flips it
    close(Locator(CameraPose(hfov_deg=60, mirrored=True)).locate(det(640)).cam_bearing_deg, +30)
    # 4. range comes from apparent height, and falls back when too small
    close(L.locate(det(320, hpx=200)).cam_range_m, f * 1.7 / 200)
    assert L.locate(det(320, hpx=200)).range_known
    close(L.locate(det(320, hpx=5)).cam_range_m, 3.0)
    assert not L.locate(det(320, hpx=5)).range_known
    # 5. a detector that knows its own real height overrides person_height_m
    close(L.locate(det(320, hpx=200, real=0.2)).cam_range_m, f * 0.2 / 200)
    # 6. camera yaw alone rotates the bearing
    close(Locator(CameraPose(yaw_deg=20)).locate(det(320)).bearing_deg, 20)

    hpx = f * 1.7 / 1.0  # a height that reads as exactly 1 m
    # 7. camera 1 m LEFT of the pivot, person 1 m ahead of it -> (1,1): +45 deg, sqrt2
    t = Locator(CameraPose(y_m=1.0, hfov_deg=60)).locate(det(320, hpx=hpx))
    close(t.bearing_deg, 45); close(t.range_m, math.sqrt(2)); close(t.x_m, 1); close(t.y_m, 1)
    # 8. camera 1 m IN FRONT, person 1 m ahead of it -> (2,0): straight on, 2 m
    t = Locator(CameraPose(x_m=1.0)).locate(det(320, hpx=hpx))
    close(t.bearing_deg, 0); close(t.range_m, 2)
    # 9. camera 1 m behind, turned 90 deg left -> (-1,1): 135 deg
    t = Locator(CameraPose(x_m=-1.0, yaw_deg=90)).locate(det(320, hpx=hpx))
    close(t.bearing_deg, 135); close(t.x_m, -1); close(t.y_m, 1)
    # 10. with no offset, range cannot affect bearing
    for hp in (10, 50, 200, 800):
        close(L.locate(det(500, hpx=hp)).bearing_deg, L.locate(det(500)).bearing_deg)
    # 11. observe() is the camera-relative view locate() is built on
    o, t = L.observe(det(480, hpx=200)), L.locate(det(480, hpx=200))
    close(o.angle_deg, t.cam_bearing_deg); close(o.distance_m, t.cam_range_m)

    print("geometry: all 11 checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
