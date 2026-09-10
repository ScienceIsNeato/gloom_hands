#!/usr/bin/env python3
"""See what the tracker sees, and time it — no servo required.

    ./eyes.py                # background detector, window
    ./eyes.py --detector face
    ./eyes.py --cam-x 0.3 --cam-y -0.2 --cam-yaw 15 --hfov 70
    ./eyes.py --bench 60     # ms/frame, both detectors, no window

Keys in the window:  d  switch detector (background -> motion -> face -> person)   s  snapshot   ESC  quit

Detections are fed through ``Tracker`` (red = confirmed this frame, yellow
= coasting on the last known motion; ``--raw`` shows bare detections).
The window draws the detection (column + vertical extent, or a box)
and prints the video piece's output — distance and angle relative to the
camera — followed by the bearing from the actuator pivot after the pose
offset. Use it to dial in --hfov (a known object should sit at the right
angle), --mirrored, and the pose.

``--bench`` is for the small computer: it captures N frames and reports
milliseconds per frame for each detector so the choice is measured, not
guessed. Works with opencv-python-headless (no window needed).
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2

from .capture import open_capture
from .detector import DETECTORS, Detection, make_detector
from .geometry import CameraPose, Locator, Target
from .tracking import make_tracked


def open_camera(src: str, width: int, height: int) -> cv2.VideoCapture:
    return open_capture(src, width, height)


def read(cam: cv2.VideoCapture, upside_down: bool):  # noqa: ANN201
    ok, frame = cam.read()
    if not ok:
        return None
    return cv2.flip(frame, -1) if upside_down else frame


def draw(frame, det: Detection | None, target: Target | None, roi_y0: int, ms: float, label: str) -> None:  # noqa: ANN001
    """Overlay the detection and the numbers onto ``frame`` (the detector's
    analysis frame). Shared with gloom.py --show."""
    h, w = frame.shape[:2]
    cv2.line(frame, (w // 2, 0), (w // 2, h), (90, 90, 90), 1)
    if det is not None and target is not None:
        x = int(det.x)
        top, bot = int(det.top - roi_y0), int(det.bottom - roi_y0)
        color = (0, 220, 255) if det.coasting else (0, 0, 255)  # yellow = coasting, red = confirmed
        box = det.box
        if box is not None:
            l, _, r, _ = box
            cv2.rectangle(frame, (l, top), (r, bot), color, 2)
            cv2.circle(frame, (x, (top + bot) // 2), 4, color, -1)
        else:
            cv2.line(frame, (x, top), (x, bot), color, 3)
            cv2.line(frame, (x - 15, top), (x + 15, top), color, 2)
            cv2.line(frame, (x - 15, bot), (x + 15, bot), color, 2)
        dist = f"{target.cam_range_m:.1f} m" + ("" if target.range_known else " (default)")
        state = f"coasting {det.weight:.0%}" if det.coasting else "confirmed"
        lines = [
            f"camera: {dist} at {target.cam_bearing_deg:+6.1f} deg   (h {det.height_px:.0f} px)  {state}",
            f"pivot:  {target.range_m:.1f} m at {target.bearing_deg:+6.1f} deg",
        ]
    else:
        lines = ["no target"]
    lines.append(f"{label}  {ms:5.1f} ms/frame   [d] switch  [s] snapshot  [esc] quit")
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (16, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)


def bench(cam: cv2.VideoCapture, frames: int, upside_down: bool, scale_kw: dict) -> int:
    print(f"benchmarking {frames} frames per detector on {sys.platform}...")
    for kind in [k for k in DETECTORS if k != "face"]:
        try:
            det = make_detector(kind, **scale_kw.get(kind, {}))
        except RuntimeError as e:
            print(f"  {kind:7s}: unavailable ({e})")
            continue
        det.reset()
        hits = 0
        t_det = 0.0
        for _ in range(frames):
            frame = read(cam, upside_down)
            if frame is None:
                break
            t0 = time.perf_counter()
            if det.detect(frame) is not None:
                hits += 1
            t_det += time.perf_counter() - t0
        print(f"  {kind:7s}: {1000 * t_det / max(1, frames):7.1f} ms/frame   ({hits}/{frames} frames with a target)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video-src", default="0", help="camera index or video file (default 0)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--upside-down", action="store_true", help="camera is mounted upside down")
    p.add_argument("--detector", choices=list(DETECTORS), default="background",
                   help="starting detector; press d in the window to switch")
    p.add_argument("--scale", type=float, default=None, help="analysis scale (default: detector's own)")
    p.add_argument("--roi-top", type=float, default=0.0,
                   help="motion detector: ignore rows above this fraction of the frame (head used 0.5)")
    p.add_argument("--hfov", type=float, default=60.0, help="camera horizontal field of view, degrees")
    p.add_argument("--mirrored", action="store_true", help="image is left-right flipped")
    p.add_argument("--cam-x", type=float, default=0.0, help="camera metres in front of the pivot")
    p.add_argument("--cam-y", type=float, default=0.0, help="camera metres to the left of the pivot")
    p.add_argument("--cam-yaw", type=float, default=0.0, help="camera heading vs pivot zero, deg, left +")
    p.add_argument("--person-height", type=float, default=1.7, help="metres, for the distance estimate")
    p.add_argument("--raw", action="store_true", help="show raw per-frame detections, no tracking")
    p.add_argument("--coast", type=float, default=1.5, help="seconds a track survives unconfirmed")
    p.add_argument("--bench", type=int, metavar="N", help="time both detectors over N frames and exit")
    a = p.parse_args(argv)

    cam = open_camera(a.video_src, a.width, a.height)
    time.sleep(0.5)
    scale_kw = {k: {} for k in DETECTORS}
    scale_kw["motion"] = {"roi": (a.roi_top, 1.0)}
    if a.scale is not None:
        for kw in scale_kw.values():
            kw["scale"] = a.scale
    try:
        if a.bench:
            return bench(cam, a.bench, a.upside_down, scale_kw)

        kinds = ["background", "motion", "face", "person"]  # d cycles these; --detector may name any
        kind = a.detector

        def build(kind: str):  # noqa: ANN202
            if a.raw:
                return make_detector(kind, **scale_kw[kind])
            return make_tracked(kind, scale_kw, coast_s=a.coast)

        det = build(kind)
        loc = Locator(
            CameraPose(a.cam_x, a.cam_y, a.cam_yaw, a.hfov, a.mirrored), person_height_m=a.person_height
        )
        snaps = 0
        print(f"detector: {kind}.  d to switch, s to save a snapshot, ESC to quit")
        while True:
            frame = read(cam, a.upside_down)
            if frame is None:
                print("end of stream")
                break
            t0 = time.perf_counter()
            d = det.detect(frame)
            ms = 1000 * (time.perf_counter() - t0)
            target = loc.locate(d) if d else None
            view = det.last_frame.copy()
            label = kind if a.raw else f"{kind}+track" + ("+bg" if kind in ("face", "person") else "")
            draw(view, d, target, det.roi_y0, ms, label)
            try:
                cv2.imshow("gloom eyes", view)
            except cv2.error:
                raise SystemExit(
                    "no GUI in this OpenCV build (headless?) — use --bench, or install opencv-python"
                ) from None
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key == ord("s"):
                snaps += 1
                path = f"eyes_snapshot_{snaps}.png"
                cv2.imwrite(path, frame)  # the raw capture, so detectors can be re-run on it
                print(f"saved {path} ({frame.shape[1]}x{frame.shape[0]}); "
                      f"detector {kind}: {'hit' if d else 'no target'}")
            if key == ord("d"):
                # yunet/haar are flavours of "face" for cycling purposes
                slot = kinds.index(kind if kind in kinds else "face")
                for step in range(1, len(kinds) + 1):
                    kind = kinds[(slot + step) % len(kinds)]
                    try:
                        det = build(kind)  # keeps the tracker unless --raw
                        break
                    except RuntimeError as e:
                        print(f"cannot switch to {kind}: {e}")
                det.reset()
                print(f"detector: {kind}")
        return 0
    finally:
        cam.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
