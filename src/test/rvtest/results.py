"""Result model and exit-code contract for rvtest."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

PASS = "pass"
FAIL = "fail"
ERROR = "error"
SKIP = "skip"


@dataclass
class Check:
    label: str
    expected: str
    actual: str
    ok: bool
    blocking: bool = True


@dataclass
class CaseResult:
    name: str
    status: str = ""
    checks: list[Check] = field(default_factory=list)
    message: str = ""
    log: str | None = None
    log_tail: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    duration: float = 0.0
    skip_reason: str = ""

    def finalize(self) -> CaseResult:
        if self.status == SKIP:
            if not self.skip_reason:
                raise ValueError(f"{self.name}: skip requires a non-empty skip_reason")
            return self
        if self.status not in ("", PASS, FAIL, ERROR):
            raise ValueError(f"{self.name}: unknown status {self.status!r}")
        if self.status in ("", PASS):
            self.status = ""
            blocking = [c for c in self.checks if c.blocking]
            failing = [c for c in blocking if not c.ok]
            if failing:
                self.status = FAIL
            elif not blocking:
                self.status = ERROR
                if not self.message:
                    self.message = "no checks recorded"
            else:
                self.status = PASS
        if self.status == FAIL and not self.message:
            for c in self.checks:
                if c.blocking and not c.ok:
                    self.message = f"{c.label}: expected {c.expected}, actual {c.actual}"
                    break
        return self


def skipped(name: str, reason: str) -> CaseResult:
    return CaseResult(name, status=SKIP, skip_reason=reason).finalize()


def errored(name: str, message: str) -> CaseResult:
    return CaseResult(name, status=ERROR, message=message)


def exit_code(results: list[CaseResult], no_skips: bool = False) -> int:
    """0 = all passed; 1 = any fail (or skip with no_skips); 2 = error/nothing ran."""
    statuses = [r.status for r in results]
    if FAIL in statuses:
        return 1
    if ERROR in statuses:
        return 2
    if not any(s == PASS for s in statuses):
        return 2
    if no_skips and SKIP in statuses:
        return 1
    return 0


def to_dict(r: CaseResult) -> dict:
    return asdict(r)
