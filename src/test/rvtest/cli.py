"""Suite driver and common CLI: select cases, preflight, run safely, report."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import proc
from .report import (console_line, console_summary, prepare_out_dir, write_json,
                     write_junit)
from .results import CaseResult, errored, exit_code, skipped  # noqa: F401
from .rvresolve import (find_rvio, probe_software_renderer, rv_build_info, rv_version,
                        software_renderer)
from .tools import display_wrapper, find_tool, tool_version


@dataclass
class Context:
    rvio: Path
    rv_version: str
    tools: dict[str, Path]
    args: argparse.Namespace
    artifacts_dir: Path
    display: list[str]
    renderer: str = "gpu"  # "gpu" or "sw"
    rvio_args: list[str] = field(default_factory=list)  # prefix for every rvio call

    def case_dir(self, name: str) -> Path:
        d = self.artifacts_dir / _safe(name)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        return d


@dataclass
class Case:
    name: str
    tier: str  # "ci" or "local"
    run: Callable[[Context], CaseResult]


def _safe(name: str) -> str:
    return name.replace("/", "__")


def select_cases(items: list, args: argparse.Namespace) -> list:
    """Items (anything with .name and .tier) matching --tier and --filter."""
    return [c for c in items
            if args.tier in ("all", c.tier) and (not args.filter or args.filter in c.name)]


def describe_build(info: dict) -> str:
    """One console line for rv_build_info()'s result."""
    return info.get("line") or f"unknown ({info.get('error', 'no build line')})"


def common_parser(description: str, tools: list[str]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--rv", default="/Applications/RV.app", help="RV app bundle or rvio path")
    p.add_argument("--python", default=None, help="Python interpreter override")
    p.add_argument("--tier", choices=["ci", "local", "all"], default="all")
    p.add_argument("--filter", default=None, help="run cases whose name contains this substring")
    p.add_argument("--list", action="store_true", help="list selected cases and exit")
    p.add_argument("--timeout", type=float, default=120, help="per-rvio-call timeout in seconds")
    p.add_argument("--sw-renderer", action="store_true",
                   help="render in software (macOS: -swRenderer, Linux: rvio_sw; not on Windows)")
    p.add_argument("--keep", action="store_true", help="keep artifacts of passing cases")
    p.add_argument("--no-skips", action="store_true", help="treat any skip as failure")
    p.add_argument("--out-dir", default=None, help="output dir (default: <suite dir>/out)")
    p.add_argument("-v", "--verbose", action="store_true")
    for t in tools:
        p.add_argument(f"--{t}", default=None, help=f"path to {t}")
    return p


def _tool_arg(args: argparse.Namespace, tool: str):
    return getattr(args, tool.replace("-", "_"), None)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _run_case(case: Case, ctx: Context) -> CaseResult:
    start = time.monotonic()
    try:
        result = case.run(ctx)
        if not isinstance(result, CaseResult):
            raise TypeError(f"case returned {type(result).__name__}, not CaseResult")
    except Exception as e:  # KeyboardInterrupt/SystemExit deliberately propagate
        result = errored(case.name, f"{type(e).__name__}: {e}")
    if not result.duration:
        result.duration = time.monotonic() - start
    try:
        result.finalize()
    except Exception as e:
        result = errored(case.name, f"{type(e).__name__}: {e}")
        result.finalize()
    return result


def run_suite(suite: str, suite_dir: Path, cases: list[Case], args: argparse.Namespace,
              tools: list[str], meta_extra: dict | None = None) -> int:
    # POSIX: if a parent (the orchestrator's timeout) sends SIGTERM, kill every
    # in-flight rvio/tool group before exiting 2, so nothing is orphaned.
    previous = proc.install_sigterm_handler()
    try:
        return _run_suite(suite, suite_dir, cases, args, tools, meta_extra)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _run_suite(suite: str, suite_dir: Path, cases: list[Case], args: argparse.Namespace,
               tools: list[str], meta_extra: dict | None = None) -> int:
    suite_dir = Path(suite_dir)
    args.rv = os.path.abspath(str(args.rv))  # relative --rv means relative to the caller's cwd
    selected = select_cases(cases, args)
    if not selected:
        print("no cases selected")
        return 2
    if args.list:
        for c in selected:
            print(c.name)
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else suite_dir / "out"
    prepare_out_dir(out_dir)  # before preflight: a stale green report must never survive

    try:
        rvio = find_rvio(args.rv)
        version = rv_version(rvio)
        tool_paths = {t: find_tool(t, _tool_arg(args, t)) for t in tools}
        display = display_wrapper(software=bool(args.sw_renderer))
        renderer, rvio_args = "gpu", []
        if args.sw_renderer:
            rvio, rvio_args = software_renderer(args.rv, rvio)
            renderer = "sw"
            if sys.platform == "darwin":
                probe_software_renderer(rvio, rvio_args, display)
        meta = {
            "rv": str(args.rv),
            "rvio": str(rvio),
            "rv_version": version,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "tools": {n: tool_version(p) for n, p in tool_paths.items()},
            "renderer": renderer,
            "rv_build": rv_build_info(rvio, display, rvio_args=rvio_args),
        }
        meta.update(meta_extra or {})
    except Exception as e:  # includes OSError/PermissionError from tool_version
        _err(f"preflight failed: {type(e).__name__}: {e}")
        return 2

    print(f"RV build: {describe_build(meta['rv_build'])}", flush=True)
    ctx = Context(rvio, version, tool_paths, args, out_dir / "artifacts", display,
                  renderer, rvio_args)

    results: list[CaseResult] = []
    for case in selected:
        r = _run_case(case, ctx)
        results.append(r)
        print(console_line(r), flush=True)
        if r.status == "pass" and not args.keep:
            shutil.rmtree(ctx.artifacts_dir / _safe(case.name), ignore_errors=True)

    code = exit_code(results, args.no_skips)
    write_json(out_dir, suite, meta, results, exit_code=code)
    write_junit(out_dir, suite, results)
    print(console_summary(results))
    return code
