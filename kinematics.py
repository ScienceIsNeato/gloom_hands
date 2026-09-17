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

L_UPPER = 145.0   # shoulder pivot -> elbow pivot
L_FORE = 140.0    # elbow pivot -> wrist pivot
L_HAND = 240.0    # wrist pivot -> the middle of the gripper


def hand_at(shoulder: float, elbow: float, wrist: float) -> tuple[float, float, float]:
    """Where the hand is, given the three angles: (reach, height, pitch).

    Angles are absolute along the chain, which is the convention the poses
    already use: the hand's pitch is simply the three of them added up.
    """
    a = math.radians(shoulder)
    b = a + math.radians(elbow)
    c = b + math.radians(wrist)
    reach = L_UPPER * math.cos(a) + L_FORE * math.cos(b) + L_HAND * math.cos(c)
    height = L_UPPER * math.sin(a) + L_FORE * math.sin(b) + L_HAND * math.sin(c)
    return reach, height, shoulder + elbow + wrist


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
    elbow = -math.acos(max(-1.0, min(1.0, cos_e)))      # elbow-up
    shoulder = math.atan2(wz, wx) - math.atan2(
        L_FORE * math.sin(elbow), L_UPPER + L_FORE * math.cos(elbow)
    )
    return (math.degrees(shoulder), math.degrees(elbow),
            pitch - math.degrees(shoulder) - math.degrees(elbow))


def reachable(reach: float, height: float, pitch: float) -> bool:
    return arm_for(reach, height, pitch) is not None
