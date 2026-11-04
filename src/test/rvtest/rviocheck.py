"""Blocking checks every rvio-driven case shares: exit status and error lines."""
from __future__ import annotations

from .proc import ProcResult
from .results import Check


def output_checks(res: ProcResult) -> list[Check]:
    """rvio exit (TIMEOUT shown), no ERROR: lines, no GL_ERROR."""
    code = "TIMEOUT" if res.timed_out else str(res.returncode)
    lines = res.output.splitlines()
    err_lines = [l for l in lines if l.startswith("ERROR:")]
    gl_lines = [l for l in lines if "GL_ERROR" in l]
    return [
        Check("rvio exit", "0", code, code == "0"),
        Check("no ERROR lines", "none", err_lines[0] if err_lines else "none", not err_lines),
        Check("no GL_ERROR", "none", gl_lines[0] if gl_lines else "none", not gl_lines),
    ]
