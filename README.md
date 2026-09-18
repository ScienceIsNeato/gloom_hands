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

By default the camera is assumed to sit **on the robot's front face**,
looking where the arm looks. That is the rig to aim for. The offset maths
below only earns its keep when the lens genuinely cannot live there.

The `vision/` package finds the person in the webcam frame and works out
their bearing from the **base pivot**, not from the camera, so the camera
can sit wherever is convenient. (Its frame-differencing detector was
ported from the 2024 HalloweenTracker head; the rest is new.) Describe where it is in the `CAMERA` block at
the top of `gloom.py` (metres forward and left of the pivot, and which
way the lens points), plus its horizontal field of view.

### Running it for days

The overload alarm is the thing that ends an unattended run: the servos
latch, the board drops its radio, and only a power cycle brings it back.
Three defences, in order of how much they are worth.

**Deploy on the USB cable, not Bluetooth.** The controller reports its
supply voltage, and the voltage falls *before* the alarm fires. Wired, the
hunt watches it and eases off — no strikes under `BROWNOUT_V`, and slack if
it stays there. Wireless is write-only and therefore blind to the one
signal that predicts the failure. USB also removes the radio that the
brownout knocks over in the first place.

```bash
py gloom.py --eyes --usb
```

**Activity is rationed.** However busy the room is, the arm stops after
`ACTIVE_MAX_S` and refuses to wake for `COOLDOWN_S`. Heat and supply sag
both build over minutes, and nothing else in the loop would ever choose to
stop while people keep arriving.

**The undulation is an opposed pair.** The shoulder and elbow are driven
together, the shoulder `+D` while the elbow goes `-D`, at the same rate and
the same instant. Their sum is the forearm's angle in the world, so holding
that sum constant makes the upper arm sweep through a wide arc while the
forearm counter-rotates by exactly as much: the arm works hard underneath
while the hand keeps its attitude. Measured in a dry run, the shoulder
travels 30 degrees peak to peak and the forearm angle does not change at
all. The wrist only breathes, because it is meant to look steady while that
happens.

It is also cheaper than moving them independently. The two contributions to
the centre of mass largely cancel, so a large visible motion costs much less
change in holding torque than its size suggests — which matters while the
supply is still the unexplained problem.

**Do not stream poses. Send waypoints.** Measured on the arm with
`smoothtest.py`: a single four-second command sweeps perfectly smoothly,
and the same arc sent as a stream of small steps gets rougher the more
steps you use. These servos do not blend a new command into the move they
are already making — they restart from wherever they are, so every packet
is a fresh little acceleration. Streaming a pose several times a second was
not refining the motion, it was chopping it up, and the board's light
blinking in time with the shudder was the arm reporting one flash per
interruption.

So each joint is given a destination and a travel time and then left alone.
The writhe aims at the sine's next peak or trough, which is two commands a
cycle instead of fifteen, and the servo draws the line between them — those
turning points are where the motion is genuinely meant to pause, so the
stop costs nothing. The base is different: it re-aims on a deadband, and
its travel time is `BASE_OVERLAP` times the gap between commands so the
move is always still running when the next lands and it never comes to
rest. A stop and a restart is exactly the twitch being removed. Per-joint
command rates drop from 4.5 a second to under two.

**Older note, still true: duration must outlast the gap.** Each packet
tells a servo where to go *and how long to take*. If that duration is
shorter than the gap to the next packet, the servo arrives early and then
sits perfectly still until the next one lands: move, stop, move, stop, which
is what a tremor looks like and why the board's light blinks once per
twitch. The duration is therefore measured from how fast packets actually
*leave* — not how often the loop offers one, since a packet superseded
before it went out never reached the arm — and carries 1.6x margin, so the
servo is always still travelling when the next target arrives. A joint whose
target has not moved is left out of the packet entirely: re-commanding a
loaded shoulder several times a second only makes it hunt, and every joint
costs bytes on the link that is already the bottleneck.

