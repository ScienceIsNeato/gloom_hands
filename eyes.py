#!/usr/bin/env python3
"""Look through the gloom hand's eyes without moving the arm.

    ./eyes.py                     # motion detector, using gloom.py's CAMERA settings
    ./eyes.py --detector person   # HOG person detector
    ./eyes.py --bench 60          # ms/frame for both detectors on this machine
    ./eyes.py --video-src walk.avi

Keys in the window:  d  switch detector   ESC  quit

Wraps halloween_tracker.preview (installed in ./.venv) and fills in the
camera pose and field of view from gloom.py's CAMERA block, so what you
calibrate here is exactly what the hunt uses. Any flag you pass overrides
those defaults; see ./eyes.py --help for the full list.
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, "bin", "python")
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import sys

from halloween_tracker.preview import main

from gloom import CAMERA, CAPTURE_SIZE, DETECT_ROI, DETECTOR, PERSON_HEIGHT_M, VIDEO_SRC

defaults = [
    "--video-src", str(VIDEO_SRC),
    "--width", str(CAPTURE_SIZE[0]), "--height", str(CAPTURE_SIZE[1]),
    "--detector", DETECTOR,
    "--roi-top", str(DETECT_ROI[0]),
    "--hfov", str(CAMERA["hfov_deg"]),
    "--cam-x", str(CAMERA["x_m"]), "--cam-y", str(CAMERA["y_m"]), "--cam-yaw", str(CAMERA["yaw_deg"]),
    "--person-height", str(PERSON_HEIGHT_M),
] + (["--mirrored"] if CAMERA["mirrored"] else [])

# argparse takes the last occurrence, so anything on the command line wins.
raise SystemExit(main(defaults + sys.argv[1:]))
