#!/usr/bin/env python3
"""List nearby BLE advertisements and flag the arm if it's broadcasting.

BLE peripherals like the xArm never appear in macOS Bluetooth Settings —
no pairing exists or is needed; scripts connect directly. First run asks
for Bluetooth permission for your terminal (System Settings → Privacy &
Security → Bluetooth) — allow it, then rerun.
"""
import asyncio

from bleak import BleakScanner


async def main() -> None:
    print("scanning 10s...")
    devs = await BleakScanner.discover(timeout=10)
    named = sorted((d.name, d.address) for d in devs if d.name)
    for n, a in named:
        print(f"  {n}  ({a})")
    print(f"({len(devs)} devices total, {len(named)} named)")
    hits = [n for n, _ in named if any(k in n.lower() for k in ("hiwonder", "lobot", "arm"))]
    if hits:
        print("ARM FOUND:", ", ".join(hits), "->  ./move_ble.py 6 600")
    else:
        print("no arm advertisement. Checklist: arm power switch ON (its own")
        print("supply), within ~10m, and the phone app force-quit (it hogs")
        print("the only connection — a connected peripheral stops advertising).")


asyncio.run(main())
