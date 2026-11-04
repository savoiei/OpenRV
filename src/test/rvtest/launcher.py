"""Re-run a suite under RV's bundled Python."""
from __future__ import annotations

import os
import subprocess
import sys

from .rvresolve import find_rv_python, find_rvio, plugins_python_dir

_IS_WINDOWS = os.name == "nt"
MIN_PYTHON = (3, 10)


def ensure_rv_python(
    argv: list[str],
    rv: str,
    needs_rv_modules: bool = False,
    python_override: str | None = None,
) -> None:
    """Re-run the current script under RV's Python unless already re-executed.

    argv is the full argument vector (script first); the script path is taken
    from sys.argv[0], made absolute.
    """
    if os.environ.get("RVTEST_REEXEC") == "1":
        if tuple(sys.version_info[:2]) < MIN_PYTHON:
            print(
                f"rvtest needs Python >= 3.10, found {sys.version.split()[0]}",
                file=sys.stderr,
            )
            sys.exit(2)
        return None

    env = dict(os.environ)
    try:
        # An explicit --python needs no rvio unless RV's modules are wanted.
        rvio = find_rvio(rv) if (needs_rv_modules or not python_override) else None
        python = str(python_override) if python_override else str(find_rv_python(rvio))
        if needs_rv_modules:
            plugins = plugins_python_dir(rvio)
            if plugins is None:
                raise FileNotFoundError(f"no RV plugins Python dir near {rvio}")
            old = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(plugins) + (os.pathsep + old if old else "")
    except FileNotFoundError as exc:
        print(f"cannot resolve RV Python: {exc}", file=sys.stderr)
        sys.exit(2)

    env["RVTEST_REEXEC"] = "1"
    script = os.path.abspath(sys.argv[0])
    cmd = [python, script, *argv[1:]]
    try:
        if _IS_WINDOWS:
            returncode = subprocess.run(cmd, env=env).returncode
        else:
            os.execve(python, cmd, env)
            return None
    except OSError as exc:
        print(f"cannot start Python {python}: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(returncode)
