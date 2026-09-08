"""Where is the person, relative to the thing that has to turn toward them?

The camera does not have to sit on the actuator. Describe its pose in the
actuator's frame and ``Locator`` does the trig:

    top-down view, actuator pivot at the origin

              +y (left)
               ^
               |      * person
               |     /
        cam ---+--> +x (forward = the actuator's zero heading)
      (x_m, y_m, yaw_deg)

* ``x_m``   metres in FRONT of the pivot (negative = behind)
* ``y_m``   metres to the LEFT of the pivot (negative = right)
* ``yaw_deg`` where the camera's optical axis points, relative to the
  actuator's zero heading; positive = turned left (counter-clockwise).
* ``hfov_deg`` the camera's horizontal field of view.

Angles everywhere are degrees, counter-clockwise positive (left = +).

A single camera gives a bearing but no range, and the offset only matters
once you know the range. It is estimated from the target's height in
pixels (pinhole: range = f * H / h_px). H is what the detector says its
box spans (a 1.7 m body, a 0.18 m face), or ``person_height_m`` when it
does not know. For the motion detector the "height" is the moving blob, so
it is rough; the
estimate is clamped and falls back to ``default_range_m`` when the blob
is too small to trust. With zero offset the range drops out entirely.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .detector import Detection


@dataclass(frozen=True)
class CameraPose:
    x_m: float = 0.0
    y_m: float = 0.0
    yaw_deg: float = 0.0
    hfov_deg: float = 60.0
    mirrored: bool = False   # image is left-right flipped (selfie-style)


@dataclass(frozen=True)
class Observation:
    """What the video side ultimately reports: where the person is
    relative to the CAMERA. Angle is degrees off the optical axis (left
    positive); distance is metres, estimated from apparent size."""
    angle_deg: float
    distance_m: float
    distance_known: bool   # False when default_range_m had to be used
    height_px: float       # the apparent size the distance came from


@dataclass(frozen=True)
class Target:
    bearing_deg: float       # from the actuator pivot, CCW positive
    range_m: float           # from the actuator pivot
    cam_bearing_deg: float   # from the camera's optical axis
    cam_range_m: float       # from the camera
    range_known: bool        # False when default_range_m was used
    x_m: float               # position in the actuator frame
    y_m: float


def focal_px(frame_w: int, hfov_deg: float) -> float:
    return (frame_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


class Locator:
    def __init__(
        self,
        pose: CameraPose,
        person_height_m: float = 1.7,
        default_range_m: float = 3.0,
        min_height_px: int = 20,
        range_clamp_m: tuple[float, float] = (0.4, 10.0),
    ) -> None:
        self.pose = pose
        self.person_height_m = person_height_m
        self.default_range_m = default_range_m
        self.min_height_px = min_height_px
        self.range_clamp_m = range_clamp_m

    def cam_bearing_deg(self, det: Detection) -> float:
        f = focal_px(det.frame_w, self.pose.hfov_deg)
        dx = det.x - det.frame_w / 2.0
        if self.pose.mirrored:
            dx = -dx
        # pixels increase to the right; bearings are positive to the left
        return -math.degrees(math.atan2(dx, f))

    def cam_range_m(self, det: Detection) -> tuple[float, bool]:
        if det.height_px < self.min_height_px:
            return self.default_range_m, False
        f = focal_px(det.frame_w, self.pose.hfov_deg)
        real = det.real_height_m if det.real_height_m else self.person_height_m
        r = f * real / det.height_px
        lo, hi = self.range_clamp_m
        return max(lo, min(hi, r)), True

    def observe(self, det: Detection) -> Observation:
        """Distance and angle relative to the camera — the video piece's output."""
        r, known = self.cam_range_m(det)
        return Observation(self.cam_bearing_deg(det), r, known, det.height_px)

    def locate(self, det: Detection) -> Target:
        obs = self.observe(det)
        beta, r_cam, known = obs.angle_deg, obs.distance_m, obs.distance_known
        # person in the camera frame (camera looks along its +x)
        cx = r_cam * math.cos(math.radians(beta))
        cy = r_cam * math.sin(math.radians(beta))
        # rotate into the actuator frame by the camera's yaw, then translate
        yaw = math.radians(self.pose.yaw_deg)
        px = cx * math.cos(yaw) - cy * math.sin(yaw) + self.pose.x_m
        py = cx * math.sin(yaw) + cy * math.cos(yaw) + self.pose.y_m
        return Target(
            bearing_deg=math.degrees(math.atan2(py, px)),
            range_m=math.hypot(px, py),
            cam_bearing_deg=beta,
            cam_range_m=r_cam,
            range_known=known,
            x_m=px,
            y_m=py,
        )
