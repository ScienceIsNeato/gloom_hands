"""Find the person in a frame. Two detectors, one interface:

    det = detector.detect(frame_bgr)   # -> Detection | None

* BackgroundDetector (default) — for a STATIC camera over a mostly static
  scene. OpenCV's MOG2 learns the empty background and flags whatever
  differs from it, so a person who pauses stays detected until they stand
  still for ``history`` frames. Anything big enough to matter is assumed
  to be a person (false positives are not a concern in this setting), so
  the size threshold is eager. A few milliseconds per frame.
* MotionDetector (legacy) — frame differencing from the 2024 head. Diffs
  consecutive frames, thresholds, opens and dilates the blobs, then picks
  the column with the tallest continuous run of moving pixels. Only sees
  things that MOVE right now.
* YuNetDetector ("face" when its model is present) — OpenCV's own CNN
  face detector (FaceDetectorYN), the one OpenCV recommends over cascades:
  robust to glasses, beards, and turned heads, a few ms on CPU. Needs the
  ~230 KB ONNX model from github.com/opencv/opencv_zoo (MIT), placed at
  vision/models/face_detection_yunet_2023mar.onnx or pointed
  to by $GLOOM_YUNET.
* FaceDetector ("haar") — the 2001-era Haar cascades, kept as the no-model
  fallback for "face". Flickers on anything but a well-lit frontal face.
* PersonDetector (opt-in) — OpenCV's built-in HOG pedestrian detector.
  Whole standing bodies at a distance; useless for someone seated close.

Both opt-ins cost more than motion. Run
``python -m vision.preview --bench`` on the target machine.

Both report pixel coordinates in the *analysis frame*: the captured frame
after downscaling by ``scale`` (and before any ROI crop, whose offset is
added back). Feed the Detection to ``geometry.Locator`` for angles.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    x: float          # px column of the target, analysis-frame coords
    top: float        # px row where the target starts (up)
    bottom: float     # px row where it ends (down)
    frame_w: int      # analysis-frame size
    frame_h: int
    weight: float = 1.0  # detector-specific confidence (HOG score, blob run length)
    real_height_m: float | None = None  # what height_px corresponds to in the world, if known
    width_px: float | None = None       # box width when the detector has one (face, person)
    coasting: bool = False              # set by Tracker: no detector confirmed this frame, predicted

    @property
    def box(self) -> tuple[int, int, int, int] | None:
        """(left, top, right, bottom) in analysis-frame px, or None for a column-only hit."""
        if self.width_px is None:
            return None
        half = self.width_px / 2.0
        return int(self.x - half), int(self.top), int(self.x + half), int(self.bottom)

    @property
    def height_px(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def cx_norm(self) -> float:
        """-1.0 at the left edge, 0 at center, +1.0 at the right edge."""
        return (self.x - self.frame_w / 2.0) / (self.frame_w / 2.0)


def _longest_vertical_runs(binary: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per column: length of the longest run of nonzero pixels, plus its
    start and end rows (end exclusive). Vectorized over columns."""
    h, w = binary.shape
    run = np.zeros(w, dtype=np.int32)
    best_len = np.zeros(w, dtype=np.int32)
    best_end = np.zeros(w, dtype=np.int32)
    for i in range(h):
        run = np.where(binary[i] > 0, run + 1, 0)
        better = run > best_len
        best_len = np.where(better, run, best_len)
        best_end = np.where(better, i + 1, best_end)
    return best_len, best_end - best_len, best_end


