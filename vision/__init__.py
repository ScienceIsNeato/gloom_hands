"""vision — the gloom hand's eyes: find a person with a webcam and say
where they are (distance and angle from the camera, then the bearing
from the arm's base pivot).

The frame-differencing and column-scan algorithm was ported from the
2024 HalloweenTracker head (github.com/ScienceIsNeato/HalloweenTracker);
everything else was written for this project.

    from vision import MotionDetector, CameraPose, Locator, Smoother

    det = BackgroundDetector()   # static camera; MotionDetector / FaceDetector / PersonDetector too
    loc = Locator(CameraPose(x_m=0.2, y_m=-0.1, yaw_deg=0, hfov_deg=60))
    ...
    d = det.detect(frame)
    if d:
        obs = loc.observe(d)       # obs.angle_deg, obs.distance_m  (relative to the camera)
        target = loc.locate(d)     # target.bearing_deg, target.range_m (relative to the actuator)
"""
from .detector import (
    DETECTORS, BackgroundDetector, Detection, FaceDetector, MotionDetector, PersonDetector, YuNetDetector,
    make_detector, yunet_model_path,
)
from .filters import Smoother
from .tracking import Tracker, make_tracked
from .geometry import CameraPose, Locator, Observation, Target, focal_px

__all__ = [
    "DETECTORS", "BackgroundDetector", "Detection", "FaceDetector", "MotionDetector", "PersonDetector",
    "YuNetDetector", "yunet_model_path",
    "make_detector", "Tracker", "make_tracked",
    "Smoother", "CameraPose", "Locator", "Observation", "Target", "focal_px",
]
__version__ = "0.2.0"
