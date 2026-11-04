"""One session case: run its ops in rvio, check the reported working names and
the files written, reload the round-trip session and compare its render with an
in-run reference render of the original sources, and check the saved session's
structure.

Every rvio call runs with cwd = <case dir>/work, so relative op paths resolve
there. Layout under the case dir:
  work/       cwd; the case's precreate_dirs and every file the ops save
  write/      ops.mu, throwaway.exr, rvio.log  (write phase)
  ref/        <short>.<frame>.exr, rvio.log    (reference: the sources rendered live)
  roundtrip/  <short>.<frame>.exr, rvio.log    (the saved session reloaded)

Check labels are prefixed `write:`, `ref:`, `roundtrip:` or `structure:`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from gto import check_gto_expect, check_gzip_binary, parse_gto
from mugen import write_mu_text
from muparse import parse_assertions, saw_done
from rvtest import oiio, proc
from rvtest.cli import Context
from rvtest.results import ERROR, CaseResult, Check
from rvtest.rviocheck import output_checks

NOT_COMPARED = "not compared"
NOT_WRITTEN = "not written"
GZIP_LABEL = "structure: gzip-binary (magic + movie names)"


class _CompareFailed(Exception):
    """oiiotool could not compare two images (`error` unless a blocking check failed)."""


def frame_arg(frames: tuple[int, int]) -> str:
    """rvio -t value: "a-b", or "a" for a single frame (as the legacy suite)."""
    return f"{frames[0]}-{frames[1]}" if frames[0] != frames[1] else str(frames[0])


def expected_name(op, work: Path) -> str | None:
    """sessionFileName() expected after `op`: relative ops (and "") literally,
    absolute ops as the work dir joined with the token (as mugen.op_path)."""
    if not op.expect_name or op.relative:
        return op.expect_name
    return os.path.join(str(work), op.expect_name)


def _rvio(ctx: Context, inputs: list[str], tail: list[str], log: Path, work: Path):
    cmd = [*ctx.display, str(ctx.rvio), *ctx.rvio_args, *inputs, *tail]
    return proc.run(cmd, timeout=ctx.args.timeout, log_path=log, cwd=work)


def _blocking(prefix: str, res) -> list[Check]:
    checks = [Check(f"{prefix}: {c.label}", c.expected, c.actual, c.ok)
              for c in output_checks(res)]
    unc = [l for l in res.output.splitlines() if "uncaught exception" in l]
    checks.append(Check(f"{prefix}: no uncaught exception", "none",
                        unc[0] if unc else "none", not unc))
    return checks


def _write_phase(ctx: Context, case, cdir: Path, work: Path, checks: list[Check]):
    wdir = cdir / "write"
    wdir.mkdir(parents=True, exist_ok=True)
    mu = wdir / "ops.mu"
    mu.write_text(write_mu_text(case, str(work)), encoding="utf-8")
    sources = [s.to_filename() for s in case.sources]
    res = _rvio(ctx, [*sources, "-init", str(mu)],
                ["-t", "1", "-err-to-out", "-o", str(wdir / "throwaway.exr")],
                wdir / "rvio.log", work)
    checks += _blocking("write", res)
    done = saw_done(res.output)
    checks.append(Check("write: ops ran", "DONE marker", "present" if done else "missing",
                        done))
    actuals = parse_assertions(res.output)
    for i, op in enumerate(case.ops):
        want = expected_name(op, work)
        if want is not None:
            got = actuals.get(i)
            checks.append(Check(f"write: op {i} sessionFileName", want,
                                "missing" if got is None else got, got == want))
        if op.expect_file:
            f = work / op.expect_file
            ok = f.is_file()
            checks.append(Check(f"write: op {i} file written", str(f),
                                "written" if ok else "missing", ok))
    return res


def _render(ctx: Context, case, phase: str, inputs: list[str], cdir: Path, work: Path,
            checks: list[Check]):
    """Render `inputs` into <cdir>/<phase>; returns (ProcResult, {frame: path or None})."""
    pdir = cdir / phase
    pdir.mkdir(parents=True, exist_ok=True)
    res = _rvio(ctx, inputs, ["-outhalf", "-err-to-out", "-t", frame_arg(case.frames),
                              "-o", str(pdir / f"{case.short}.#.exr")],
                pdir / "rvio.log", work)
    checks += _blocking(phase, res)
    outputs: dict[int, Path | None] = {}
    for n in case.frame_numbers():
        out = pdir / f"{case.short}.{n:04d}.exr"
        ok = out.is_file()
        checks.append(Check(f"{phase}: frame {n} exists", "yes", "yes" if ok else "no", ok))
        outputs[n] = out if ok else None
    return res, outputs


def _writer(case, token: str):
    """The last op that writes `token` (decides whether RV got a relative path)."""
    return next((op for op in reversed(case.ops) if op.expect_file == token), None)


def _roundtrip_phase(ctx: Context, case, cdir: Path, work: Path, checks: list[Check],
                     renders: list, compare_errors: list[str]) -> None:
    sources = [s.to_filename() for s in case.sources]
    res, ref = _render(ctx, case, "ref", sources, cdir, work, checks)
    renders.append(("ref", res, cdir / "ref"))
    tool = ctx.tools["oiiotool"]
    usable_ref: dict[int, Path | None] = {}
    for n, out in ref.items():
        label = f"ref: frame {n} not blank"
        usable_ref[n] = None
        if out is None:
            checks.append(Check(label, "not constant", NOT_COMPARED, False))
            continue
        try:
            colour = oiio.constant_color(tool, out, log_path=cdir / "ref" / f"stats.{n:04d}.log")
        except ValueError:
            checks.append(Check(label, "not constant", "unreadable", False))
            continue
        if colour is not None:
            shown = ", ".join(f"{v:g}" for v in colour)
            checks.append(Check(label, "not constant", f"blank reference ({shown})", False))
            continue
        checks.append(Check(label, "not constant", "not constant", True))
        usable_ref[n] = out

    session_file = work / case.roundtrip
    written = session_file.is_file()
    checks.append(Check("roundtrip: session file", str(session_file),
                        "written" if written else NOT_WRITTEN, written))
    if not written:
        for n in case.frame_numbers():
            checks.append(Check(f"roundtrip: frame {n} equals reference", "equal",
                                NOT_COMPARED, False))
        return
    op = _writer(case, case.roundtrip)
    given = case.roundtrip if op is not None and op.relative else str(session_file)
    res, rt = _render(ctx, case, "roundtrip", [given], cdir, work, checks)
    renders.append(("roundtrip", res, cdir / "roundtrip"))
    for n, out in rt.items():
        label = f"roundtrip: frame {n} equals reference"
        if out is None or usable_ref[n] is None:
            checks.append(Check(label, "equal", NOT_COMPARED, False))
            continue
        d = oiio.diff(tool, out, usable_ref[n], log_path=cdir / "roundtrip" / f"diff.{n:04d}.log")
        try:
            ok = oiio.equal_within(d, case.tolerance)
        except oiio.CompareError as e:
            compare_errors.append(f"{label}: {e}")
            continue
        actual = "equal" if ok else f"differs (max={d.max_abs:g}, mean={d.mean_abs:g})"
        checks.append(Check(label, "equal", actual, ok))


def _structure_phase(case, work: Path, checks: list[Check]) -> None:
    token = case.gto_file or case.roundtrip
    f = work / token
    if not f.is_file():
        checks.append(Check("structure: file written", str(f), NOT_WRITTEN, False))
        return
    expect = case.gto_expect
    movies = list(expect.get("movies") or [])
    if case.gto_format == "gzip-binary":
        label = GZIP_LABEL
        asserted = f"gunzips, magic 0x29f, movies present: {movies}"
        problems = check_gzip_binary(f.read_bytes(), expect)
    else:
        label = "structure: gto"
        parts = []
        if "sources" in expect:
            parts.append(f"{expect['sources']} source{'' if expect['sources'] == 1 else 's'}")
        if "movies" in expect:
            parts.append(f"movies in order: {movies}")
        for key in ("viewNode", "props"):
            if key in expect:
                parts.append(f"{key}: {expect[key]!r}")
        asserted = "; ".join(parts)
        with open(f, encoding="utf-8", errors="replace") as fh:
            problems = check_gto_expect(parse_gto(fh.read()), expect)
    if problems:
        checks += [Check(label, asserted, p, False) for p in problems]
    else:
        checks.append(Check(label, asserted, "as expected", True))


def run_case(ctx: Context, case) -> CaseResult:
    start = time.monotonic()
    # Resolved once: rvio runs with cwd=work, so every path handed to it (Mu file,
    # outputs, absolute op paths, reload path) and every expected absolute name
    # must not depend on the Python cwd or on symlinks (/var -> /private/var).
    cdir = ctx.case_dir(case.name).resolve()
    work = cdir / "work"
    work.mkdir(parents=True, exist_ok=True)
    for d in case.precreate_dirs:
        (work / d).mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    compare_errors: list[str] = []
    res = _write_phase(ctx, case, cdir, work, checks)
    renders = [("write", res, cdir / "write")]
    if case.roundtrip:
        _roundtrip_phase(ctx, case, cdir, work, checks, renders, compare_errors)
    if case.gto_expect is not None:
        _structure_phase(case, work, checks)

    status = message = ""
    blocking_fail = [c for c in checks if c.blocking and not c.ok]
    if compare_errors:
        message = "; ".join(compare_errors)
        if blocking_fail:  # stays `fail` (finalize decides), error appended
            c = blocking_fail[0]
            message = (f"{c.label}: expected {c.expected}, actual {c.actual}; "
                       f"comparison error: {message}")
        else:
            status = ERROR
    elif not blocking_fail:
        n = len(case.ops)
        parts = [f"{n} op{'' if n == 1 else 's'}"]
        if case.roundtrip:
            parts.append("round trip")
        if case.gto_expect is not None:
            parts.append("structure")
        message = ", ".join(parts)

    failing = {c.label.split(":")[0] for c in blocking_fail}
    shown = next((r for r in renders if r[0] in failing), renders[0])
    artifacts = [str(p) for _n, _r, d in renders for p in sorted(d.iterdir())
                 if p.suffix in (".mu", ".log")]
    artifacts += [str(p) for p in sorted(work.rglob("*")) if p.is_file()]
    return CaseResult(case.name, status=status, message=message, checks=checks,
                      log=str(shown[2] / "rvio.log"), log_tail=shown[1].tail(),
                      artifacts=artifacts, duration=time.monotonic() - start)
