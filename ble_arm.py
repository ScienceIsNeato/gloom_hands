"""Wireless (Bluetooth LE) control of the Hiwonder xArm 1S.

The controller board advertises as "Hiwonder" and exposes a vendor UART-style
BLE service (0xFFE0). It accepts the same LOBOT protocol packets as USB:

    0x55 0x55 <len> <cmd> <params...>
    cmd 0x03 = CMD_SERVO_MOVE:
        [count, dur_lo, dur_hi, (servo_id, pos_lo, pos_hi) * count]

Positions are xArm bus-servo units 0-1000 (~0.24 deg each, 500 = center).
Only one central can hold the link — force-quit the phone app first.

BleArm wraps bleak's asyncio API behind plain synchronous calls (it runs an
event loop in a background thread), so scripts and the curses teleop can use
it exactly like the USB controller.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from bleak import BleakClient, BleakScanner

SERVICE_HINT = "ffe0"
CMD_SERVO_MOVE = 0x03
# The arm's address is cached after the first find so later runs connect
# in ~1-2s instead of sitting through a full discovery sweep.
ADDRESS_CACHE = Path(__file__).with_name(".xarm_ble_address")


def servo_move_packet(moves: list[tuple[int, int]], duration_ms: int) -> bytes:
    params = [len(moves), duration_ms & 0xFF, (duration_ms >> 8) & 0xFF]
    for sid, pos in moves:
        pos = max(0, min(1000, int(pos)))
        params += [sid, pos & 0xFF, (pos >> 8) & 0xFF]
    return bytes([0x55, 0x55, len(params) + 2, CMD_SERVO_MOVE] + params)


class BleArm:
    """Synchronous facade over an async BLE link to the arm."""

    def __init__(
        self,
        name_hints: tuple[str, ...] = ("xarm", "hiwonder", "lobot"),
        timeout: float = 12.0,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client: BleakClient | None = None
        self._char = None
        self._run(self._connect(name_hints, timeout))

    def _run(self, coro):  # noqa: ANN001, ANN202 - small internal helper
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _connect(self, name_hints: tuple[str, ...], timeout: float) -> None:
        device = None
        # Fast path: directed lookup of the cached address (returns the
        # moment the arm advertises; stale cache falls through to a scan).
        if ADDRESS_CACHE.exists():
            addr = ADDRESS_CACHE.read_text().strip()
            if addr:
                device = await BleakScanner.find_device_by_address(addr, timeout=4.0)
        if device is None:
            # Filtered scan: stops as soon as a matching name appears
            # instead of sweeping for the full timeout.
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: bool(d.name and any(h in d.name.lower() for h in name_hints)),
                timeout=timeout,
            )
        if device is None:
            raise RuntimeError(
                f"no BLE device named like {name_hints} found — arm powered on? "
                "phone app fully closed (it hogs the only connection)?"
            )
        ADDRESS_CACHE.write_text(device.address)
        self._client = BleakClient(device)
        await self._client.connect()
        # The vendor UART service: pick its writable characteristic.
        for service in self._client.services:
            if SERVICE_HINT in service.uuid.lower():
                for ch in service.characteristics:
                    if "write" in ch.properties or "write-without-response" in ch.properties:
                        self._char = ch
                        break
        if self._char is None:
            uuids = [s.uuid for s in self._client.services]
            raise RuntimeError(f"no writable characteristic under {SERVICE_HINT}; services: {uuids}")
        print(f"connected: {device.name} ({device.address}), char {self._char.uuid}")

    def set_position(
        self, moves: int | list[tuple[int, int]], pos: int | None = None, duration_ms: int = 1500
    ) -> None:
        """set_position(3, 600) or set_position([(3, 600), (6, 400)]). Raw units."""
        if isinstance(moves, int):
            moves = [(moves, int(pos))]  # type: ignore[arg-type]
        packet = servo_move_packet(moves, duration_ms)
        self._run(self._client.write_gatt_char(self._char, packet, response=False))

    def set_angle(
        self,
        moves: int | list[tuple[int, float]],
        deg: float | None = None,
        duration_ms: int = 1500,
    ) -> None:
        """Centered degrees (0.0 = midpoint, ±120 span), soft-limited per servo.

        set_angle(6, 30.0) or set_angle([(6, 30.0), (1, -15.0)]).
        """
        from angles import servo_units

        if isinstance(moves, int):
            moves = [(moves, float(deg))]  # type: ignore[arg-type]
        self.set_position([(sid, servo_units(sid, d)) for sid, d in moves], duration_ms=duration_ms)

    def close(self) -> None:
        if self._client is not None:
            self._run(self._client.disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)
