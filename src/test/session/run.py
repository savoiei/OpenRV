"""Session suite: rvio saves sessions (save, save-as-copy, relative and odd paths), then the suite checks the reported working names, the files written, that
the reloaded session renders like the original sources (in-run reference, not blank), and
the saved session's structure (source count and movie names).

Modes: default test run (rvio + oiiotool); --validate checks the case table for
self-consistency only (stdlib, no rvio, no oiiotool, no reports, no RV re-exec).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_root = next((p for p in HERE.parents if (p / "rvtest" / "__init__.py").is_file()), None)
if _root is None:
    raise SystemExit(f"rvtest package not found above {HERE}")
sys.path.insert(0, str(_root))
sys.path.insert(0, str(HERE))

from rvtest.cli import Case, common_parser, run_suite, select_cases  # noqa: E402
from rvtest.launcher import ensure_rv_python  # noqa: E402
from rvtest.report import console_line, console_summary  # noqa: E402
from rvtest.results import CaseResult, Check, exit_code  # noqa: E402
from rvtest.rvresolve import split_launcher_args  # noqa: E402

from cases import CASES, validate_cases  # noqa: E402
import session  # noqa: E402

TOOLS = ["oiiotool"]


def do_validate(args) -> int:
    selected = select_cases(CASES, args)
    if not selected:
        print("no cases selected")
        return 2
    if args.list:
        for c in selected:
            print(c.name)
        return 0
    results = []
    covered: list[str] = []
    for c in selected:
        mine = validate_cases([c])  # per case: every problem belongs to its case
        covered += mine
        checks = ([Check("self-consistent", "no problems", p, False) for p in mine]
                  or [Check("self-consistent", "no problems", "no problems", True)])
        r = CaseResult(c.name, checks=checks).finalize()
        results.append(r)
        print(console_line(r))
    # Belt and braces: a problem only the whole-selection check reports (suite level)
    # must not be dropped; it becomes its own failing result.
    extra = [p for p in validate_cases(selected) if p not in covered]
    if extra:
        r = CaseResult("session/validate",
                       checks=[Check("self-consistent", "no problems", p, False)
                               for p in extra]).finalize()
        results.append(r)
        print(console_line(r))
    print(console_summary(results))
    return exit_code(results, args.no_skips)


def main(argv: list[str] | None = None) -> int:
    from_cli = argv is None
    if from_cli:
        argv = sys.argv[1:]
    parser = common_parser(__doc__, TOOLS)
    parser.set_defaults(timeout=180)
    parser.add_argument("--validate", action="store_true",
                        help="check the case table for self-consistency (no rvio, no reports)")
    args = parser.parse_args(argv)
    if args.validate:
        return do_validate(args)
    if from_cli:
        rv, python = split_launcher_args(argv)
        ensure_rv_python(sys.argv, rv, python_override=python)
    cases = [Case(c.name, c.tier, lambda ctx, c=c: session.run_case(ctx, c)) for c in CASES]
    return run_suite("session", HERE, cases, args, TOOLS, meta_extra={"mode": "test"})


if __name__ == "__main__":
    sys.exit(main())
