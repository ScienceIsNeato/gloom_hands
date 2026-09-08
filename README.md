# gloom_hands

Teaching a Hiwonder xArm 1S to be a gloom hand from Tears of the
Kingdom. Step one (done): direct computer control. Step two (done): the
hunting animation — `./gloom_hand_demo.py` auto-connects over Bluetooth
and starts sweeping for victims. Step three (done, awaiting the arm):
eyes — `./gloom.py --eyes` watches a webcam and locks the base onto
whoever moves.

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
.venv/bin/pip install numpy 'opencv-python<5'    # the eyes (4.x: the 5.0 wheels dropped the face detectors)
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

## Eyes

The `vision/` package finds the person in the webcam frame and works out
their bearing from the **base pivot**, not from the camera, so the camera
can sit wherever is convenient. (Its frame-differencing detector was
ported from the 2024 HalloweenTracker head; the rest is new.) Describe where it is in the `CAMERA` block at
the top of `gloom.py` (metres forward and left of the pivot, and which
way the lens points), plus its horizontal field of view.

```bash
./eyes.py                          # just look: preview window, no arm (d switches detector)
./eyes.py --detector person        # HOG person detector instead of frame differencing
./gloom.py --eyes                  # hunt, and lock on when someone moves
./gloom.py --eyes --dry --show     # no arm: print the base headings, show what it sees
./gloom.py --eyes --video-src 1    # a different camera, or a video file
```

`eyes.py` feeds the preview the same `CAMERA` settings `gloom.py` uses.
Detectors: `background` (default; learns the static scene, anything that
differs is the person), `face` (YuNet when its model is in
`vision/models/`, else Haar cascades), `motion` (legacy differencing),
`person` (HOG, whole bodies). Every detection goes through a tracker that
follows the person across frames and holds position when the detector
blinks. Press `d` in the window to cycle detectors, `s` to save a frame.

While locked the base follows the person and the writhe continues; after
`LOST_AFTER_S` seconds with nobody moving it eases back into the sweep
from wherever it is. Calibrate before the first hunt:

1. `./eyes.py --hfov 60` — check a known object sits at the right angle;
   adjust `--hfov`, add `--mirrored` if left and right are swapped, then
   copy the values into `CAMERA`.
2. Set `BASE_SIGN` in `gloom.py`: +1 if positive servo-6 degrees turn the
   base left, -1 if right. `./move_ble.py 6 30` tells you.
3. `./eyes.py --bench 60` on the computer that will run it. The default detector is frame differencing, which costs a
   few milliseconds. The HOG person detector (`--detector person`) sees
   people who stand still but is much heavier on a small board.

`gloom_hand_demo.py` stays the blind, single-file version on purpose.

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

- `gloom.py` — the hunt; `--eyes` adds webcam tracking, `--dry` runs it without an arm
- `eyes.py` — preview and benchmark the tracking with gloom.py's camera settings
- `vision/` — detectors, camera geometry, tracker (`detector.py`, `geometry.py`, `tracking.py`, `preview.py`)
- `gloom_hand_demo.py` — single-file blind hunt for copying to any machine
- `probe.py` — USB first-contact: find device, battery, joint positions
- `move.py` / `move_ble.py` — one-shot single-servo move (wired / wireless)
- `teleop.py` — curses keyboard driving; `--ble` for wireless
- `ble_arm.py` — the BLE transport (scan → connect → LOBOT packets)

## Note for cloners

Scripts re-exec themselves into `./.venv/bin/python` when that venv
exists, so `./script.py` works without activation. After cloning, create
it once: `python3 -m venv .venv && .venv/bin/pip install xarm hidapi pyserial bleak numpy 'opencv-python<5'`.
