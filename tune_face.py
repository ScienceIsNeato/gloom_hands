#!/usr/bin/env python3
"""Tune face detection against the camera that is actually fitted.

    ./tune_face.py --grab      # record frames: some with you in view, some empty
    ./tune_face.py             # sweep settings over those frames and report

A washed-out, grainy, 120-degree lens is a different problem from a clean
webcam, and the settings that suit one lose faces on the other. Guessing at
thresholds from the far side of a chat window has already cost several
rounds, so: record what the camera really produces, then measure.

--grab writes two sets of frames under tune_frames/ — WITH a face, and an
EMPTY room. Everything after that runs offline and can be re-run as often as
needed without touching the hardware.

The sweep reports, for each setting, how many face frames it found a face in
(higher is better) and how many empty frames it found one in (must be zero).
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
import argparse
import glob
import itertools
import time

import cv2

from gloom import CAPTURE_SIZE, VIDEO_SRC
from vision.capture import open_capture
from vision.detector import YuNetDetector, yunet_model_path

HERE = _os.path.dirname(_os.path.abspath(__file__))
FRAMES = _os.path.join(HERE, "tune_frames")


def grab(src: str, n: int) -> None:
    _os.makedirs(_os.path.join(FRAMES, "face"), exist_ok=True)
    _os.makedirs(_os.path.join(FRAMES, "empty"), exist_ok=True)
    cam = open_capture(src, *CAPTURE_SIZE)
    try:
        for kind, prompt in (("face", "STAND IN FRONT OF THE CAMERA as you normally would"),
                             ("empty", "now GET OUT OF SHOT and leave the room as it will be")):
            input(f"\n{prompt}, then press enter. ")
            print(f"  recording {n} frames over {n / 4:.0f}s — move around a bit...")
            for i in range(n):
                ok, frame = cam.read()
                if ok:
                    cv2.imwrite(_os.path.join(FRAMES, kind, f"{i:03d}.png"), frame)
                time.sleep(0.25)
            print(f"  saved {n} {kind} frames")
    finally:
        cam.release()
    print(f"\nrecorded into {FRAMES}. Now run ./tune_face.py to sweep.")


def sweep() -> int:
    faces = sorted(glob.glob(_os.path.join(FRAMES, "face", "*.png")))
    empties = sorted(glob.glob(_os.path.join(FRAMES, "empty", "*.png")))
    if not faces:
        print(f"no frames in {FRAMES}. Run ./tune_face.py --grab first.")
        return 1
    print(f"  {len(faces)} frames with a face, {len(empties)} empty\n")
    imgs = {k: [cv2.imread(p) for p in v] for k, v in (("face", faces), ("empty", empties))}

    print(f"  {'equalise':>9s} {'scale':>6s} {'score':>6s}   {'found':>12s}  {'false':>10s}")
    best = []
    for eq, scale, score in itertools.product((False, True), (0.5, 0.75), (0.3, 0.4, 0.5, 0.6, 0.7)):
        d = YuNetDetector(model=yunet_model_path(), scale=scale,
                          score_threshold=score, equalise=eq)
        hit = sum(1 for im in imgs["face"] if im is not None and d.detect(im))
        bad = sum(1 for im in imgs["empty"] if im is not None and d.detect(im))
        rate = hit / max(1, len(imgs["face"]))
        print(f"  {str(eq):>9s} {scale:6.2f} {score:6.2f}   {hit:5d}/{len(imgs['face']):<3d} {rate:5.0%}  "
              f"{bad:5d}/{len(imgs['empty']):<3d}")
        best.append((bad, -rate, eq, scale, score))
    best.sort()
    bad, negrate, eq, scale, score = best[0]
    print(f"\n  best with no false positives: equalise={eq} scale={scale} "
          f"score_threshold={score} -> finds a face in {-negrate:.0%} of frames")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="tune face detection to this camera")
    p.add_argument("--grab", action="store_true", help="record frames from the camera first")
    p.add_argument("--video-src", default=VIDEO_SRC)
    p.add_argument("-n", type=int, default=40, help="frames per set (default 40)")
    a = p.parse_args()
    if a.grab:
        grab(a.video_src, a.n)
        return
    raise SystemExit(sweep())


if __name__ == "__main__":
    main()