**And the creature slows down when the link does.** A Bluetooth link that
carries only a packet or so a second cannot render a half-hertz tremor: the
arm gets two or three targets per cycle and lunges between them, which
reads as a regular shudder at exactly the packet rate. So the writhe runs
on its own clock, scaled by how fast packets are actually landing, keeping
about nine commands per oscillator cycle whatever the link does. The arm
squirms more languidly on a slow link rather than juddering. The log's
`link_hz` and `motion` columns say what it settled on, and the tracking
line shows it too when the writhe is held back.

A slow link is worth fixing rather than accommodating: 1.3 packets a second
is abnormal for Bluetooth, and the cable has no such limit.

**Cadence matters too.** Commands go out every `TICK`,
and two things used to steal from that budget. Bluetooth writes were waited
on, so the loop period became the tick *plus* the write — on a slow link
that is 2.9 commands a second where 4.5 was intended. And the loop slept a
full tick after the work rather than until the next one was due. Writes are
now fired and forgotten, with any superseded packet discarded rather than
queued (each one is a complete absolute posture, so an old one is worse
than none), and the loop sleeps only the remainder. `--tick` lowers the
interval further if it still looks coarse, at the cost of more packets.

The recorder carries `look_ms` (camera plus detection), `send_ms` (handing
a packet to the arm) and `late` (ticks that overran their slot) so this is
measurable rather than a matter of opinion.

**Everything is recorded.** `--log FILE` (on by default, `gloom.log`)
appends a flushed line per event and every five seconds otherwise: state,
strike phase, base angle, supply voltage, how long it has been awake. The
failure takes the power with it, so nothing is buffered. After a death,
the last lines say what it was doing and what the supply was doing.

```
10:42:41   12.1  slack  reason=awake 240s — taking a break awake_for=240.1 volts=8.8
10:42:55   25.9  lurch  base=-20.2 volts=7.4
10:42:56   26.4  brownout  volts=6.2 coil=settling awake=True
```

**With `--eyes` the arm is asleep almost all the time.** It is meant to sit
in a corner doing nothing, so its resting state is genuinely off:

| State | What it does |
| --- | --- |
| `SEARCH` | sweeps and writhes, looking. Entered at startup and after losing someone, and runs for `SEARCH_S`. |
| `TRACK` | follows the face, with the coil and strike cycle. |
| `SLACK` | stands upright, then **switches every servo off**. No current, no holding torque — limp enough to reposition by hand. |

`SEARCH_S` seconds with nobody in sight and it parks and lets go. Every
sighting restarts that clock, so an occupied room keeps it awake and an
empty one releases it. From slack, a face held for `WAKE_AFTER_S` brings it
back; that delay is a debounce, so one spurious detection cannot raise it.
It counts CONFIRMED sightings only. The tracker coasts for a second and a
half after losing someone, which is right while following them and wrong as
evidence that anyone is there — one stray detection used to buy 1.5s of
apparent "face held" on its own, and a second stray hit inside that window
cleared the threshold between them.

`POSE_SLACK_DEG` is not a pose it holds. It is where the arm stands a moment
before the power is cut, chosen so that letting go barely moves it: vertical
and balanced over the base. Anything reaching would fall. Tune it with
`./pose.py slack`, then run `./relax.py` and see how far it actually sags.

**Going slack no longer drops the Bluetooth link** (`SLEEP_DROPS_LINK`).
Dropping it was a guess — that `CMD_SERVO_STOP` does not release this board
and only the link dying does — and that guess was never confirmed, while the
reconnect trouble it caused was seen repeatedly: the health monitor read the
deliberate disconnect as the arm having died and reconnected it in a loop.
The unload is simply repeated while asleep instead. `./relax.py --stay`
settles whether the original guess was right; if the arm holds its pose
through a sleep, set it back to True.

