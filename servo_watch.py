#!/usr/bin/env python3
"""Wired diagnostic: find out which servo is being overworked, and stop it.

The xArm controller cannot report servo temperature (its USB protocol only
does move, read-position, read-battery, unload), but it does not need to.
A servo losing to gravity sits at a POSITION ERROR: it was told -48 deg and
is holding -50. That error is the load signal, and a servo that is FADING
as it heats shows an error that grows the longer it runs.

    ./servo_watch.py --relax        # PANIC BUTTON: limp the arm, no power-pull
    ./servo_watch.py                # live table of every joint, nothing commanded
    ./servo_watch.py --hold point   # hold one pose and watch it sag
    ./servo_watch.py --strike       # one coil -> lurch -> hold
    ./servo_watch.py --soak 600     # the REAL hunt motion, all six servos, until it sings

--soak is the one that reproduces the overload tone. A single strike barely
warms the servos; the hunt drives six of them continuously for minutes, and
heat is cumulative. Run it until you hear the tone, then read the trend
table: the joint whose error climbs between the first and last third is the
one fading.

Error sign: positive means the joint is BELOW the angle it was told to hold,
so gravity is winning there.

Ctrl-C always unloads on the way out, and the run aborts itself if a joint
stays past --abort-deg, so it cannot cook a servo unattended.

USB only: this needs position feedback, which Bluetooth cannot give us.
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
import random
import time

import xarm

from angles import NAMES, servo_units, units_to_deg
from gloom import (
    COIL_EVERY_S, COIL_HOLD_S, COIL_MS, LURCH_MS, LURCH_OVERSHOOT_DEG, POSE_COIL_DEG,
    POSE_POINT_DEG, POSE_REST_DEG, SETTLE_MS, TICK, sweep_deg, writhe_deg,
)

JOINTS = (1, 2, 3, 4, 5, 6)
POSES = {"point": POSE_POINT_DEG, "coil": POSE_COIL_DEG, "rest": POSE_REST_DEG}


class Watch:
    def __init__(self, debug: bool = False) -> None:
        self.arm = xarm.Controller("USB", debug=debug)
        self.commanded: dict[int, float] = {}
        # (seconds, phase, {servo: error_deg}, volts)
        self.samples: list[tuple[float, str, dict[int, float], float | None]] = []
        self.t0 = time.monotonic()

    # -- talking to the arm ------------------------------------------------ #
    def send(self, pose: dict[int, float], dur_ms: int) -> None:
        self.arm.setPosition(
            [xarm.Servo(sid, servo_units(sid, d)) for sid, d in pose.items()], dur_ms, wait=False
        )
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
    def sample(self, phase: str, quiet: bool = False) -> float:
        """Record one sample. Returns the worst |error| seen."""
        actual, volts = self.read(), self.voltage()
        error = {sid: self.commanded[sid] - actual[sid] for sid in actual if sid in self.commanded}
        t = time.monotonic() - self.t0
        self.samples.append((t, phase, error, volts))
        worst_sid, worst = max(error.items(), key=lambda kv: abs(kv[1]), default=(0, 0.0))
        if not quiet:
            cells = " ".join(f"{sid}:{error[sid]:+5.1f}" for sid in JOINTS if sid in error)
            v = f"{volts:5.2f}V" if volts else "  ?  "
            print(f"  [{phase}] t={t:6.1f}s {v}  err {cells}   worst: servo {worst_sid} "
                  f"({NAMES.get(worst_sid, '?')}) {worst:+.1f}")
        return abs(worst)

    def hold_and_watch(self, phase: str, seconds: float, hz: float, abort_deg: float) -> bool:
        over = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.sample(phase) > abort_deg:
                over += 1
                if over >= 3:
                    print(f"\n!! a joint is more than {abort_deg:.0f} deg off target and not "
                          f"recovering. Unloading now.")
                    return False
            else:
                over = 0
            time.sleep(1.0 / hz)
        return True

    # -- the real thing ----------------------------------------------------- #
    def soak(self, seconds: float, hz: float, abort_deg: float) -> bool:
        """Drive exactly the motion the hunt drives: all six servos, the
        writhe running continuously, strikes on the hunt's own schedule."""
        print(f"soaking for up to {seconds:.0f}s with the real hunt motion.")
        print("Listen for the tone. Ctrl-C the moment you hear it.\n")
        self.send({**POSE_POINT_DEG, 6: 0.0}, 2500)
        time.sleep(2.7)
        coil, coil_at, strikes, next_sample, over = "out", 4.0, 0, 0.0, 0
        while True:
            now = time.monotonic() - self.t0
            if now > seconds:
                return True
            base = sweep_deg(now, 0.0)

            if now >= coil_at:
                if coil == "out":
                    self.send({**POSE_COIL_DEG, 6: base}, COIL_MS)
                    coil, coil_at = "coiling", now + COIL_MS / 1000
                elif coil == "coiling":
                    coil, coil_at = "coiled", now + random.uniform(*COIL_HOLD_S)
                else:
                    strikes += 1
                    strike = {sid: POSE_POINT_DEG[sid] for sid in (5, 4, 3)}
                    strike[5] = strike[5] + LURCH_OVERSHOOT_DEG
                    self.send({**strike, 6: base}, LURCH_MS)
                    time.sleep(LURCH_MS / 1000)
                    self.send({5: POSE_POINT_DEG[5]}, SETTLE_MS)
                    time.sleep(SETTLE_MS / 1000)
                    print(f"  -- strike {strikes} at t={now:.0f}s --")
                    coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)
                    continue

            self.send(writhe_deg(now, base, coiled=coil != "out"), int(TICK * 1000) + 80)

            if now >= next_sample:
                phase = "extended" if coil == "out" else "coiled"
                if self.sample(phase) > abort_deg:
                    over += 1
                    if over >= 3:
                        print(f"\n!! a joint is more than {abort_deg:.0f} deg off target and not "
                              f"recovering. Unloading now.")
                        return False
                else:
                    over = 0
                next_sample = now + 1.0 / hz
            time.sleep(TICK)

    # -- the answer ---------------------------------------------------------- #
    def report(self) -> None:
        if not self.samples:
            return
        phases = []
        for _, phase, _, _ in self.samples:
            if phase not in phases:
                phases.append(phase)

        print("\n=== load by phase (mean |commanded - actual|, degrees) ===")
        print("    positive error = the joint is sagging below where it was told to hold\n")
        for phase in phases:
            rows = [(e, t) for t, p, e, _ in self.samples if p == phase]
            if not rows:
                continue
            per: dict[int, list[float]] = {}
            for e, _ in rows:
                for sid, v in e.items():
                    per.setdefault(sid, []).append(abs(v))
            print(f"  [{phase}]  {len(rows)} samples")
            for sid, errs in sorted(per.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True):
                mean = sum(errs) / len(errs)
                flag = "  <-- carrying the load" if mean > 1.5 else ""
                print(f"    servo {sid} {NAMES.get(sid, '?'):11s} mean {mean:5.2f}   "
                      f"peak {max(errs):6.2f}{flag}")

        print("\n=== is anything FADING? (first third vs last third of the run) ===")
        print("    a joint that is heating up holds worse as time goes on\n")
        span = self.samples[-1][0] - self.samples[0][0]
        if span < 30:
            print(f"    run was only {span:.0f}s — too short to show heat. Use --soak 600.")
        else:
            lo, hi = self.samples[0][0] + span / 3, self.samples[0][0] + 2 * span / 3
            first: dict[int, list[float]] = {}
            last: dict[int, list[float]] = {}
            for t, _, e, _ in self.samples:
                bucket = first if t <= lo else (last if t >= hi else None)
                if bucket is not None:
                    for sid, v in e.items():
                        bucket.setdefault(sid, []).append(abs(v))
            for sid in sorted(set(first) & set(last)):
                a = sum(first[sid]) / len(first[sid])
                b = sum(last[sid]) / len(last[sid])
                trend = "fading" if b > max(1.5, a * 1.4) else ("steady" if b <= a * 1.15 else "drifting")
                mark = "  <-- THIS ONE" if trend == "fading" else ""
                print(f"    servo {sid} {NAMES.get(sid, '?'):11s} {a:5.2f} -> {b:5.2f}   {trend}{mark}")

        volts = [v for _, _, _, v in self.samples if v]
        if volts:
            sag = max(volts) - min(volts)
            verdict = "healthy" if sag < 0.5 else "sagging badly — suspect the supply"
            print(f"\n  supply: {max(volts):.2f} V high, {min(volts):.2f} V low, "
                  f"sag {sag:.2f} V ({verdict})")


