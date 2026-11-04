"""oiiotool wrappers: image diff and its interpretation, size query, constant
colour, absdiff image. Shared by suites."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from . import proc

_TIMEOUT = 60.0
_INF = float("inf")
_MAX_RE = re.compile(r"Max error\s*=\s*([\d.eE+-]+)")
_MEAN_RE = re.compile(r"Mean error\s*=\s*([\d.eE+-]+)")
# "<path> :  512 x  288, 4 channel, half openexr" (anchored on the channel count
# so colons or 'x' in the path cannot match).
_SIZE_RE = re.compile(r":\s+(\d+)\s*x\s*(\d+),\s*\d+\s+channel")
# --stats lines, anchored at line start (indented) so the path line cannot match.
_CONSTANT_RE = re.compile(r"^[ \t]*Constant:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_CONSTANT_COLOR_RE = re.compile(r"^[ \t]*Constant Color:[ \t]*(.*?)[ \t]*(?:\(float\))?[ \t]*$",
                                re.MULTILINE)


@dataclass
class DiffResult:
    match: bool
    max_abs: float
    mean_abs: float
    returncode: int | None
    timed_out: bool
    raw: str


def _num(regex: re.Pattern, text: str) -> float:
    m = regex.search(text)
    if not m:
        return _INF
    try:
        return float(m.group(1))
    except ValueError:
        return _INF


def diff(oiiotool: Path, a: Path, b: Path, timeout: float = _TIMEOUT,
         log_path: Path | None = None) -> DiffResult:
    """Run `oiiotool a b --diff`. Exit 0 = match (errors 0); otherwise parse the
    Max/Mean error lines, with unparseable values reported as inf. A timeout is
    never a match."""
    res = proc.run([str(oiiotool), str(a), str(b), "--diff"],
                   timeout=timeout, log_path=log_path)
    if res.timed_out:
        return DiffResult(False, _INF, _INF, res.returncode, True, res.output)
    if res.returncode == 0:
        return DiffResult(True, 0.0, 0.0, 0, False, res.output)
    return DiffResult(False, _num(_MAX_RE, res.output), _num(_MEAN_RE, res.output),
                      res.returncode, False, res.output)


class CompareError(Exception):
    """oiiotool did not actually compare two images (timeout, crash, read error,
    size mismatch, unparseable output). Callers should record a harness `error`.
    Deliberately not a ValueError/RuntimeError so it is never swallowed by
    accident."""


def is_real_difference(d: DiffResult) -> bool:
    """A non-match is a genuine image difference only if oiiotool --diff reported
    warn/fail (rc 1 or 2) with a finite, positive max error. Anything else (read
    error, crash, signal, missing numbers) is a tool failure."""
    return (d.returncode in (1, 2) and math.isfinite(d.max_abs) and d.max_abs > 0)


def equal_within(d: DiffResult, tol) -> bool:
    """True when the images match or differ by at most tol.max_abs (max error) and
    tol.mean_abs (mean error); False when oiiotool compared them and either number
    is over. `tol` is any object with `max_abs` and `mean_abs`.

    Raises CompareError when oiiotool did not compare the images: timeout, an exit
    code other than 0/1/2, rc 1/2 without both numbers finite, or rc 1/2 with a
    max error that is not positive (the same input is_real_difference calls a
    tool failure). Never returns True for a tool failure."""
    if d.timed_out:
        raise CompareError("oiiotool --diff timed out")
    if d.match and d.returncode == 0:
        return True
    if d.returncode not in (1, 2):
        raise CompareError(f"oiiotool --diff failed (rc={d.returncode}): "
                           f"{d.raw.strip()[:300]}")
    if not (math.isfinite(d.max_abs) and math.isfinite(d.mean_abs)):
        raise CompareError(f"oiiotool --diff printed no usable error numbers "
                           f"(rc={d.returncode}): {d.raw.strip()[:300]}")
    if d.max_abs <= 0:
        raise CompareError(f"oiiotool --diff reported a difference with max error "
                           f"{d.max_abs:g} (rc={d.returncode}): {d.raw.strip()[:300]}")
    return d.max_abs <= tol.max_abs and d.mean_abs <= tol.mean_abs


def parse_constant_color(stats_text: str) -> tuple[float, ...] | None:
    """Parse `oiiotool --stats` output: the channel values when `Constant: Yes`,
    None when `Constant: No`. Raises ValueError when the output is unparseable or
    has more than one `Constant:` line (several subimages)."""
    flags = _CONSTANT_RE.findall(stats_text)
    if len(flags) != 1:
        raise ValueError(f"expected one 'Constant:' line in oiiotool --stats output, "
                         f"found {len(flags)}: {stats_text.strip()[:300]!r}")
    if flags[0] == "No":
        return None
    if flags[0] != "Yes":
        raise ValueError(f"unexpected 'Constant: {flags[0]}' in oiiotool --stats output")
    colors = _CONSTANT_COLOR_RE.findall(stats_text)
    if len(colors) != 1:
        raise ValueError(f"'Constant: Yes' without one 'Constant Color:' line: "
                         f"{stats_text.strip()[:300]!r}")
    try:
        values = tuple(float(v) for v in colors[0].split())
    except ValueError:
        raise ValueError(f"unparseable Constant Color: {colors[0]!r}") from None
    if not values:
        raise ValueError("empty Constant Color line")
    return values


def constant_color(oiiotool: Path, path: Path, timeout: float = _TIMEOUT,
                   log_path: Path | None = None) -> tuple[float, ...] | None:
    """Per-channel colour of a perfectly constant image via `oiiotool --stats`,
    or None when the image is not constant. Raises ValueError when the tool
    fails, times out, or prints something unparseable."""
    res = proc.run([str(oiiotool), "--stats", str(path)],
                   timeout=timeout, log_path=log_path)
    if res.timed_out:
        raise ValueError(f"oiiotool --stats timed out after {timeout:g}s: {path}")
    if res.returncode != 0:
        raise ValueError(f"oiiotool --stats failed (rc={res.returncode}): "
                         f"{res.output.strip()}")
    return parse_constant_color(res.output)


def parse_size(info_text: str) -> tuple[int, int]:
    m = _SIZE_RE.search(info_text)
    if not m:
        raise ValueError(f"cannot parse image size from oiiotool --info output: "
                         f"{info_text!r}")
    return int(m.group(1)), int(m.group(2))


def size(oiiotool: Path, path: Path, timeout: float = _TIMEOUT,
         log_path: Path | None = None) -> tuple[int, int]:
    """Image (width, height) via `oiiotool --info`. Raises ValueError when the
    tool fails, times out, or prints something unparseable."""
    res = proc.run([str(oiiotool), "--info", str(path)],
                   timeout=timeout, log_path=log_path)
    if res.timed_out:
        raise ValueError(f"oiiotool --info timed out after {timeout:g}s: {path}")
    if res.returncode != 0:
        raise ValueError(f"oiiotool --info failed (rc={res.returncode}): "
                         f"{res.output.strip()}")
    return parse_size(res.output)


def absdiff_image(oiiotool: Path, a: Path, b: Path, out: Path, scale: float = 10,
                  timeout: float = _TIMEOUT, log_path: Path | None = None) -> None:
    """Write |a-b| * scale to `out`. Raises RuntimeError on failure or timeout."""
    res = proc.run([str(oiiotool), str(a), str(b), "--absdiff", "--mulc",
                    f"{scale:g}", "-o", str(out)],
                   timeout=timeout, log_path=log_path)
    if res.timed_out:
        raise RuntimeError(f"oiiotool --absdiff timed out after {timeout:g}s")
    if res.returncode != 0:
        raise RuntimeError(f"oiiotool --absdiff failed (rc={res.returncode}): "
                           f"{res.output.strip()}")