class MotionDetector:
    """Frame-difference tracker ported from the 2024 Halloween head.

    Parameters keep the original tuning: analyse at 0.6 scale, look only at
    the bottom half of the frame (``roi``), threshold 25, 8 px kernel (scaled),
    7 dilation passes, and a 10x subsample for the column scan. ``min_run``
    is the shortest vertical run (in subsampled rows, scaled) that counts.
    """

    def __init__(
        self,
        scale: float = 0.6,
        roi: tuple[float, float] = (0.5, 1.0),
        threshold: int = 25,
        kernel_px: int = 8,
        dilate_iters: int = 7,
        search_scale: float = 0.1,
        min_run: float = 7.0,
        debug: bool = False,
    ) -> None:
        self.scale = scale
        self.roi = roi
        self.threshold = threshold
        self.kernel_px = kernel_px
        self.dilate_iters = dilate_iters
        self.search_scale = search_scale
        self.min_run = min_run
        self.debug = debug
        self._prev: np.ndarray | None = None
        self.roi_y0 = 0
        self.frame_w = 0
        self.frame_h = 0
        self.last_frame: np.ndarray | None = None  # scaled, ROI-cropped BGR
        self.last: dict[str, np.ndarray] = {}      # intermediates when debug

    def reset(self) -> None:
        self._prev = None

    def _prepare(self, frame_bgr: np.ndarray) -> np.ndarray:
        if self.scale != 1.0:
            frame_bgr = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        self.frame_h, self.frame_w = frame_bgr.shape[:2]
        self.roi_y0 = int(self.frame_h * self.roi[0])
        roi_y1 = int(self.frame_h * self.roi[1])
        self.last_frame = frame_bgr[self.roi_y0:roi_y1, :]
        return cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        gray = self._prepare(frame_bgr)
        prev, self._prev = self._prev, gray
        if prev is None or prev.shape != gray.shape:
            return None

        diff = cv2.absdiff(prev, gray)
        _, thresh = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)
        k = max(1, int(self.kernel_px * self.scale))
        kernel = np.ones((k, k), np.uint8)
        morphed = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        dilated = cv2.dilate(morphed, kernel, iterations=self.dilate_iters)
        sub = cv2.resize(dilated, (0, 0), fx=self.search_scale, fy=self.search_scale)
        if self.debug:
            self.last = {"diff": diff, "thresh": thresh, "morphed": morphed, "dilated": dilated}

        lengths, starts, ends = _longest_vertical_runs(sub)
        j = int(np.argmax(lengths))
        longest = int(lengths[j])
        if longest <= self.min_run * self.scale:
            return None
        up = 1.0 / self.search_scale
        return Detection(
            x=float(j * up),
            top=float(self.roi_y0 + starts[j] * up),
            bottom=float(self.roi_y0 + ends[j] * up),
            frame_w=self.frame_w,
            frame_h=self.frame_h,
            weight=float(longest),
        )