**The older note, kept because it may still be true:** The documented
unload command, `CMD_SERVO_STOP`, appears to do nothing on this board: the
arm only ever went limp when the process exited and the connection died
with it. So sleeping sends the unload and then disconnects, and the next
command reconnects on its own from the cached address. `./relax.py --stay`
is the experiment that tells the two apart — it unloads but holds the link
open, so you can push the arm and see which mechanism is really releasing
it.

**Testing whether it is really limp is harder than it sounds**, because
walking up to check puts your face in front of the camera and wakes it
within `WAKE_AFTER_S`. The terminal says so while it counts up. For an
answer that does not depend on feel, plug in the USB cable and run
`./servo_watch.py --verify-relax`: it unloads, then reads the joint angles
while you push the arm about. A joint that stays where you put it is
genuinely off; one that springs back is still powered.

```bash
./eyes.py                          # just look: preview window, no arm (d cycles detectors, s snapshots)
./eyes.py --detector background    # or motion (legacy diff), person (HOG); default is face
./gloom.py --eyes                  # search, track, and go slack in an empty room
./gloom.py                         # blind hunt, no camera — Esc or q to stop
./gloom.py --eyes --dry --show     # no arm: print the base headings, show what it sees
./gloom.py --eyes --flip           # try the other BASE_SIGN for one run
./gloom.py --eyes --video-src 1    # a different camera, or a video file
```

**A face detector alone is not enough on a wide lens.** Near the frame edge
a face is stretched by the optics and turned away from the camera, and
YuNet stops seeing it well before it stops being obvious to look at. So the
tracker will open a track on the *background blob* when the face detector
comes up empty, provided the blob is person-sized. Precision about where
someone's face is matters less than noticing they are there at all; once
a track exists, a face reasserts itself the moment one is visible.

**Nothing opens a track on one frame.** A detection has to keep appearing,
in roughly the same place, for `confirm_s` before anything downstream hears
about it. A face detector will occasionally find a face in wallpaper or a
shadow, but it will not find the same one there half a second later.
Anything shorter is a blip: counted, written to the log, and otherwise
ignored. This is the single biggest lever on false positives and it costs
only that half second of response.

That gate is why YuNet's score threshold went back up to 0.70 with a
minimum face size: a low threshold was letting through wallpaper on the
reasoning that a stretched face at the frame edge scores poorly, but the
temporal gate catches that case better — a real face keeps appearing and a
shadow does not.

A blob may raise the alarm, but only a face keeps it up: a track the face
detector has never confirmed is dropped after `prove_by_s`. Without that, a
lighting change or a shifted chair could hold the arm awake indefinitely,
and an empty room never got to sleep.

That fallback is deliberately fussy, because it started out too eager. A
webcam adjusting its exposure makes the *whole frame* differ from the
learned background, which arrives as one enormous blob and reads as a
person standing very close. So a blob must be tall rather than wide, a
quarter of the frame but not all of it, and still in the same place four
frames later before it counts.

Set `hfov_deg` in `CAMERA` from the webcam's spec sheet, or pass `--hfov`.
Wide-angle webcams are commonly 90 to 120 degrees. Getting it wrong does
not break tracking, because the frame is mapped onto the sweep either way,
but every distance in the log scales with it: a face that reads 3.7 m at an
assumed 60 degrees reads 2.1 m at 90.

`eyes.py` feeds the preview the same `CAMERA` settings `gloom.py` uses.
Detectors: `face` (default; YuNet when its model is in `vision/models/`,
else Haar cascades), `background` (learns the static scene, anything that
differs is the person; cheapest option that keeps a still person),
`motion` (legacy differencing), `person` (HOG, whole bodies). Every detection goes through a tracker that
follows the person across frames and holds position when the detector
blinks. Press `d` in the window to cycle detectors, `s` to save a frame.

