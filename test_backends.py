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

    if bad:
        print("\nFAILED:")
        for line in dict.fromkeys(bad):
            print(f"  - {line}")
        return 1
    print("\nbackends: same surface, one definition each, dry one needs no hardware")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