def main() -> None:
    p = argparse.ArgumentParser(description="wired servo load diagnostic")
    p.add_argument("--relax", action="store_true", help="unload every servo and exit (the panic button)")
    p.add_argument("--hold", choices=sorted(POSES), help="hold this pose and watch it")
    p.add_argument("--strike", action="store_true", help="one coil -> lurch -> hold")
    p.add_argument("--soak", type=float, metavar="SECONDS",
                   help="run the real hunt motion for this long — the test that reproduces the tone")
    p.add_argument("--seconds", type=float, default=60.0, help="hold time for --hold/--strike (default 60)")
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

    ok = True
    try:
        if a.soak:
            ok = w.soak(a.soak, a.hz, a.abort_deg)
        elif a.strike:
            print("coiling...")
            w.send(POSE_COIL_DEG, COIL_MS)
            time.sleep(COIL_MS / 1000 + 0.3)
            ok = w.hold_and_watch("coiled", 3.0, a.hz, a.abort_deg)
            if ok:
                print("LURCH")
                strike = {sid: POSE_POINT_DEG[sid] for sid in (5, 4, 3)}
                strike[5] = strike[5] + LURCH_OVERSHOOT_DEG
                w.send(strike, LURCH_MS)
                time.sleep(LURCH_MS / 1000)
                w.send({5: POSE_POINT_DEG[5]}, SETTLE_MS)
                time.sleep(SETTLE_MS / 1000 + 0.4)  # let it arrive before we judge it
                print(f"holding full extension for {a.seconds:.0f}s")
                ok = w.hold_and_watch("extended", a.seconds, a.hz, a.abort_deg)
        elif a.hold:
            print(f"moving to the {a.hold} pose...")
            w.send(POSES[a.hold], 1800)
            time.sleep(2.2)
            ok = w.hold_and_watch(a.hold, a.seconds, a.hz, a.abort_deg)
        else:
            print("monitoring only — nothing commanded. Ctrl-C to stop.")
            while True:
                actual, volts = w.read(), w.voltage()
                cells = " ".join(f"{sid}:{actual[sid]:+6.1f}" for sid in JOINTS)
                print((f"  {volts:5.2f}V  " if volts else "    ?    ") + cells)
                time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        w.report()
        w.relax()
        print("\nservos unloaded (arm is limp).")
        if not ok:
            print("Aborted early on drift — see the tables above.")


if __name__ == "__main__":
    main()