class BackgroundDetector:
    """MOG2 background subtraction, then the largest foreground blob.

    * ``history``: frames a stationary thing survives before it is absorbed
      into the background. Counted in frames fed to ``detect``, so at the
      gloom hand's ~4.5 frames/s the default 200 is ~45 s. It cuts both
      ways: a person who stands still that long fades out, and the spot
      someone just LEFT stays flagged (a "ghost") for the same time.
      Expect a person present at startup to be baked into the background
      until they move, leaving a ghost that fades on the same schedule.
    * ``var_threshold``: MOG2's pixel sensitivity; lower = more eager.
    * ``min_area_frac``: smallest blob (as a fraction of the analysis frame)
      that counts. 0.002 is eager on purpose: in this setting a blob is a
      person, not a false positive.
    """

    def __init__(
        self,
        scale: float = 0.6,
        history: int = 200,
        var_threshold: float = 16.0,
        kernel_px: int = 8,
        dilate_iters: int = 3,
        min_area_frac: float = 0.002,
        person_height_m: float = 1.7,
        debug: bool = False,
    ) -> None:
        self.scale = scale
        self.history = history
        self.var_threshold = var_threshold
        self.kernel_px = kernel_px
        self.dilate_iters = dilate_iters
        self.min_area_frac = min_area_frac
        self.person_height_m = person_height_m
        self.debug = debug
        self._sub = None
        self.roi_y0 = 0
        self.frame_w = 0
        self.frame_h = 0
        self.last_frame: np.ndarray | None = None
        self.last: dict[str, np.ndarray] = {}
        self.reset()

    def reset(self) -> None:
        self._sub = cv2.createBackgroundSubtractorMOG2(
            history=self.history, varThreshold=self.var_threshold, detectShadows=False
        )
        self._n = 0

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        if self.scale != 1.0:
            frame_bgr = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        self.frame_h, self.frame_w = frame_bgr.shape[:2]
        self.last_frame = frame_bgr
        mask = self._sub.apply(frame_bgr)
        self._n += 1
        if self._n < 3:
            return None  # let the model see the scene before believing it
        k = max(1, int(self.kernel_px * self.scale))
        kernel = np.ones((k, k), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=self.dilate_iters)
        if self.debug:
            self.last = {"mask": mask}
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if count < 2:
            return None
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < self.min_area_frac * self.frame_w * self.frame_h:
            return None
        x, y, w, h = (float(stats[i, c]) for c in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                                    cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        return Detection(
            x=x + w / 2.0, top=y, bottom=y + h,
            frame_w=self.frame_w, frame_h=self.frame_h, weight=float(area),
            real_height_m=self.person_height_m, width_px=w,
        )


class FaceDetector:
    """OpenCV's built-in Haar cascades. Tries the frontal cascade first
    (``alt2``, more tolerant of glasses and slight turns than ``default``),
    then the profile cascade on the frame and its mirror, so a head turned
    either way still counts. Returns the largest face.

    ``face_height_m`` is what the box spans in the world, roughly eyebrows
    to chin: ~0.18 m on an adult; it sets the distance estimate.
    ``min_face_px`` rejects specks. Lower ``min_neighbors`` (3) is more
    eager, higher (5+) is stricter."""

    def __init__(
        self,
        scale: float = 0.5,
        face_height_m: float = 0.18,
        min_face_px: int = 24,
        scale_factor: float = 1.08,
        min_neighbors: int = 3,
        profile: bool = True,
        cascade: str = "haarcascade_frontalface_alt2.xml",
        debug: bool = False,
    ) -> None:
        data = getattr(cv2, "data", None)
        if data is None:
            raise RuntimeError(
                "cv2.data missing: install opencv-python>=4.5,<5 (5.0 wheels dropped the cascades)"
            )
        self._frontal = cv2.CascadeClassifier(data.haarcascades + cascade)
        if self._frontal.empty():
            raise RuntimeError(f"Haar cascade not found: {data.haarcascades + cascade}")
        self._profile = cv2.CascadeClassifier(data.haarcascades + "haarcascade_profileface.xml") if profile else None
        if self._profile is not None and self._profile.empty():
            self._profile = None
        self.scale = scale
        self.face_height_m = face_height_m
        self.min_face_px = min_face_px
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors
        self.debug = debug
        self.roi_y0 = 0
        self.frame_w = 0
        self.frame_h = 0
        self.last_frame: np.ndarray | None = None
        self.last: dict[str, np.ndarray] = {}
        self.last_source = ""  # which cascade produced the last hit

    def reset(self) -> None:
        pass

    def _run(self, cascade, gray: np.ndarray):  # noqa: ANN001, ANN202
        return cascade.detectMultiScale(
            gray, scaleFactor=self.scale_factor, minNeighbors=self.min_neighbors,
            minSize=(self.min_face_px, self.min_face_px),
        )

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        if self.scale != 1.0:
            frame_bgr = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        self.frame_h, self.frame_w = frame_bgr.shape[:2]
        self.last_frame = frame_bgr
        gray = cv2.equalizeHist(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY))

        faces = self._run(self._frontal, gray)
        self.last_source = "frontal"
        if len(faces) == 0 and self._profile is not None:
            faces = self._run(self._profile, gray)
            self.last_source = "profile"
            if len(faces) == 0:
                flipped = self._run(self._profile, cv2.flip(gray, 1))
                if len(flipped):
                    faces = [(self.frame_w - x - w, y, w, h) for x, y, w, h in flipped]
                    self.last_source = "profile-mirrored"
        if len(faces) == 0:
            return None
        x, y, w, h = (float(v) for v in max(faces, key=lambda r: r[2] * r[3]))
        return Detection(
            x=x + w / 2.0, top=y, bottom=y + h,
            frame_w=self.frame_w, frame_h=self.frame_h, weight=w * h,
            real_height_m=self.face_height_m, width_px=w,
        )


YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET_MODEL


def yunet_model_path() -> str | None:
    """Where the YuNet model is, or None. Checks $GLOOM_YUNET,
    then the package's models/ directory."""
    import os
    from pathlib import Path

    for cand in (os.environ.get("GLOOM_YUNET", ""), Path(__file__).with_name("models") / YUNET_MODEL):
        if cand and Path(cand).is_file():
            return str(cand)
    return None


