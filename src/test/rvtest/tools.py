"""External tool discovery and display preflight."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from rvtest import proc


def find_tool(name: str, override: str | None = None) -> Path:
    if override:
        if Path(override).exists():
            return Path(override)
        raise FileNotFoundError(f"{name} not found: {override}")
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(f"{name} not found (pass --{name} or add it to PATH)")


def tool_version(path: Path) -> str:
    res = proc.run([str(path), "-version"], timeout=30.0)
    lines = res.output.splitlines()
    return lines[0] if lines else ""


def display_wrapper(software: bool = False) -> list[str]:
    """Command prefix that provides a display; [] when none is needed.

    On Linux the software renderer (OSMesa rvio_sw) is headless, so it needs no display.
    """
    if sys.platform in ("darwin", "win32"):
        return []
    if software:
        return []
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return []
    if shutil.which("xvfb-run"):
        return ["xvfb-run", "-a"]
    raise RuntimeError("no display")
