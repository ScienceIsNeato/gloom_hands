#!/usr/bin/env python3
"""Wired diagnostic: find out which servo is being overworked, and stop it.

The xArm controller cannot report servo temperature (its USB protocol only
does move, read-position, read-battery, unload), but it does not need to.
A servo losing to gravity sits at a POSITION ERROR: it was told 36 deg and
is holding 28. That error is the stall signal, and the joint with the
biggest steady error is the one drawing the current and making the noise.

    ./servo_watch.py --relax        # PANIC BUTTON: limp the arm, no power-pull
    ./servo_watch.py                # live table of every joint, nothing commanded
    ./servo_watch.py --hold point   # hold the point pose and watch it sag
    ./servo_watch.py --strike       # coil -> lurch -> hold, the actual failure

Ctrl-C always unloads the servos on the way out. The test also aborts and
unloads on its own if any joint drifts past --abort-deg, so a run left
alone cannot cook a servo.

USB only: this needs the position feedback that Bluetooth cannot give us.
Power the arm from its own supply as well as the USB cable.
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, *(("Scripts", "python.exe") if _os.name == "nt" else ("bin", "python")))
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    if _os.name == "nt":
        # execv on Windows detaches from the console (the prompt returns while
        # output keeps coming), so spawn and pass the exit code through.
        import subprocess as _sp
        _sys.exit(_sp.call([_venv_py] + _sys.argv))
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import argparse
import time

import xarm

from angles import NAMES, servo_units, units_to_deg
from gloom import (
    LURCH_MS, LURCH_OVERSHOOT_DEG, POSE_COIL_DEG, POSE_POINT_DEG, POSE_REST_DEG, SETTLE_MS,
)

JOINTS = (1, 2, 3, 4, 5, 6)
POSES = {"point": POSE_POINT_DEG, "coil": POSE_COIL_DEG, "rest": POSE_REST_DEG}


class Watch:
    def __init__(self, debug: bool = False) -> None:
        self.arm = xarm.Controller("USB", debug=debug)
        self.commanded: dict[int, float] = {}
        self.samples: list[tuple[float, dict[int, float], float | None]] = []

    # -- talking to the arm ------------------------------------------------ #
    def send(self, pose: dict[int, float], dur_ms: int) -> None:
        servos = [xarm.Servo(sid, servo_units(sid, d)) for sid, d in pose.items()]
        self.arm.setPosition(servos, dur_ms, wait=False)
        self.commanded.update(pose)

    def read(self) -> dict[int, float]:
        return {sid: units_to_deg(self.arm.getPosition(sid)) for sid in JOINTS}

    def voltage(self) -> float | None:
        try:
            return self.arm.getBatteryVoltage()
        except Exception:  # noqa: BLE001 - a dropped read must not end the test
            return None

    def relax(self) -> None:
        self.arm.servoOff()

    # -- the measurement --------------------------------------------------- #
    def sample(self, t0: float) -> tuple[dict[int, float], dict[int, float], float | None]:
        actual = self.read()
        volts = self.voltage()
        error = {sid: self.commanded[sid] - actual[sid] for sid in actual if sid in self.commanded}
        self.samples.append((time.monotonic() - t0, error, volts))
        return actual, error, volts

    def watch(self, seconds: float, t0: float, hz: float, abort_deg: float, label: str) -> bool:
        """Poll for `seconds`. False if it aborted on excessive drift."""
        period = 1.0 / hz
        over = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            actual, error, volts = self.sample(t0)
            worst = max(error.items(), key=lambda kv: abs(kv[1]), default=(0, 0.0))
            cells = " ".join(
                f"{sid}:{error.get(sid, 0.0):+5.1f}" for sid in JOINTS if sid in self.commanded
            )
            v = f"{volts:5.2f}V" if volts else "  ?  "
            print(f"  [{label}] t={time.monotonic()-t0:5.1f}s {v}  err {cells}   worst: "
                  f"servo {worst[0]} ({NAMES.get(worst[0], '?')}) {worst[1]:+.1f}")
            over = over + 1 if abs(worst[1]) > abort_deg else 0
            if over >= 3:
                print(f"\n!! servo {worst[0]} ({NAMES.get(worst[0])}) is {worst[1]:+.1f} deg off its "
                      f"target and not recovering — that is the one straining. Unloading now.")
                return False
            time.sleep(period)
        return True

    def report(self) -> None:
        if not self.samples:
            return
        print("\n=== who is working hardest (mean position error while holding) ===")
        per: dict[int, list[float]] = {}
        for _, error, _ in self.samples:
            for sid, e in error.items():
                per.setdefault(sid, []).append(abs(e))
        ranked = sorted(per.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        for sid, errs in ranked:
            mean, peak = sum(errs) / len(errs), max(errs)
            flag = "  <-- straining" if mean > 3.0 else ""
            print(f"  servo {sid} {NAMES.get(sid, '?'):11s} mean {mean:5.2f} deg   peak {peak:5.2f}{flag}")
        volts = [v for _, _, v in self.samples if v]
        if volts:
            print(f"\n  supply: {max(volts):.2f} V idle high -> {min(volts):.2f} V under load "
                  f"(sag {max(volts) - min(volts):.2f} V)")


def main() -> None:
    p = argparse.ArgumentParser(description="wired servo load diagnostic")
    p.add_argument("--relax", action="store_true", help="unload every servo and exit (the panic button)")
    p.add_argument("--hold", choices=sorted(POSES), help="hold this pose and watch it")
    p.add_argument("--strike", action="store_true", help="coil -> lurch -> hold, the failing sequence")
    p.add_argument("--seconds", type=float, default=60.0, help="how long to hold and watch (default 60)")
    p.add_argument("--hz", type=float, default=2.0, help="samples per second (default 2)")
    p.add_argument("--abort-deg", type=float, default=12.0,
                   help="unload if a joint stays this far off target (default 12)")
    p.add_argument("--debug", action="store_true", help="dump raw USB packets")
    a = p.parse_args()

    w = Watch(debug=a.debug)
    v = w.voltage()
    print(f"connected. supply {v:.2f} V\n" if v else "connected (no voltage read)\n")

    if a.relax:
        w.relax()
        print("all servos unloaded — the arm is limp. No need to pull the power.")
        return

    t0 = time.monotonic()
    ok = True
    try:
        if a.strike:
            print("coiling...")
            w.send(POSE_COIL_DEG, 1800)
            time.sleep(2.0)
            ok = w.watch(3.0, t0, a.hz, a.abort_deg, "coiled")
            if ok:
                print("LURCH")
                strike = {sid: POSE_POINT_DEG[sid] for sid in (5, 4, 3)}
                strike[5] = strike[5] + LURCH_OVERSHOOT_DEG
                w.send(strike, LURCH_MS)
                time.sleep(LURCH_MS / 1000)
                w.send({5: POSE_POINT_DEG[5]}, SETTLE_MS)
                time.sleep(SETTLE_MS / 1000)
                print(f"holding full extension for {a.seconds:.0f}s — this is where it sings")
                ok = w.watch(a.seconds, t0, a.hz, a.abort_deg, "extended")
        elif a.hold:
            pose = POSES[a.hold]
            print(f"moving to the {a.hold} pose...")
            w.send(pose, 1800)
            time.sleep(2.0)
            ok = w.watch(a.seconds, t0, a.hz, a.abort_deg, a.hold)
        else:
            print("monitoring only — nothing commanded. Ctrl-C to stop.")
            while True:
                actual = w.read()
                volts = w.voltage()
                cells = " ".join(f"{sid}:{actual[sid]:+6.1f}" for sid in JOINTS)
                print(f"  {volts:5.2f}V  " if volts else "   ?    ", cells)
                time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        w.report()
        w.relax()
        print("\nservos unloaded (arm is limp).")
        if not ok:
            print("Aborted early on drift — see the ranking above.")


if __name__ == "__main__":
    main()
