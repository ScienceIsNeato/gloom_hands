"""Degrees in, servo units out — the one conversion authority.

The xArm 1S bus servos travel 240 degrees across their 0-1000 unit range
(4.1667 units/degree). This module speaks CENTERED degrees: 0.0 = the
servo's midpoint (unit 500), positive = toward unit 1000, so the usable
span is -120.0 .. +120.0. Every script converts here and ships raw units
to the transport, on either USB or BLE, so both paths agree exactly.

Soft limits default to +/-108 deg (units 50-950), staying off the hard
stops; tighten per joint in LIMITS_DEG once you know the arm's real
clearances (e.g. the elbow usually cannot use its full range without
meeting the base).
"""
from __future__ import annotations

UNITS_PER_DEGREE = 1000.0 / 240.0
CENTER_UNITS = 500

NAMES = {1: "gripper", 2: "wrist roll", 3: "wrist bend", 4: "elbow", 5: "shoulder", 6: "base"}

#: Per-servo soft limits in centered degrees (lo, hi).
LIMITS_DEG: dict[int, tuple[float, float]] = {sid: (-108.0, 108.0) for sid in range(1, 7)}


def deg_to_units(deg: float) -> int:
    """Centered degrees -> servo units (unclamped except electrical range)."""
    return int(round(max(0.0, min(1000.0, CENTER_UNITS + deg * UNITS_PER_DEGREE))))


def units_to_deg(units: int) -> float:
    return (units - CENTER_UNITS) / UNITS_PER_DEGREE


def servo_units(sid: int, deg: float) -> int:
    """Convert with that servo's soft limits applied. Use this one."""
    lo, hi = LIMITS_DEG.get(sid, (-108.0, 108.0))
    return deg_to_units(max(lo, min(hi, deg)))


def clamp_deg(sid: int, deg: float) -> float:
    lo, hi = LIMITS_DEG.get(sid, (-108.0, 108.0))
    return max(lo, min(hi, deg))
