"""Locate rvio, RV's bundled Python, and related paths."""
from __future__ import annotations

import plistlib
import re
import sys
import tempfile
from pathlib import Path

from . import proc

_PYTHON_NAMES = ("python3", "python3.exe", "python.exe", "python")
_RVIO_NAMES = ("rvio", "rvio.exe")
_RVIO_SW_NAMES = ("rvio_sw", "rvio_sw.exe")
DEFAULT_RV = "/Applications/RV.app"


def _usable(path: Path) -> bool:
    import os

    if not path.is_file():
        return False
    if path.name.lower().endswith(".exe"):
        return True
    return os.access(path, os.X_OK)


def find_rvio(rv: str | Path, sw: bool = False) -> Path:
    """Resolve rvio from the binary itself or an install directory.

    With sw=True resolve the Linux software renderer, rvio_sw (a wrapper script next
    to rvio): from an install dir, or as the sibling of an rvio file. (An rvio_sw
    path is also accepted here, but the suite CLI always resolves rvio first.)
    """
    p = Path(rv)
    names = _RVIO_SW_NAMES if sw else _RVIO_NAMES
    what = "rvio_sw" if sw else "rvio"
    if p.is_file():
        if p.name.lower() in names and _usable(p):
            return p
        if sw and p.name.lower() in _RVIO_NAMES:
            for name in names:
                cand = p.parent / name
                if _usable(cand):
                    return cand
        raise FileNotFoundError(f"no {what} found under {rv}")
    if p.is_dir():
        for sub in (Path("Contents") / "MacOS", Path("bin"), Path(".")):
            for name in names:
                cand = p / sub / name
                if _usable(cand):
                    return cand
    raise FileNotFoundError(f"no {what} found under {rv}")


def software_renderer(rv: str | Path, rvio: Path,
                      platform: str = sys.platform) -> tuple[Path, list[str]]:
    """The rvio to use for software rendering and its prefix args.

    macOS: the same rvio with -swRenderer (fork-only patch, see probe_software_renderer).
    Linux: rvio_sw next to rvio. Windows has no software path.
    """
    if platform == "win32":
        raise RuntimeError("no software renderer on Windows")
    if platform == "darwin":
        return rvio, ["-swRenderer"]
    return find_rvio(rv, sw=True), []


def probe_software_renderer(rvio: Path, rvio_args: list[str], display: list[str] | None = None,
                            timeout: float = 60.0) -> None:
    """Render one 64x36 frame with rvio_args; raise RuntimeError if it cannot."""
    with tempfile.TemporaryDirectory(prefix="rvtest_swprobe_") as tmp:
        out = Path(tmp) / "probe.exr"
        cmd = [*(display or []), str(rvio), *rvio_args, "-err-to-out",
               "smptebars,width=64,height=36,start=1,end=1.movieproc", "-o", str(out)]
        res = proc.run(cmd, timeout=timeout)
        if res.timed_out:
            raise RuntimeError(f"software renderer probe timed out after {timeout:g}s")
        if "unrecognized arg: -swRenderer" in res.output:
            raise RuntimeError(
                f"{rvio} rejects -swRenderer (unrecognized arg): this build lacks the "
                "fork-only -swRenderer patch dacb1e1")
        if res.returncode != 0:
            raise RuntimeError(
                f"software renderer probe failed (exit {res.returncode}): {res.tail()}")
        if not out.is_file():
            raise RuntimeError("software renderer probe produced no output frame")


def find_rv_python(rvio: Path) -> Path:
    """RV's bundled Python: next to rvio, else ../bin (Linux/Windows, unverified)."""
    for d in (rvio.parent, rvio.parent.parent / "bin"):
        for name in _PYTHON_NAMES:
            cand = d / name
            if cand.is_file():
                return cand
    raise FileNotFoundError(f"no RV python found near {rvio}")


def plugins_python_dir(rvio: Path) -> Path | None:
    for cand in (
        rvio.parent.parent / "PlugIns" / "Python",
        rvio.parent.parent / "plugins" / "Python",
    ):
        if cand.is_dir():
            return cand
    return None


def rv_version(rvio: Path) -> str:
    plist = rvio.parent.parent / "Info.plist"
    try:
        with open(plist, "rb") as f:
            return str(plistlib.load(f)["CFBundleShortVersionString"])
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return "unknown"


# e.g. "Version 2026.1.0 (PRE-RELEASE), built on Oct 24 2026 at 06:41:02
#       (HEAD=88499ce ([Open RV] 9da33a55))."
_BUILD_RE = re.compile(
    r"^Version (?P<version>\S+) \((?P<release>[^)]+)\), built on (?P<built>.+?) "
    r"\(HEAD=(?P<rv_commit>[0-9a-fA-F]+) \(\[Open RV\] (?P<openrv_commit>[0-9a-fA-F]+)\)\)\.?$")


def parse_build_line(text: str) -> dict | None:
    """Find rvio's verbose "Version ..." line; split it into fields when recognised."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("Version "):
            continue
        m = _BUILD_RE.match(line)
        return {"line": line, **m.groupdict()} if m else {"line": line}
    return None


def rv_build_info(rvio: Path, display: list[str] | None = None, timeout: float = 60.0,
                  rvio_args: list[str] | None = None) -> dict:
    """Run `rvio -v` on a one-frame procedural source and return its build line.

    Informational only: never raises; on failure returns {"line": None, "error": ...}.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="rvtest_build_") as tmp:
            cmd = [*(display or []), str(rvio), *(rvio_args or []),
                   "-err-to-out", "-v",
                   "smptebars,width=64,height=36,start=1,end=1.movieproc",
                   "-o", str(Path(tmp) / "build.exr")]
            res = proc.run(cmd, timeout=timeout)
    except Exception as exc:  # build info must never stop a run
        return {"line": None, "error": f"{type(exc).__name__}: {exc}"}
    if res.timed_out:
        return {"line": None, "error": f"rvio -v timed out after {timeout:g}s"}
    info = parse_build_line(res.output)
    if info is None:
        return {"line": None, "error": f"no Version line in rvio -v output (exit {res.returncode})"}
    return info


def split_launcher_args(argv: list[str]) -> tuple[str, str | None]:
    """Read --rv and --python from argv without consuming them."""
    values: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        for flag in ("--rv", "--python"):
            if arg == flag and i + 1 < len(argv):
                values[flag] = argv[i + 1]
                i += 1
                break
            if arg.startswith(flag + "="):
                values[flag] = arg[len(flag) + 1:]
                break
        i += 1
    return values.get("--rv", DEFAULT_RV), values.get("--python")
