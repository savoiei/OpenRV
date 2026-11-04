"""Console, JSON and JUnit reports for rvtest."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .results import ERROR, FAIL, PASS, SKIP, CaseResult
from .results import exit_code as compute_exit_code
from .results import to_dict


def prepare_out_dir(out_dir: Path) -> None:
    """Create the output tree and delete reports left by a previous run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "history").mkdir(exist_ok=True)
    (out_dir / "artifacts").mkdir(exist_ok=True)
    for name in ("report.json", "junit.xml"):
        (out_dir / name).unlink(missing_ok=True)


def _count(results: list[CaseResult], status: str) -> int:
    return sum(1 for r in results if r.status == status)


def write_json(out_dir: Path, suite: str, meta: dict, results: list[CaseResult],
               exit_code: int | None = None) -> Path:
    out_dir = Path(out_dir)
    now = datetime.now(timezone.utc)
    code = compute_exit_code(results) if exit_code is None else exit_code
    doc = {
        "suite": suite,
        "timestamp": now.isoformat(),
        "meta": meta,
        "summary": {
            "total": len(results),
            "passed": _count(results, PASS),
            "failed": _count(results, FAIL),
            "errors": _count(results, ERROR),
            "skipped": _count(results, SKIP),
            "exit_code": code,
        },
        "results": [to_dict(r) for r in results],
    }
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    history = out_dir / "history"
    history.mkdir(parents=True, exist_ok=True)
    (history / f"report-{now.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(text, encoding="utf-8")
    path = out_dir / "report.json"
    path.write_text(text, encoding="utf-8")
    return path


# XML 1.0 forbids most C0 controls and U+FFFE/U+FFFF; replace them with U+FFFD.
_ILLEGAL_XML = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _xml_safe(text: str) -> str:
    return _ILLEGAL_XML.sub("\ufffd", text)


def _failure_body(r: CaseResult) -> str:
    lines = [
        f"{'ok  ' if c.ok else 'FAIL'} {c.label}: expected {c.expected}, actual {c.actual}"
        for c in r.checks
    ]
    lines.extend(r.log_tail)
    return _xml_safe("\n".join(lines))


def write_junit(out_dir: Path, suite: str, results: list[CaseResult]) -> Path:
    out_dir = Path(out_dir)
    root = ET.Element("testsuite", {
        "name": _xml_safe(suite),
        "tests": str(len(results)),
        "failures": str(_count(results, FAIL)),
        "errors": str(_count(results, ERROR)),
        "skipped": str(_count(results, SKIP)),
        "time": f"{sum(r.duration for r in results):.3f}",
    })
    for r in results:
        classname, _, name = r.name.rpartition("/")
        tc = ET.SubElement(root, "testcase", {
            "classname": _xml_safe(classname), "name": _xml_safe(name), "time": f"{r.duration:.3f}",
        })
        if r.status in (FAIL, ERROR):
            el = ET.SubElement(tc, "failure" if r.status == FAIL else "error",
                               {"message": _xml_safe(r.message)})
            el.text = _failure_body(r)
        elif r.status == SKIP:
            ET.SubElement(tc, "skipped", {"message": _xml_safe(r.skip_reason)})
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "junit.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def console_line(r: CaseResult) -> str:
    return f"{r.status.upper():5} {r.name}  {r.message or r.skip_reason}"


def console_summary(results: list[CaseResult]) -> str:
    lines = [
        f"{len(results)} total: {_count(results, PASS)} passed, {_count(results, FAIL)} failed, "
        f"{_count(results, ERROR)} errors, {_count(results, SKIP)} skipped"
    ]
    reasons = Counter(r.skip_reason for r in results if r.status == SKIP)
    for reason, n in reasons.items():
        lines.append(f"{n} skipped: {reason}")
    return "\n".join(lines)
