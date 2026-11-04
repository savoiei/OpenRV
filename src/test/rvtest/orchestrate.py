"""Orchestrator: run native and legacy suites as subprocesses and merge results."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import proc
from .report import write_json, write_junit
from .results import ERROR, FAIL, PASS, CaseResult, Check
from .rvresolve import find_rv_python, find_rvio, rv_build_info
from .tools import display_wrapper

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 1800
LEGACY_SUFFIX = "(exit code only, not verified)"


@dataclass
class SuiteOutcome:
    name: str
    type: str
    status: str
    exit_code: int | None
    duration: float
    report: dict | None
    log_path: Path
    message: str = ""
    verified: bool = False  # native only: fresh report agreeing with the exit code


def load_config(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = []
    for raw in data["suites"]:
        e = dict(raw)
        e.setdefault("note", "")
        e.setdefault("args", [])
        e.setdefault("accepts_rv", False)
        e.setdefault("python", "system")
        e["timeout"] = e.get("timeout") or DEFAULT_TIMEOUT
        if e["type"] == "legacy":
            e["path"] = str(Path(e["path"]).expanduser())
        entries.append(e)
    return entries


def select(entries: list[dict], suite: str | None) -> list[dict]:
    if suite is None:
        return [e for e in entries if e["enabled"]]
    for e in entries:
        if e["name"] == suite:
            if not e["enabled"]:
                print(f"NOTE: {e['name']} is disabled: {e.get('note', '')}")
            return [e]
    raise ValueError(f"unknown suite {suite!r}; known: {', '.join(e['name'] for e in entries)}")


def _script_path(entry: dict) -> Path:
    p = Path(entry["path"])
    return p if p.is_absolute() else REPO / p


def _resolve_python(entry: dict, rv: str, script_dir: Path) -> str:
    if entry["type"] == "native":
        return sys.executable
    spec = entry.get("python", "system")
    if spec == "system":
        found = shutil.which("python3")
        if not found:
            raise FileNotFoundError("python3 not found on PATH")
        return found
    if spec == "rv":
        return str(find_rv_python(find_rvio(rv)))
    cand = script_dir / spec
    if not cand.is_file():
        raise FileNotFoundError(f"interpreter not found: {cand}")
    return str(cand)


def _fresh_report(script_dir: Path, started: float) -> dict | None:
    path = script_dir / "out" / "report.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(doc["timestamp"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.timestamp() >= started:
            return doc
        reason = "is older than this run"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = f"is missing or unreadable ({exc.__class__.__name__})"
    print(f"WARNING: {path} {reason}; ignoring it")
    return None


def _status_from_code(code: int | None) -> str:
    return {0: PASS, 1: FAIL}.get(code, ERROR) if code is not None else ERROR


def _verify_native(report: dict | None, returncode: int | None) -> str:
    """Why a native suite's report cannot be trusted, or "" if it can.

    A native suite is only green when its fresh report agrees with its exit
    code and holds at least one passing result (spec section 2: a green result
    always means something was asserted).
    """
    if report is None:
        return "no fresh report.json"
    summary = report.get("summary")
    reported = summary.get("exit_code") if isinstance(summary, dict) else None
    if reported != returncode:
        return f"report.json summary.exit_code {reported!r} != process exit {returncode!r}"
    results = report.get("results")
    if returncode == 0 and not any(
            isinstance(r, dict) and r.get("status") == PASS for r in results or []):
        return "report.json has no passing result"
    return ""


def run_entry(entry: dict, rv: str, out_dir: Path, started: float) -> SuiteOutcome:
    name = entry["name"]
    rv = os.path.abspath(str(rv))  # children run with cwd=their own folder
    script = _script_path(entry)
    script_dir = script.parent
    log_path = Path(out_dir) / "logs" / f"{name}.log"
    try:
        python = _resolve_python(entry, rv, script_dir)
    except FileNotFoundError as exc:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        msg = f"could not resolve interpreter: {exc}"
        log_path.write_text(msg + "\n", encoding="utf-8")
        return SuiteOutcome(name, entry["type"], ERROR, None, 0.0, None, log_path, msg)
    cmd = [python, str(script)]
    if entry.get("accepts_rv"):
        cmd += ["--rv", rv]
    cmd += list(entry.get("args", []))
    # A re-executed orchestrator must not make its children skip their own re-exec.
    env = {k: v for k, v in os.environ.items() if k != "RVTEST_REEXEC"}
    try:
        res = proc.run(cmd, timeout=entry.get("timeout", DEFAULT_TIMEOUT),
                       log_path=log_path, env=env, cwd=script_dir)
    except OSError as exc:
        msg = f"cannot launch {python}: {exc.__class__.__name__}: {exc}"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(msg + "\n", encoding="utf-8")
        return SuiteOutcome(name, entry["type"], ERROR, None, 0.0, None, log_path, msg)
    status = ERROR if res.timed_out else _status_from_code(res.returncode)
    message = "timed out" if res.timed_out else ""
    report = None
    verified = False
    if entry["type"] == "native":
        report = _fresh_report(script_dir, started)
        why = _verify_native(report, res.returncode)
        verified = not why
        if why:
            message = f"{message}; {why}" if message else why
            if status == PASS:
                status = ERROR
    return SuiteOutcome(name, entry["type"], status, res.returncode, res.duration,
                        report, log_path, message, verified)


def display(o: SuiteOutcome) -> str:
    if o.type == "legacy":
        return f"LEGACY {o.status.upper()} {o.name} {LEGACY_SUFFIX}"
    return f"{o.status.upper()} {o.name}" + (f" ({o.message})" if o.message else "")


def overall_exit(outcomes: list[SuiteOutcome]) -> int:
    statuses = [o.status for o in outcomes]
    if FAIL in statuses:
        return 1
    if ERROR in statuses or not outcomes:
        return 2
    return 0


def _case_from_dict(d: dict) -> CaseResult:
    checks = [Check(**c) for c in d.get("checks", [])]
    fields = {k: v for k, v in d.items() if k in CaseResult.__dataclass_fields__ and k != "checks"}
    return CaseResult(checks=checks, **fields)


def build_results(outcomes: list[SuiteOutcome]) -> list[CaseResult]:
    results: list[CaseResult] = []
    for o in outcomes:
        suite_results = []
        if o.type == "native" and o.report is not None:
            suite_results = [_case_from_dict(d) for d in o.report.get("results") or []]
            results.extend(suite_results)
        if o.type == "legacy" or not o.verified or not suite_results:
            code = "none" if o.exit_code is None else str(o.exit_code)
            msg = f"{o.name}: exit code {code}"
            if o.type == "legacy":
                msg += f" {LEGACY_SUFFIX}"
            if o.message:
                msg += f" ({o.message})"
            results.append(CaseResult(
                f"legacy/{o.name}" if o.type == "legacy" else f"native/{o.name}",
                status=o.status, message=msg, duration=o.duration, log=str(o.log_path),
                checks=[Check("exit code", "0", code, o.status == PASS)]))
    return results


def build_info_for(rv: str) -> dict:
    """rvio's build line for the orchestrator report; never raises."""
    try:
        rvio = find_rvio(rv)
    except FileNotFoundError as exc:
        return {"line": None, "error": str(exc)}
    try:
        display = display_wrapper()
    except RuntimeError:
        display = []  # suites will report the missing display themselves
    return rv_build_info(rvio, display)


def write_reports(out_dir: Path, outcomes: list[SuiteOutcome], meta: dict) -> None:
    results = build_results(outcomes)
    meta = dict(meta)
    meta["suites"] = [
        {"name": o.name, "type": o.type, "status": o.status, "exit_code": o.exit_code,
         "duration": round(o.duration, 3), "log": str(o.log_path),
         "label": display(o), "verified": o.verified, "message": o.message}
        for o in outcomes]
    write_json(out_dir, "orchestrator", meta, results, exit_code=overall_exit(outcomes))
    write_junit(out_dir, "orchestrator", results)


def now() -> float:
    return time.time()
