#!/usr/bin/env python3
"""Wired diagnostic: find out which servo is being overworked, and stop it.

The xArm controller cannot report servo temperature (its USB protocol only
does move, read-position, read-battery, unload), but it does not need to.
A servo losing to gravity sits at a POSITION ERROR: it was told -48 deg and
is holding -50. That error is the load signal, and a servo that is FADING
as it heats shows an error that grows the longer it runs.

The catch: error only means LOAD on a joint that is meant to be still. A
joint the hunt is actively sweeping lags behind its command simply because
it cannot slew that fast, and that lag can be tens of degrees while nothing
is wrong. So every sample is classed per joint:

  HOLDING  its command has not moved for three samples -> error is real sag
  MOVING   its command is still changing -> error is slew lag, not load

Only HOLDING error can trigger the abort. For the joints that never stop
moving (gripper, wrist, base) the signal is the TREND: the soak repeats the
same motion throughout, so lag that grows from early to late is a servo
fading, whatever its absolute value.

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
    COIL_EVERY_S, COIL_HOLD_S, COIL_MS, COIL_STAGGER_S, LURCH_MS, LURCH_OVERSHOOT_DEG,
    POSE_COIL_DEG, POSE_POINT_DEG, POSE_REST_DEG, SETTLE_MS, SHOULDER_SETTLE, TICK,
    Animator, body_pose, lurch_pose, sweep_deg,
)

JOINTS = (1, 2, 3, 4, 5, 6)
POSES = {"point": POSE_POINT_DEG, "coil": POSE_COIL_DEG, "rest": POSE_REST_DEG}


class Watch:
    def __init__(self, debug: bool = False) -> None:
        self.arm = xarm.Controller("USB", debug=debug)
        self.commanded: dict[int, float] = {}
        # (seconds, phase, {servo: (error_deg, holding)}, volts)
        self.samples: list[tuple[float, str, dict[int, tuple[float, bool]], float | None]] = []
        self._cmd_log: dict[int, list[float]] = {}
        self.events: list[tuple[float, str]] = []   # (seconds, what the rig just did)
        self.t0 = time.monotonic()

    # -- talking to the arm ------------------------------------------------ #
    def note(self, what: str) -> None:
        self.events.append((time.monotonic() - self.t0, what))

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
    HOLD_EPS = 0.5   # deg: a command this close to the last one counts as unchanged
    # Samples a command must sit still before we trust the error. At the
    # default 2 Hz this is 2 s, longer than the slowest single move the hunt
    # makes (the 1800 ms coil), so a joint still travelling is never counted
    # as holding.
    HOLD_RUN = 4

    def sample(self, phase: str, quiet: bool = False) -> tuple[int, float]:
        """Record one sample. Returns the worst (servo, |error|) among the
        joints that are HOLDING — the only ones whose error means load."""
        actual, volts = self.read(), self.voltage()
        t = time.monotonic() - self.t0
        row: dict[int, tuple[float, bool]] = {}
        for sid, a in actual.items():
            if sid not in self.commanded:
                continue
            cmd = self.commanded[sid]
            log = self._cmd_log.setdefault(sid, [])
            log.append(cmd)
            del log[:-self.HOLD_RUN]
            holding = len(log) == self.HOLD_RUN and max(log) - min(log) <= self.HOLD_EPS
            row[sid] = (cmd - a, holding)
        self.samples.append((t, phase, row, volts))

        held = {sid: e for sid, (e, h) in row.items() if h}
        worst_sid, worst = max(held.items(), key=lambda kv: abs(kv[1]), default=(0, 0.0))
        if not quiet:
            cells = " ".join(
                f"{sid}:{e:+5.1f}{'' if h else '~'}" for sid, (e, h) in sorted(row.items())
            )
            v = f"{volts:5.2f}V" if volts else "  ?  "
            tail = (f"holding worst: servo {worst_sid} ({NAMES.get(worst_sid, '?')}) {worst:+.1f}"
                    if worst_sid else "nothing settled yet")
            print(f"  [{phase}] t={t:6.1f}s {v}  {cells}   {tail}")
        return worst_sid, abs(worst)

    def watch_through(self, phase: str, seconds: float, period: float = 0.1) -> None:
        """Sample hard for a short window instead of sleeping through it.

        The lurch used to be a blocking sleep, so the one instant that
        matters — the arm arriving at full extension and having to arrest
        and hold it — was never sampled at all."""
        end = time.monotonic() + seconds
        while True:
            self.sample(phase)
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(period, left))

    def hold_and_watch(self, phase: str, seconds: float, hz: float, abort_deg: float) -> bool:
        over = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            sid, worst = self.sample(phase)
            if abort_deg and worst > abort_deg:
                over += 1
                if over >= 3:
                    print(f"\n!! servo {sid} ({NAMES.get(sid, '?')}) is holding {worst:.1f} deg "
                          f"off a STATIONARY target. That is a stall. Unloading now.")
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
        anim = Animator(POSE_POINT_DEG)
        coil, coil_at, strikes, next_sample, over = "out", 4.0, 0, 0.0, 0
        while True:
            now = time.monotonic() - self.t0
            if now > seconds:
                return True
            base = sweep_deg(now, 0.0)

            if now >= coil_at:
                if coil == "out":
                    self.note("coil")
                    anim.to({4: POSE_COIL_DEG[4], 3: POSE_COIL_DEG[3]}, COIL_MS, now)
                    anim.to({5: POSE_COIL_DEG[5]}, COIL_MS, now, delay_s=COIL_STAGGER_S)
                    coil = "coiling"
                    coil_at = now + COIL_STAGGER_S + COIL_MS / 1000
                elif coil == "coiling":
                    coil, coil_at = "coiled", now + random.uniform(*COIL_HOLD_S)
                elif coil == "coiled":
                    strikes += 1
                    self.note("lurch")
                    print(f"  -- strike {strikes} at t={now:.0f}s --")
                    anim.to(lurch_pose(), LURCH_MS, now)
                    coil, coil_at = "settling", now + LURCH_MS / 1000
                else:
                    self.note("arrive")
                    sid = LURCH_OVERSHOOT_JOINT
                    anim.to({sid: POSE_POINT_DEG[sid]}, SETTLE_MS, now)
                    coil, coil_at = "out", now + random.uniform(*COIL_EVERY_S)

            home = anim.pose_at(now)
            nod = 0.6 if coil in ("coiling", "coiled") else 1.0
            self.send(body_pose(now, base, home, nod), int(TICK * 1000) + 80)

            if now >= next_sample:
                phase = {"out": "extended", "settling": "arriving"}.get(coil, coil)
                sid, worst = self.sample(phase)
                if abort_deg and worst > abort_deg:
                    over += 1
                    if over >= 3:
                        print(f"\n!! servo {sid} ({NAMES.get(sid, '?')}) is holding {worst:.1f} deg "
                              f"off a STATIONARY target. That is a stall. Unloading now.")
                        return False
                else:
                    over = 0
                next_sample = now + 1.0 / hz
            time.sleep(TICK)

    # -- the answer ---------------------------------------------------------- #
    @staticmethod
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _collect(self, holding: bool, lo: float = None, hi: float = None) -> dict[int, list[float]]:
        out: dict[int, list[float]] = {}
        for t, _, row, _ in self.samples:
            if lo is not None and not (lo <= t <= hi):
                continue
            for sid, (e, h) in row.items():
                if h is holding:
                    out.setdefault(sid, []).append(abs(e))
        return out

    def report(self) -> None:
        if not self.samples:
            return
        span = self.samples[-1][0] - self.samples[0][0]

        print("\n=== HOLDING: joints told to stay put (error here means real load) ===")
        held = self._collect(True)
        if not held:
            print("    nothing stayed still long enough to measure.")
        for sid, errs in sorted(held.items(), key=lambda kv: self._mean(kv[1]), reverse=True):
            mean = self._mean(errs)
            flag = "  <-- fighting gravity" if mean > 1.5 else ""
            print(f"    servo {sid} {NAMES.get(sid, '?'):11s} mean {mean:5.2f}   peak {max(errs):6.2f}   "
                  f"({len(errs)} samples){flag}")

        print("\n=== MOVING: joints mid-command (error here is slew lag, NOT load) ===")
        moving = self._collect(False)
        for sid, errs in sorted(moving.items(), key=lambda kv: self._mean(kv[1]), reverse=True):
            print(f"    servo {sid} {NAMES.get(sid, '?'):11s} mean {self._mean(errs):5.2f}   "
                  f"peak {max(errs):6.2f}   ({len(errs)} samples)")

        print("\n=== FADING? first third vs last third — THIS is the heat signature ===")
        if span < 120:
            print(f"    run was {span:.0f}s. Heat needs minutes; soak for 10 and let it sing.")
        else:
            t_start = self.samples[0][0]
            lo_hi = (t_start, t_start + span / 3)
            hi_hi = (t_start + 2 * span / 3, self.samples[-1][0])
            for label, is_hold in (("holding", True), ("moving", False)):
                first = self._collect(is_hold, *lo_hi)
                last = self._collect(is_hold, *hi_hi)
                shared = sorted(set(first) & set(last))
                if not shared:
                    continue
                print(f"  [{label}]")
                for sid in shared:
                    a, b = self._mean(first[sid]), self._mean(last[sid])
                    if b > max(1.5, a * 1.4):
                        verdict, mark = "FADING", "  <-- THIS ONE"
                    elif b > a * 1.15:
                        verdict, mark = "drifting", ""
                    else:
                        verdict, mark = "steady", ""
                    print(f"    servo {sid} {NAMES.get(sid, '?'):11s} {a:5.2f} -> {b:5.2f}   "
                          f"{verdict}{mark}")

        rows = [(t, v) for t, _, _, v in self.samples if v]
        if rows:
            volts = [v for _, v in rows]
            hi, lo = max(volts), min(volts)
            print(f"\n=== SUPPLY ===")
            print(f"  {hi:.2f} V high, {lo:.2f} V low, sag {hi - lo:.2f} V")
            if lo < 6.5:
                print("  !! BROWNOUT. Bus servos raise their alarm tone on under-voltage as")
                print("     readily as on heat, and the controller can drop its radio. This is")
                print("     a power problem before it is a motion problem.")
            elif hi - lo > 1.0:
                print("  !  sagging hard under load — the supply is close to its limit.")
            deepest = sorted(rows, key=lambda tv: tv[1])[:6]
            if self.events and deepest:
                print("\n  deepest dips, and what the arm had just been told to do:")
                for t, v in sorted(deepest):
                    prior = [(te, w) for te, w in self.events if te <= t]
                    when = (f"{t - prior[-1][0]:.1f}s after a {prior[-1][1]}"
                            if prior else "before the first move")
                    print(f"    t={t:6.1f}s  {v:5.2f} V   {when}")
                by_move: dict[str, list[float]] = {}
                for t, v in rows:
                    prior = [(te, w) for te, w in self.events if te <= t and t - te <= 2.5]
                    if prior:
                        by_move.setdefault(prior[-1][1], []).append(v)
                if by_move:
                    print("\n  lowest voltage within 2.5 s of each kind of move:")
                    for what, vs in sorted(by_move.items(), key=lambda kv: min(kv[1])):
                        print(f"    {what:6s}  min {min(vs):5.2f} V   mean {sum(vs)/len(vs):5.2f} V "
                              f"({len(vs)} samples)")


def main() -> None:
    p = argparse.ArgumentParser(description="wired servo load diagnostic")
    p.add_argument("--relax", action="store_true", help="unload every servo and exit (the panic button)")
    p.add_argument("--verify-relax", action="store_true",
                   help="unload, then watch the joints while you push the arm — proves whether "
                        "it is really limp instead of guessing from how it feels")
    p.add_argument("--hold", choices=sorted(POSES), help="hold this pose and watch it")
    p.add_argument("--strike", action="store_true", help="one coil -> lurch -> hold")
    p.add_argument("--soak", type=float, metavar="SECONDS",
                   help="run the real hunt motion for this long — the test that reproduces the tone")
    p.add_argument("--seconds", type=float, default=60.0, help="hold time for --hold/--strike (default 60)")
    p.add_argument("--hz", type=float, default=2.0, help="samples per second (default 2)")
    p.add_argument("--abort-deg", type=float, default=12.0,
                   help="unload if a STATIONARY joint stays this far off target (default 12)")
    p.add_argument("--no-abort", action="store_true",
                   help="never stop early — you are listening for the tone yourself")
    p.add_argument("--debug", action="store_true", help="dump raw USB packets")
    a = p.parse_args()
    if a.no_abort:
        a.abort_deg = 0.0  # falsy: the abort checks skip entirely

    w = Watch(debug=a.debug)
    v = w.voltage()
    print(f"connected. supply {v:.2f} V\n" if v else "connected (no voltage read)\n")

    if a.relax:
        w.relax()
        print("all servos unloaded — the arm is limp. No need to pull the power.")
        return

    if a.verify_relax:
        # Whether the arm "feels" stiff is not evidence: these gear trains have
        # real backdrive friction even with no current in them. A servo that is
        # HOLDING, though, will drag itself back to where it was told to be.
        w.relax()
        time.sleep(0.5)
        rest = w.read()
        print("unloaded.\n\nNow PUSH THE ARM AROUND by hand for 12 seconds.\n")
        moved: dict[int, float] = {}
        returned: dict[int, float] = {}
        end = time.monotonic() + 12.0
        while time.monotonic() < end:
            live = w.read()
            for sid, deg in live.items():
                moved[sid] = max(moved.get(sid, 0.0), abs(deg - rest[sid]))
            print("   " + "  ".join(f"{sid}:{live[sid] - rest[sid]:+6.1f}" for sid in JOINTS), end="\r")
            time.sleep(0.15)
        print("\n")
        settled = w.read()
        for sid in JOINTS:
            returned[sid] = abs(settled[sid] - rest[sid])
        budged = [sid for sid in JOINTS if moved.get(sid, 0) > 4.0]
        if not budged:
            print("NOTHING MOVED. Either you did not push it, or the servos still have")
            print("current in them and the unload is not reaching the board.")
        else:
            names = ", ".join(f"{sid} ({NAMES[sid]})" for sid in budged)
            print(f"moved by hand: {names}")
            sprung = [sid for sid in budged if returned[sid] < moved[sid] * 0.25]
            if sprung:
                print(f"but {', '.join(str(s) for s in sprung)} SPRANG BACK to where it started"
                      f" — those are still powered and holding.")
            else:
                print("and they stayed where you put them. That is genuinely unloaded.")
        w.relax()
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
                strike = lurch_pose()
                w.send(strike, LURCH_MS)
                w.watch_through("lurching", LURCH_MS / 1000)
                w.note("arrive")
                if SHOULDER_SETTLE:
                    w.send({5: POSE_POINT_DEG[5]}, SETTLE_MS)
                w.watch_through("arriving", SETTLE_MS / 1000 + 0.4)
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
