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
.venv/bin/pip install -r requirements.txt
```

On Windows, see [Running on Windows](#running-on-windows) below.

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
Detectors: `face` (default; YuNet when its model is in `vision/models/`,
else Haar cascades), `background` (learns the static scene, anything that
differs is the person; cheapest option that keeps a still person),
`motion` (legacy differencing), `person` (HOG, whole bodies). Every detection goes through a tracker that
follows the person across frames and holds position when the detector
blinks. Press `d` in the window to cycle detectors, `s` to save a frame.

While locked the base follows the person and the writhe continues; after
`LOST_AFTER_S` seconds with nobody moving it eases back into the sweep
from wherever it is. Every `COIL_EVERY_S` or so while locked it draws
back into `POSE_COIL_DEG` (slowly), sits coiled for `COIL_HOLD_S` still
tracking you, then lurches back out to the point pose in `LURCH_MS` with
a shoulder overshoot. Find the coil pose on the arm with
`./pose.py 5=-12 4=80 3=12` (or `./pose.py coil 4=60` to tweak one joint)
and paste the numbers into `POSE_COIL_DEG`. Keep `LURCH_MS` at 450 or
above: faster than that the servos are flat out and the supply sag can
drop the Bluetooth link (the hunt now reconnects and carries on if it does). Calibrate before the first hunt:

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
- `pose.py` — move several joints at once and hold, for finding poses by eye
- `probe.py` — USB first-contact: find device, battery, joint positions
- `move.py` / `move_ble.py` — one-shot single-servo move (wired / wireless)
- `teleop.py` — curses keyboard driving; `--ble` for wireless
- `ble_arm.py` — the BLE transport (scan → connect → LOBOT packets)

## Running on Windows

Everything works, with three differences. Nothing in `requirements.txt`
compiles from source: every dependency ships a Python 3.14 Windows wheel.

**Setup**

```bat
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**Invocation.** `./gloom.py` is a Unix idiom. On Windows run scripts
through Python, from any interpreter — they re-exec themselves into
`.venv\Scripts\python.exe` when that venv exists, so activating is
optional:

```bat
py gloom.py --eyes --show
py eyes.py --detector face
py pose.py
```

**Bluetooth.** Bleak talks to the WinRT stack and needs Windows 10 build
16299 or newer with a Bluetooth 4.0+ radio. Pair the arm in Windows
Bluetooth settings first if a bare connect fails. The board still accepts
exactly one connection, so close the phone app and any other script
(`Get-Process python`) before running.

**Camera.** OpenCV defaults to Media Foundation on Windows, which is slow
to open a webcam and often ignores resolution requests, so the eyes ask
for DirectShow first and fall back automatically. Windows also gates the
camera per app: Settings → Privacy & security → Camera → *Let desktop apps
access your camera*.

**Two files are per-machine** and are not in git: `.xarm_ble_address` (the
arm's address, which is a MAC on Windows and a different UUID on macOS, so
it is tagged with the platform that wrote it and ignored elsewhere) and
`vision/models/*.onnx` (the YuNet face model — re-download it, see
`vision/models/README.md`).

## Note for cloners

Scripts re-exec themselves into the project venv when one exists, so
`./script.py` (macOS/Linux) or `py script.py` (Windows) works without
activating anything. After cloning, create it once:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Then fetch the YuNet face model into `vision/models/` (see the note there)
if you want `--detector face`.