**The base follows where the victim sits across the frame**, mapped onto
its sweep range, so walking from one side of the picture to the other
swings the arm through most of its travel wherever you are standing.
That mapping is computed through the camera-offset geometry at the
victim's own distance rather than being a fudge factor, so a camera
mounted off to one side still points the arm at the right side of the
room. Pointing at the victim's literal computed bearing was the obvious
alternative and is a trap: with the camera 4.6 m from the arm it produces
about 18 degrees of travel for someone at the lens, and about 90 for a
single step by someone standing at the arm. `TRACK_FILL` sets how much of
the sweep a full frame crossing uses.

Tracking lag is a budget, not a single number. Averaging readings costs
about (N-1)/2 ticks of delay, so `LOCK_SMOOTH` buys steadiness with
responsiveness. `TRACK_SLEW_DPS` caps how fast the base may turn, and
crossing the sweep at that rate is usually the largest single term. The
base is a yaw joint, so gravity never opposes it and it is the cheapest
thing on the arm to move quickly; the shoulder and elbow are where the
current goes. If the overload alarm ever returns, throttle those, not this.

**The coil and the strike are not animations that play.** Every tick the
arm sends a single posture: where the animator has eased the shoulder,
elbow and wrist to, the writhe oscillating on top of that, and the freshest
tracked heading for the base. Nothing blocks, so someone moving during a
draw-back or a lunge is followed all the way through it. The first sighting
after waking gets an entrance instead of a power-on: it rises straight into
the coil, writhes there for `GREET_HOLD_S`, then strikes.

While locked the base follows the person and the writhe continues; after
`LOST_AFTER_S` seconds with nobody moving it eases back into the sweep
from wherever it is. Every `COIL_EVERY_S` or so while locked it draws
back into `POSE_COIL_DEG` (slowly), sits coiled for `COIL_HOLD_S` still
tracking you, then lurches back out to the point pose in `LURCH_MS` with
a snap of the wrist as it lands. That overshoot used to be on the
shoulder, which drove the heaviest joint past its most cantilevered pose
at the end of a fast move and then reversed it under full load — the
instant the overload alarm always fired. `LURCH_OVERSHOOT_JOINT` puts it
back on servo 5 if you want the old violence. Find the coil pose on the arm with
`./pose.py 5=-12 4=80 3=12` (or `./pose.py coil 4=60` to tweak one joint)
and paste the numbers into `POSE_COIL_DEG`. Keep `LURCH_MS` at 450 or
above: faster than that the servos are flat out and the supply sag can
drop the Bluetooth link (the hunt now reconnects and carries on if it does). Calibrate before the first hunt:

1. `./eyes.py --hfov 60` — check a known object sits at the right angle;
   adjust `--hfov`, add `--mirrored` if left and right are swapped, then
   copy the values into `CAMERA`.
2. Confirm `BASE_SIGN`. Stand in front of the arm and run
   `./move_ble.py 6 30`. If the hand swings toward **your right**, the
   default `+1` is correct; if toward your left, set `-1`. Or just run the
   hunt with `--flip` to try the other sign for one run.
3. `./eyes.py --bench 60` on the computer that will run it. The default detector is frame differencing, which costs a
   few milliseconds. The HOG person detector (`--detector person`) sees
   people who stand still but is much heavier on a small board.

`gloom_hand_demo.py` stays the blind, single-file version on purpose.

## The hand, not the joints

Everything used to command joint angles and let the hand end up wherever
those put it. That is how the gripper came to travel from the table top to
straight overhead: the shoulder was swinging 110 degrees and nothing
anywhere asked where the hand was going.

`kinematics.py` turns the three pitch joints into the only three numbers
that matter — how far the hand reaches, how high it sits, and which way it
points. Three joints, three numbers, exactly one solution. The arm is now
told where to put its hand, and the shoulder, elbow and wrist each do
whatever that requires, including opposing one another.

