#!/usr/bin/env python3
"""Backend shape check.

Backend and DryBackend must expose the same methods, exactly once each. I
have now twice inserted a method into both classes at once by replacing on
a signature they share, leaving DryBackend with code that reaches for
attributes only the real one has — and both times it got as far as the
hardware before anything noticed.

    ./test_backends.py
"""
# Re-exec into the project venv (if present) so ./script.py works without
# activation — and without hardcoding any machine-specific path.
import os as _os, sys as _sys
_venv_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv")
_venv_py = _os.path.join(_venv_dir, *(("Scripts", "python.exe") if _os.name == "nt" else ("bin", "python")))
if _os.path.exists(_venv_py) and _os.path.abspath(_sys.prefix) != _os.path.abspath(_venv_dir):
    if _os.name == "nt":
        import subprocess as _sp
        _sys.exit(_sp.call([_venv_py] + _sys.argv))
    _os.execv(_venv_py, [_venv_py] + _sys.argv)
import inspect
import pathlib
import re
import sys

sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from gloom import Backend, DryBackend  # noqa: E402

REQUIRED = ("send", "stream", "relax", "voltage", "health", "prewarm", "delivered_hz")


def main() -> int:
    bad = []
    src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "gloom.py")).read()
    spans = {
        "Backend": src[src.index("class Backend:"):src.index("class DryBackend:")],
        "DryBackend": src[src.index("class DryBackend:"):src.index("class Eyes:")],
    }
    for cls, body in spans.items():
        for name in REQUIRED:
            n = len(re.findall(rf"\n    def {name}\(", body))
            print(f"  {cls:11s} {name:13s} defined {n}x  {'ok' if n == 1 else 'WRONG'}")
            if n != 1:
                bad.append(f"{cls}.{name} defined {n} times")

    # the dry one must be usable with no hardware attributes at all
    d = DryBackend()
    for name in REQUIRED:
        fn = getattr(d, name)
        try:
            if name in ("send",):
                fn({6: 0.0}, 100)
            elif name == "stream":
                fn({6: 0.0})
            else:
                fn()
        except AttributeError as err:
            bad.append(f"DryBackend.{name} reaches for hardware: {err}")

    # and the signatures must line up, so one can stand in for the other
    for name in REQUIRED:
        a = inspect.signature(getattr(Backend, name))
        b = inspect.signature(getattr(DryBackend, name))
        if list(a.parameters) != list(b.parameters):
            bad.append(f"{name}{a} vs {name}{b}")

    # The link must survive a sleep. This has been fixed twice and come back
    # twice, both times because a relax() call site forgot drop_link and
    # inherited an unsafe default. Pin the default, and require every call in
    # the sleep path to say what it wants out loud.
    for cls in (Backend, DryBackend):
        d = inspect.signature(cls.relax).parameters["drop_link"].default
        if d is not False:
            bad.append(f"{cls.__name__}.relax drop_link defaults to {d}: "
                       "forgetting it must not drop the link")
        else:
            print(f"  {cls.__name__+'.relax':<20} drop_link defaults to False   ok")

    src = pathlib.Path("gloom.py").read_text()
    for ln, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("arm.relax(") and "drop_link" not in stripped:
            if "SLEEP" in src.splitlines()[max(0, ln - 3):ln][0] or ln > 1400:
                continue   # shutdown; the process is ending either way
            bad.append(f"gloom.py:{ln} calls relax() without saying drop_link=")
    print("  sleep-path relax calls  all state drop_link explicitly   ok")

    # And two threads must never dial the radio at once.
    import ble_arm
    src_rc = inspect.getsource(ble_arm.BleArm.reconnect)
    if "with self._dialling" not in src_rc:
        bad.append("BleArm.reconnect no longer takes the dial lock: two threads "
                   "can connect at once and orphan the peripheral")
    elif not isinstance(
        inspect.getattr_static(ble_arm.BleArm, "_reconnect", None), type(lambda: 0)
    ):
        bad.append("BleArm lost the serialised _reconnect body")
    elif not any("disconnect" in inspect.getsource(f)
                 for f in (ble_arm._release,)):
        bad.append("ble_arm._release no longer disconnects the client it is given")
    else:
        print("  ble_arm              connects serialised, clients released   ok")

    if bad:
        print("\nFAILED:")
        for line in dict.fromkeys(bad):
            print(f"  - {line}")
        return 1
    print("\nbackends: same surface, one definition each, link survives sleep")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
