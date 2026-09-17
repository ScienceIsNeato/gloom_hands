"""The arm as a hand position, instead of a pile of joint angles.

Everything until now commanded angles and let the hand go wherever those put
it — which is how the shoulder swinging 110 degrees ended up throwing the
gripper from the table top to straight overhead. To keep the hand steady you
have to ask for the hand, not the joints.

Three joints move in the arm's vertical plane: shoulder, elbow, wrist. Three
numbers describe where the hand is and how it is pointing: reach, height, and
pitch. Three and three, so there is exactly one answer — no choices to make,
and nothing left over to wobble.

    reach   forward from the shoulder pivot
    height  above the shoulder pivot (this is the one to hold steady)
    pitch   the hand's angle, shoulder + elbow + wrist

LENGTHS ARE ESTIMATED FROM VIDEO. Only their ratio matters, so the units
cancel and any consistent measure will do. If the hand drifts up or down as
it reaches in and out, these three numbers are why — measure them with a
ruler, pivot to pivot, and the drift goes away.
"""
from __future__ import annotations

import math

# Measured on the arm, pivot to pivot, in inches. Only the ratios matter.
L_UPPER = 4.25    # shoulder pivot -> elbow pivot
L_FORE = 4.00     # elbow pivot -> wrist pivot
L_HAND = 4.00     # wrist pivot -> the middle of the gripper

#: With every joint at zero the arm stands straight up, so the first link
#: starts 90 degrees from forward.
UP_DEG = 90.0

#: Which way a positive angle turns each joint: +1 forward, -1 backward.
#: NOT yet confirmed on the hardware — calibrate.py answers this, and until
#: it does, nothing here should be driving the arm.
SIGN_SHOULDER = -1.0
SIGN_ELBOW = 1.0
SIGN_WRIST = 1.0


def link_angles(shoulder: float, elbow: float, wrist: float) -> tuple[float, float, float]:
    """Each link's angle from forward, measuring up as positive.

    Every joint is relative to the link before it, and each carries its own
    turning direction, so the servo numbers cannot simply be added.
    """
    a = UP_DEG + SIGN_SHOULDER * shoulder
    b = a + SIGN_ELBOW * elbow
    c = b + SIGN_WRIST * wrist
    return a, b, c


def hand_at(shoulder: float, elbow: float, wrist: float) -> tuple[float, float, float]:
    """Where the hand is: (reach forward, height above the shoulder, pitch)."""
    a, b, c = (math.radians(v) for v in link_angles(shoulder, elbow, wrist))
    reach = L_UPPER * math.cos(a) + L_FORE * math.cos(b) + L_HAND * math.cos(c)
    height = L_UPPER * math.sin(a) + L_FORE * math.sin(b) + L_HAND * math.sin(c)
    return reach, height, math.degrees(c)


def arm_for(reach: float, height: float, pitch: float) -> tuple[float, float, float] | None:
    """The three angles that put the hand there, or None if it cannot.

    The hand's pitch fixes where the wrist pivot has to be, which leaves an
    ordinary two-link problem for the shoulder and elbow. The elbow-up
    solution is chosen, matching the posture the arm already holds.
    """
    p = math.radians(pitch)
    wx = reach - L_HAND * math.cos(p)
    wz = height - L_HAND * math.sin(p)
    d2 = wx * wx + wz * wz
    d = math.sqrt(d2)
    if d > (L_UPPER + L_FORE) or d < abs(L_UPPER - L_FORE) or d == 0:
        return None
    cos_e = (d2 - L_UPPER * L_UPPER - L_FORE * L_FORE) / (2 * L_UPPER * L_FORE)
    # elbow-forward: the branch that keeps the arm reaching rather than folded
    rel_elbow = math.acos(max(-1.0, min(1.0, cos_e)))
    link_a = math.atan2(wz, wx) + math.atan2(
        L_FORE * math.sin(rel_elbow), L_UPPER + L_FORE * math.cos(rel_elbow)
    )
    link_b = link_a - rel_elbow
    # back out of link angles into servo angles, undoing each joint's direction
    shoulder = (math.degrees(link_a) - UP_DEG) / SIGN_SHOULDER
    elbow = (math.degrees(link_b) - math.degrees(link_a)) / SIGN_ELBOW
    wrist = (pitch - math.degrees(link_b)) / SIGN_WRIST
    return shoulder, elbow, wrist


def reachable(reach: float, height: float, pitch: float) -> bool:
    return arm_for(reach, height, pitch) is not None
