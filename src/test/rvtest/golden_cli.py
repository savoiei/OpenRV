"""Shared --generate / --promote / --validate plumbing for golden-image suites.

A suite describes itself with a GoldenSuite (name, dirs, cases and three hooks)
and its run.py dispatches to generate(), promote() or validate(). The default
test run stays in the suite (rvtest.cli.run_suite).

Hooks:
  targets(case) -> list of golden keys "<section>/.../<name>" the case owns
                   (render: one per case; a case may own none).
  source(case, key) -> provenance "source" string recorded by --promote.
  generate_case(ctx, case, staging, summary) -> CaseResult: render the case,
                   stage candidate goldens under `staging`, add per-case numbers
                   to `summary` (written into STAGING.json).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import goldens
from .cli import Case, Context, run_suite, select_cases
from .report import console_line, console_summary
from .results import CaseResult, exit_code
from .tools import find_tool


@dataclass
class GoldenSuite:
    name: str                 # generate reports go to suite "<name>-generate"
    suite_dir: Path
    goldens: Path
    tools: list[str]
    cases: Sequence[Any]      # suite case objects with .name and .tier
    targets: Callable[[Any], list[str]]
    source: Callable[[Any, str], str]
    generate_case: Callable[[Context, Any, Path, dict], CaseResult]


def add_mode_arguments(parser: argparse.ArgumentParser, validate_help: str) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true",
                      help="render candidate goldens into <out>/staging (refused in CI)")
    mode.add_argument("--promote", action="store_true",
                      help="copy reviewed staged goldens into goldens/ (refused in CI; "
                           "needs oiiotool only)")
    mode.add_argument("--validate", action="store_true", help=validate_help)
    parser.add_argument("--yes", action="store_true", help="--promote: skip the confirmation")
    parser.add_argument("--allow-sw", action="store_true",
                        help="--promote: allow promoting software-renderer output")


def needs_rv(args: argparse.Namespace) -> bool:
    """--validate and --promote are stdlib + oiiotool only: no RV re-exec."""
    return not (args.validate or args.promote)


def out_dir(spec: GoldenSuite, args: argparse.Namespace) -> Path:
    return Path(args.out_dir) if args.out_dir else Path(spec.suite_dir) / "out"


def _report_meta(out: Path) -> dict:
    try:
        return json.loads((out / "report.json").read_text(encoding="utf-8")).get("meta", {})
    except (OSError, ValueError):
        return {}


def generate(spec: GoldenSuite, args: argparse.Namespace) -> int:
    goldens.refuse_in_ci()
    out = out_dir(spec, args)
    staging = out / "staging"
    summary: dict = {}
    cases = [Case(c.name, c.tier,
                  lambda ctx, c=c: spec.generate_case(ctx, c, staging, summary))
             for c in spec.cases]
    # Own suite name and mode so the green report/JUnit/history of a generate run
    # can never be mistaken for a test run (nothing was compared to a golden).
    suite, meta_extra = f"{spec.name}-generate", {"mode": "generate"}
    if args.list or not select_cases(spec.cases, args):
        # run_suite lists or reports "no cases selected"; staged candidates survive.
        return run_suite(suite, spec.suite_dir, cases, args, spec.tools,
                         meta_extra=meta_extra)
    goldens.fresh_staging(out)
    code = run_suite(suite, spec.suite_dir, cases, args, spec.tools, meta_extra=meta_extra)
    meta = _report_meta(out)
    if meta.get("mode") == "generate":  # never a stale report from a test run
        build_line = (meta.get("rv_build") or {}).get("line")
        goldens.write_staging_info(staging, {
            "build_line": build_line,
            "renderer": meta.get("renderer", "gpu"),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "cases": summary})
        if not build_line:
            print("warning: the RV build line is unknown; --promote will refuse this staging")
    print(f"\nStaged candidate goldens: {staging}")
    if code == 0:
        print("Next: review the *.diff.exr images next to them (see STAGING.json), then run\n"
              "  run.py --promote [--filter X] [--yes]")
    else:
        print("fix the failures above before promoting")
    return code


def promote(spec: GoldenSuite, args: argparse.Namespace) -> int:
    goldens.refuse_in_ci()
    selected = select_cases(spec.cases, args)
    if not selected:
        print("no cases selected")
        return 2
    owned = [(c, key) for c in selected for key in spec.targets(c)]
    names = [key for _c, key in owned] if args.filter else None
    sources = {key: spec.source(c, key) for c, key in owned}
    staging = out_dir(spec, args) / "staging"
    try:
        oiiotool = find_tool("oiiotool", args.oiiotool)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    info = goldens.read_staging_info(staging)  # SystemExit(2) when absent/unreadable
    renderer = info["renderer"]
    print(f"Staging was rendered by: {info['build_line']} ({renderer} renderer)")
    if renderer == "sw" and not args.allow_sw:
        print("error: the staged files are software-renderer output; refusing to promote "
              "them into the golden set (pass --allow-sw to override)", file=sys.stderr)
        return 2
    goldens.promote(staging, spec.goldens, names, info["build_line"], args.yes,
                    oiiotool, sources=sources, renderer=renderer)
    return 0


def validate(selected: list, validate_one: Callable[[Any, Path], CaseResult],
             args: argparse.Namespace) -> int:
    """Run validate_one(case, oiiotool) for each selected case; console only (no
    report files, no RV)."""
    if not selected:
        print("no cases selected")
        return 2
    try:
        oiiotool = find_tool("oiiotool", args.oiiotool)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    results = [validate_one(c, oiiotool).finalize() for c in selected]
    for r in results:
        print(console_line(r))
    print(console_summary(results))
    return exit_code(results, args.no_skips)
