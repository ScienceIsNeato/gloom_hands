#!/usr/bin/env python3
"""Every constructor argument must actually be stored.

    ./test_detectors.py

A detector took a `min_face_frac` argument, used `self.min_face_frac` deep
in its detect path, and never assigned it — so it built fine, imported fine,
ran fine against an empty room, and crashed the moment a real face appeared.
An edit had silently not applied, and nothing downstream could tell.

This walks each detector's signature and checks the object comes out holding
what it was handed. It is a dull check that would have caught a bug that
reached the hardware.
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
import inspect
import re
import sys

sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import numpy as np  # noqa: E402

from vision import detector as D  # noqa: E402
from vision.tracking import Tracker  # noqa: E402

#: Arguments that are deliberately consumed rather than kept.
CONSUMED = {"debug", "model", "cascade", "profile", "primary", "secondary"}


def stored(cls) -> list[str]:
    """Constructor arguments the class never keeps."""
    try:
        obj = cls() if cls is not D.YuNetDetector else cls(model=D.yunet_model_path())
    except RuntimeError as err:
        print(f"  {cls.__name__:18s} skipped ({str(err).splitlines()[0][:40]})")
        return []
    missing = []
    for name in inspect.signature(cls.__init__).parameters:
        if name in ("self", *CONSUMED):
            continue
        if not hasattr(obj, name):
            missing.append(name)
    flag = "ok" if not missing else f"MISSING {', '.join(missing)}"
    print(f"  {cls.__name__:18s} {len(inspect.signature(cls.__init__).parameters)-1:2d} args   {flag}")
    return missing


def main() -> int:
    bad = []
    print("  every constructor argument should survive into the object:\n")
    for cls in (D.BackgroundDetector, D.MotionDetector, D.FaceDetector,
                D.PersonDetector, D.YuNetDetector):
        bad += [f"{cls.__name__}.{m}" for m in stored(cls)]

    class Stub:
        frame_w, frame_h, roi_y0, last_frame, last = 640, 360, 0, None, {}
        def reset(self): pass
        def detect(self, f): return None
    obj = Tracker(Stub(), Stub())
    for name in inspect.signature(Tracker.__init__).parameters:
        if name in ("self", *CONSUMED):
            continue
        if not hasattr(obj, name):
            bad.append(f"Tracker.{name}")
    print(f"  {'Tracker':18s} {len(inspect.signature(Tracker.__init__).parameters)-1:2d} args   "
          f"{'ok' if not any(b.startswith('Tracker') for b in bad) else 'MISSING'}")

    # and the detect path must survive a real detection, not just an empty room
    print("\n  a detection actually flowing through YuNet's detect():")
    try:
        d = D.YuNetDetector(model=D.yunet_model_path())
        class FakeNet:
            def setInputSize(self, s): pass
            def detect(self, img):
                row = [100.0, 50.0, 60.0, 80.0] + [0.0] * 10 + [0.95]
                return 1, np.array([row], dtype=np.float32)
        d._net = FakeNet()
        got = d.detect(np.zeros((360, 640, 3), np.uint8))
        print(f"    returned {'a Detection' if got else 'None'} without raising")
    except RuntimeError:
        print("    skipped (no model)")
    except AttributeError as err:
        bad.append(f"YuNetDetector.detect: {err}")

    if bad:
        print("\nFAILED:")
        for b in dict.fromkeys(bad):
            print(f"  - {b}")
        return 1
    print("\ndetectors: every argument is kept, and detect() runs on a real hit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
