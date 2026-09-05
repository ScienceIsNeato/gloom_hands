#!/usr/bin/env python3
"""GLOOM HAND DEMO — fully standalone. Auto-connects to the xArm over
Bluetooth and starts hunting. Ctrl-C releases the victim (calm exit).

    ./gloom_hand_demo.py

Single file on purpose: BLE transport, LOBOT protocol, degree conversion,
soft limits, and the animation all live here, so it can be copied to any
machine that has Python 3.10+ and `pip install bleak`. No other files
from this folder are needed.

Requirements: arm powered from its own supply and switched ON; the phone
app force-quit (the board accepts exactly one Bluetooth connection);
terminal granted Bluetooth permission on macOS.

Servo map: 1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
Angles are centered degrees: 0.0 = servo midpoint, ±120 span.
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, "bin", "python")
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import asyncio
import math
import random
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

# ================= tune the creature here ============================ #
POSE_POINT_DEG = {5: 36.0, 4: -48.0, 3: 12.0, 2: 0.0, 1: 12.0}  # aimed at you
POSE_REST_DEG = {sid: 0.0 for sid in range(1, 7)}

SWEEP_LO_DEG, SWEEP_HI_DEG = -53.0, 53.0  # base search range
SWEEP_PERIOD = 14.0                       # seconds per full pass
TICK = 0.22                               # seconds between packets

GROPE_DEPTH = 22.0   # gripper flex (deg)
ROLL_DEPTH = 14.0    # wrist roll writhe
NOD_DEPTH = 11.0     # wrist bend nods

FREEZE_CHANCE = 0.012   # per tick: freeze mid-sweep...
FREEZE_S = (0.8, 2.2)   # ...for this long
SNAP_CHANCE = 0.010     # per tick: sudden re-aim
SNAP_MS = 280

SOFT_LIMIT_DEG = 108.0  # stay off the hard stops
# ===================================================================== #

NAME_HINTS = ("xarm", "hiwonder", "lobot")
SERVICE_HINT = "ffe0"
CMD_SERVO_MOVE = 0x03
UNITS_PER_DEGREE = 1000.0 / 240.0
ADDRESS_CACHE = Path(__file__).with_name(".xarm_ble_address")


def deg_to_units(deg: float) -> int:
    deg = max(-SOFT_LIMIT_DEG, min(SOFT_LIMIT_DEG, deg))
    return int(round(500 + deg * UNITS_PER_DEGREE))


def packet(moves_deg: dict[int, float], duration_ms: int) -> bytes:
    params = [len(moves_deg), duration_ms & 0xFF, (duration_ms >> 8) & 0xFF]
    for sid, deg in moves_deg.items():
        pos = deg_to_units(deg)
        params += [sid, pos & 0xFF, (pos >> 8) & 0xFF]
    return bytes([0x55, 0x55, len(params) + 2, CMD_SERVO_MOVE] + params)


async def connect() -> tuple[BleakClient, object]:
    device = None
    if ADDRESS_CACHE.exists():
        addr = ADDRESS_CACHE.read_text().strip()
        if addr:
            device = await BleakScanner.find_device_by_address(addr, timeout=4.0)
    if device is None:
        device = await BleakScanner.find_device_by_filter(
            lambda d, _ad: bool(d.name and any(h in d.name.lower() for h in NAME_HINTS)),
            timeout=12.0,
        )
    if device is None:
        raise SystemExit(
            "no arm found on Bluetooth — power switch ON? phone app force-quit? "
            "terminal granted Bluetooth permission?"
        )
    ADDRESS_CACHE.write_text(device.address)
    client = BleakClient(device)
    await client.connect()
    char = None
    for service in client.services:
        if SERVICE_HINT in service.uuid.lower():
            for ch in service.characteristics:
                if "write" in ch.properties or "write-without-response" in ch.properties:
                    char = ch
                    break
    if char is None:
        await client.disconnect()
        raise SystemExit(f"connected but found no writable {SERVICE_HINT} characteristic")
    print(f"connected: {device.name} ({device.address})")
    return client, char


async def hunt(client: BleakClient, char: object) -> None:
    async def send(moves_deg: dict[int, float], dur_ms: int) -> None:
        await client.write_gatt_char(char, packet(moves_deg, dur_ms), response=False)

    print("assuming the pose...")
    await send({**POSE_POINT_DEG, 6: (SWEEP_LO_DEG + SWEEP_HI_DEG) / 2}, 2500)
    await asyncio.sleep(2.7)
    print("hunting. Ctrl-C to release the victim.")

    t0 = time.monotonic()
    heading_offset = 0.0
    try:
        while True:
            now = time.monotonic() - t0

            if random.random() < FREEZE_CHANCE:
                await asyncio.sleep(random.uniform(*FREEZE_S))  # ...it heard something
                continue

            phase = 2 * math.pi * (now / SWEEP_PERIOD) + heading_offset
            sweep = 0.5 * (1 + math.sin(phase + 0.35 * math.sin(2.7 * now)))
            base = SWEEP_LO_DEG + sweep * (SWEEP_HI_DEG - SWEEP_LO_DEG)

            if random.random() < SNAP_CHANCE:
                heading_offset += random.uniform(-2.2, 2.2)
                await send({6: base}, SNAP_MS)  # violent re-aim
                await asyncio.sleep(SNAP_MS / 1000 + 0.1)
                continue

            await send(
                {
                    6: base,
                    1: POSE_POINT_DEG[1]
                    + GROPE_DEPTH * math.sin(1.9 * now + 1.0)
                    + 6.0 * math.sin(6.3 * now),
                    2: POSE_POINT_DEG[2] + ROLL_DEPTH * math.sin(0.7 * now),
                    3: POSE_POINT_DEG[3] + NOD_DEPTH * math.sin(1.3 * now + 2.1),
                },
                int(TICK * 1000) + 80,
            )
            await asyncio.sleep(TICK)
    finally:
        print("\nreleasing...")
        await send(POSE_REST_DEG, 2500)
        await asyncio.sleep(2.7)
        await client.disconnect()


async def main() -> None:
    client, char = await connect()
    await hunt(client, char)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