class YuNetDetector:
    """cv2.FaceDetectorYN (YuNet). Returns the highest-scoring face.
    ``score_threshold`` 0.6 is eager on purpose for a static room; raise
    it if it ever locks onto a poster. ``face_height_m`` is what its box
    spans, roughly hairline to chin: ~0.2 m on an adult."""

    def __init__(
        self,
        scale: float = 0.5,
        model: str | None = None,
        score_threshold: float = 0.6,
        nms_threshold: float = 0.3,
        face_height_m: float = 0.2,
        debug: bool = False,
    ) -> None:
        if not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("cv2.FaceDetectorYN missing: needs opencv-python >= 4.5.4")
        model = model or yunet_model_path()
        if not model:
            raise RuntimeError(
                f"YuNet model not found. Download {YUNET_URL} (MIT, ~230 KB) to "
                f"vision/models/{YUNET_MODEL} or set $GLOOM_YUNET"
            )
        self.scale = scale
        self.face_height_m = face_height_m
        self.debug = debug
        self._net = cv2.FaceDetectorYN.create(model, "", (320, 320), score_threshold, nms_threshold, 5000)
        self._size = (320, 320)
        self.roi_y0 = 0
        self.frame_w = 0
        self.frame_h = 0
        self.last_frame: np.ndarray | None = None
        self.last: dict[str, np.ndarray] = {}

    def reset(self) -> None:
        pass

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        if self.scale != 1.0:
            frame_bgr = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        self.frame_h, self.frame_w = frame_bgr.shape[:2]
        self.last_frame = frame_bgr
        if (self.frame_w, self.frame_h) != self._size:
            self._size = (self.frame_w, self.frame_h)
            self._net.setInputSize(self._size)
        _, faces = self._net.detect(frame_bgr)
        if faces is None or len(faces) == 0:
            return None
        best = faces[int(np.argmax(faces[:, 14]))]
        x, y, w, h, score = (float(v) for v in (best[0], best[1], best[2], best[3], best[14]))
        return Detection(
            x=x + w / 2.0, top=y, bottom=y + h,
            frame_w=self.frame_w, frame_h=self.frame_h, weight=score,
            real_height_m=self.face_height_m, width_px=w,
        )


class PersonDetector:
    """OpenCV's built-in HOG + linear SVM pedestrian detector (needs
    opencv-python 4.x; the 5.0 wheels dropped HOGDescriptor).

    Returns the highest-scoring box. Slow on small computers: the 64x128
    window is slid over an image pyramid, so cost grows fast with
    resolution. ``scale`` sets the analysis size; 0.5 on a 1280x720
    capture is a reasonable start.
    """

    def __init__(
        self,
        scale: float = 0.5,
        win_stride: tuple[int, int] = (8, 8),
        padding: tuple[int, int] = (8, 8),
        pyramid_scale: float = 1.05,
        min_weight: float = 0.3,
        person_height_m: float = 1.7,
        debug: bool = False,
    ) -> None:
        if not hasattr(cv2, "HOGDescriptor"):
            raise RuntimeError(
                "cv2.HOGDescriptor missing: install opencv-python>=4.5,<5 for the person detector"
            )
        self.scale = scale
        self.win_stride = win_stride
        self.padding = padding
        self.pyramid_scale = pyramid_scale
        self.min_weight = min_weight
        self.person_height_m = person_height_m
        self.debug = debug
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.roi_y0 = 0
        self.frame_w = 0
        self.frame_h = 0
        self.last_frame: np.ndarray | None = None
        self.last: dict[str, np.ndarray] = {}

    def reset(self) -> None:
        pass

    def detect(self, frame_bgr: np.ndarray) -> Detection | None:
        if self.scale != 1.0:
            frame_bgr = cv2.resize(frame_bgr, (0, 0), fx=self.scale, fy=self.scale)
        self.frame_h, self.frame_w = frame_bgr.shape[:2]
        self.last_frame = frame_bgr
        rects, weights = self._hog.detectMultiScale(
            frame_bgr, winStride=self.win_stride, padding=self.padding, scale=self.pyramid_scale
        )
        if len(rects) == 0:
            return None
        weights = np.asarray(weights).reshape(-1)
        i = int(np.argmax(weights))
        if weights[i] < self.min_weight:
            return None
        x, y, w, h = (float(v) for v in rects[i])
        return Detection(
            x=x + w / 2.0, top=y, bottom=y + h,
            frame_w=self.frame_w, frame_h=self.frame_h, weight=float(weights[i]),
            real_height_m=self.person_height_m, width_px=w,
        )


DETECTORS = {
    "background": BackgroundDetector,
    "motion": MotionDetector,
    "face": None,  # resolved in make_detector: YuNet if its model is present, else Haar
    "yunet": YuNetDetector,
    "haar": FaceDetector,
    "person": PersonDetector,
}

_warned_haar = False


def make_detector(kind: str, **kwargs):  # noqa: ANN201 - factory over all detector classes
    global _warned_haar
    if kind == "face":
        if yunet_model_path():
            return YuNetDetector(**kwargs)
        if not _warned_haar:
            _warned_haar = True
            print(f"[vision] 'face' is using Haar cascades; for the far better YuNet detector "
                  f"download {YUNET_URL} to vision/models/{YUNET_MODEL}")
        return FaceDetector(**kwargs)
    try:
        cls = DETECTORS[kind]
    except KeyError:
        raise ValueError(f"unknown detector {kind!r}; choose from {list(DETECTORS)}") from None
    return cls(**kwargs)
