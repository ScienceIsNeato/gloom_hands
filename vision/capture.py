"""Opening a camera the same way on every platform.

Windows is the awkward one. OpenCV's default there is Media Foundation
(MSMF), which can take several seconds to open a webcam and often ignores
resolution requests; DirectShow is faster and honours them, so try it
first and fall back if the machine has no DirectShow driver for the
device. macOS and Linux use OpenCV's default backend, which is fine.
"""
from __future__ import annotations

import sys

import cv2

#: Backends to try, in order, per platform.
_BACKENDS = {
    "win32": (("DirectShow", cv2.CAP_DSHOW), ("Media Foundation", cv2.CAP_MSMF), ("default", cv2.CAP_ANY)),
}


def as_source(src: str | int) -> str | int:
    """A camera index if it looks like one, otherwise the path/URL as given."""
    if isinstance(src, int):
        return src
    try:
        return int(src)
    except (TypeError, ValueError):
        return src


def open_capture(src: str | int, width: int | None = None, height: int | None = None) -> cv2.VideoCapture:
    """An opened VideoCapture, or SystemExit with a readable message.

    Backend selection only applies to live cameras (an integer index);
    video files always go through OpenCV's default reader.
    """
    source = as_source(src)
    attempts = _BACKENDS.get(sys.platform, (("default", cv2.CAP_ANY),)) if isinstance(source, int) else ((None, None),)

    cam = None
    for name, backend in attempts:
        cam = cv2.VideoCapture(source) if backend is None else cv2.VideoCapture(source, backend)
        if cam.isOpened():
            if name not in (None, "default", "DirectShow"):
                print(f"camera: opened with the {name} backend")
            break
        cam.release()
        cam = None

    if cam is None:
        tried = ", ".join(n for n, _ in attempts if n) or "the default backend"
        raise SystemExit(
            f"could not open video source {src!r} (tried {tried}) — camera plugged in, "
            "not in use by another app, and this program allowed to use it?"
        )

    if width:
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # newest frame, not a backlog
    return cam
