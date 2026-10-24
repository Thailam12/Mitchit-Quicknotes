#!/usr/bin/env python3
"""Mitchit Quicknotes Studio v1.0 RTM — direct root launcher.

`python main.py` boots exactly the same engine as `./run.sh` or
`python -m mitchit_quicknotes`: every audit, milestone token, geometry lock and
export factory lives inside the package, so this file only resolves the
interpreter, validates the bare-metal runtime and hands the argument vector to
`mitchit_quicknotes.app.main`. Nothing here talks to a network — the whole
studio runs local-first at 0đ cloud cost.
"""

from __future__ import annotations

import importlib.util
import os
import struct
import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent
VENV_PYTHON: Path = ROOT / ".venv" / "bin" / "python"
REQUIREMENTS: Path = ROOT / "requirements.txt"
PACKAGE_NAME: str = "mitchit_quicknotes"
MINIMUM_PYTHON: tuple[int, int] = (3, 9)
REEXEC_GUARD: str = "MITCHIT_QUICKNOTES_REEXEC"
REQUIRED_MODULES: tuple[str, ...] = ("PyQt6", "PIL")


def note(message: str) -> None:
    """Launcher diagnostics go to stderr so stdout keeps the milestone stream pure."""
    try:
        sys.stderr.write(f"[launcher] {message}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def refuse(problem: str, remedy: str) -> int:
    note(problem)
    note(remedy)
    return 1


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def dependencies_present() -> bool:
    return all(module_available(module) for module in REQUIRED_MODULES)


def running_inside(candidate: Path) -> bool:
    """True when this interpreter already belongs to the candidate environment.

    A venv launcher is a symlink chain into the system interpreter, so resolving
    the paths would collapse both environments onto the same binary. Compare the
    executable directory and the active prefix unresolved instead.
    """
    try:
        if Path(sys.executable).parent == candidate.parent:
            return True
        return Path(sys.prefix) == candidate.parent.parent
    except OSError:
        return False


def hand_off_to_venv() -> int | None:
    """Re-exec under the project venv when it holds the toolkit and we do not.

    Returns only when the exec attempt fails, because a successful os.execve
    replaces this process image outright.
    """
    if os.environ.get(REEXEC_GUARD) == "1":
        return None
    if not VENV_PYTHON.is_file() or not os.access(VENV_PYTHON, os.X_OK):
        return None
    if running_inside(VENV_PYTHON) or dependencies_present():
        return None
    argv = [str(VENV_PYTHON), str(ROOT / "main.py"), *sys.argv[1:]]
    environment = dict(os.environ)
    environment[REEXEC_GUARD] = "1"
    environment.pop("PYTHONHOME", None)
    note(f"interpreter lacks PyQt6/Pillow · re-exec {VENV_PYTHON}")
    try:
        os.execve(str(VENV_PYTHON), argv, environment)
    except OSError as failure:
        note(f"re-exec failed ({failure}) · continuing on the current interpreter")
    return None


def verify_runtime() -> str | None:
    """Return a human-readable blocker for the bare-metal runtime, or None."""
    if sys.version_info[:2] < MINIMUM_PYTHON:
        return (
            f"python {sys.version.split()[0]} is too old · "
            f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}+ required"
        )
    if struct.calcsize("P") * 8 != 64:
        return "the studio is engineered for a 64-bit interpreter · a 32-bit host was detected"
    missing = [module for module in REQUIRED_MODULES if not module_available(module)]
    if missing:
        return "missing local toolkit: " + ", ".join(missing)
    return None


def load_engine():
    """Import the package from ROOT regardless of the caller's working directory."""
    entry = str(ROOT)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    from mitchit_quicknotes import app

    return app


def bootstrap(argv: list[str] | None = None) -> int:
    hand_off_to_venv()
    blocker = verify_runtime()
    if blocker is not None:
        remedy = (
            "run ./run.sh once to build .venv, or install locally with "
            f"'{sys.executable}' -m pip install -r {REQUIREMENTS}"
        )
        return refuse(blocker, remedy)
    try:
        app = load_engine()
    except ImportError as failure:
        return refuse(
            f"package {PACKAGE_NAME} failed to import: {failure}",
            f"confirm the package directory exists beside this file at {ROOT / PACKAGE_NAME}",
        )
    except Exception as failure:  # defensive: a broken wheel must not surface as a traceback wall
        return refuse(
            f"engine load failed: {type(failure).__name__}: {failure}",
            "re-run with ./run.sh --selftest for the full compliance report",
        )
    return app.main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    try:
        raise SystemExit(bootstrap())
    except KeyboardInterrupt:
        note("interrupted by the operator")
        raise SystemExit(130) from None