So the height is simply held. Measured across a full body cycle in both
postures, the hand's height wanders 0.0000 inches; the only change left is
the 0.69 inches between the reaching and coiled poses themselves. The body
breathes by gliding the hand in and out along that level, `GLIDE_IN`, which
is now the whole "how alive does it look" dial.

Two things had to be measured on the real arm, and both are in
`kinematics.py`: the link lengths pivot to pivot, and which way each joint
turns. That second one cannot be reasoned out — positive turns the shoulder
forward, the elbow backward and the wrist forward, so the joint angles do
NOT simply add up, and every attempt to infer the geometry from the tuned
poses contradicted itself until `calibrate.py` settled it on the hardware.

A gesture that runs along the arm as a wave has to be checked against the
hand, not just admired in joint angles. Drawing back it is fine, because
each joint's travel is stretched so they all arrive together and the hand
dips only 1.3in. Striking, the same trick was disastrous: the shoulder
swinging forward a beat early, while the arm is still folded behind it,
scooped the gripper 5.1 inches *below* where it started. The strike now
moves as one, and the crack comes from the wrist overshooting as it lands.

## Servo map (xArm 1S)

```
1 gripper · 2 wrist roll · 3 wrist bend · 4 elbow · 5 shoulder · 6 base
```

## Safety notes

- **If a servo starts emitting a tone, run `./relax.py`** (add `--usb` when
  wired). It cuts the motors and the arm goes slack, so support it first if
  it is holding out over an edge. Stopping the hunt is not enough on its
  own: the servos keep holding their last position, and their last position
  is what they are complaining about.
- **The tone is the servo alarm.** These servos raise
  it for under-voltage as readily as for heat, and on this arm the cause
  measured out to be a supply brownout, not a hot joint: the pack fell from
  9.0 V to 4.9 V during the coil. Check power before you go hunting for a
  stalled servo. `./servo_watch.py --relax` cuts the motors and
  the arm goes limp, which is gentler than pulling the power. The wired
  diagnostic (`./servo_watch.py --strike`) reproduces the coil-and-lurch and
  ranks the joints by how far each one drifts off its commanded angle; the
  worst offender is the one doing the work. `--soak 600` runs the hunt's real
  motion, which is what actually builds the heat; a single strike does not.
  Read the FADING table, not the raw errors: a joint the hunt is sweeping
  lags tens of degrees behind its command with nothing wrong, so only error
  on a joint told to stay *still* means load, and only error that grows from
  the start of the run to the end means heat. It aborts and unloads if a
  stationary joint stalls, and `--no-abort` turns that off.
- A pose is not a rest. Parking at `POSE_REST_DEG` still leaves every servo
  energised and holding, so the hunt now unloads the arm when it exits.
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
- `relax.py` — panic button: unload every servo, wired or wireless
- `pose.py` — move several joints at once and hold, for finding poses by eye
- `servo_watch.py` — wired load diagnostic: which joint is straining, and `--relax` to limp the arm
- `test_geometry.py` — hand-computed checks for the camera-offset trig; no hardware needed
- `test_motion.py` — writhe budgets: is each oscillator slow enough to render, and to afford?
- `test_backends.py` — the real and dry backends must keep the same surface
- `test_detectors.py` — every constructor argument must actually be stored, and
  `detect()` must survive a real hit, not just an empty room
- `smoothtest.py` — why is the motion jerky? Sweeps one joint at several
  command rates, and at two packet sizes, so the cause can be watched rather
  than argued about. Bypasses the hunt's streaming entirely.
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
`vision/models/README.md`, and use `curl -fL` so a 404 page cannot land in
the file; it should be 232589 bytes).

## Note for cloners

Scripts re-exec themselves into the project venv when one exists, so
`./script.py` (macOS/Linux) or `py script.py` (Windows) works without
activating anything. After cloning, create it once:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Then fetch the YuNet face model into `vision/models/` (see the note there)
if you want `--detector face`.
