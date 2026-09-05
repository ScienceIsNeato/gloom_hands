# gloom_hands

Teaching a Hiwonder xArm 1S to be a gloom hand from Tears of the
Kingdom. Step one (done): direct computer control. Step two (done): the
hunting animation — `./gloom_hand_demo.py` auto-connects over Bluetooth
and starts sweeping for victims. Step three: eyes.

Direct computer control of the Hiwonder / LewanSoul **xArm 1S** from macOS —
no bundled controller, no phone app. Two transports, same protocol
underneath (LOBOT packets: `0x55 0x55 <len> <cmd> <params>`):

| Transport | How | Feedback |
| --- | --- | --- |
| **USB (wired)** | `xarm` package over USB HID (VID 0x0483 / PID 0x5750) | full — read positions, battery voltage |
| **Bluetooth LE (wireless)** | `bleak` writing LOBOT packets to the board's vendor UART service (`0xFFE0`); it advertises as "Hiwonder" | write-only in this v1 (commands work, no position reads) |

## Setup (already done in this folder)

```bash
python3 -m venv .venv
.venv/bin/pip install xarm hidapi pyserial bleak
```

## Getting started — wired first

USB is the best first test because it can *read back* state and prove the
link end-to-end:

1. Power the arm from its own supply (USB alone cannot drive servos) and
   flip the switch ON.
2. Connect the controller board's USB port to the Mac (a data cable, not a
   charge-only one).
3. `./probe.py` — finds the device, prints battery voltage and where every
   joint currently sits.
4. `./move.py 6 600` — nudge the base. `./teleop.py` — keyboard driving.

## Wireless

1. Force-quit the phone app — the board accepts ONE Bluetooth connection
   and the app will hog it.
2. Arm powered on, then: `./move_ble.py 6 600` or `./teleop.py --ble`.
3. First run: macOS will ask to grant the terminal Bluetooth permission
   (System Settings → Privacy & Security → Bluetooth).

The BLE protocol details came from the community LeArm work
(patspace/learm-bluetooth-python): service `0xFFE0`, same `0x55 0x55`
packets as USB. Note the xArm 1S uses bus-servo units **0–1000**
(≈0.24°/unit, 500 = center) — the LeArm's 500–2500 range is its PWM
cousin, not this arm.

## Servo map (xArm 1S)

```
1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
```

## Safety notes

- Always give a duration (`move.py` defaults to 1500 ms). A big jump with
  no duration is a full-speed lunge.
- Soft limits in `teleop.py` stay 50 units off the hard stops; widen them
  only once you know the arm's actual clearances.
- The BLE path is fire-and-forget: it cannot verify a move landed. Use USB
  when you need feedback (or for anything closed-loop).

## Files

- `probe.py` — USB first-contact: find device, battery, joint positions
- `move.py` / `move_ble.py` — one-shot single-servo move (wired / wireless)
- `teleop.py` — curses keyboard driving; `--ble` for wireless
- `ble_arm.py` — the BLE transport (scan → connect → LOBOT packets)

## Note for cloners

Script shebangs point at this project's venv by absolute path (so
`./script.py` works without activation). After cloning elsewhere, either
recreate the venv at `.venv/` and fix the shebang paths, or just run
everything as `.venv/bin/python script.py`.
