"""Timeout-safe subprocess runner that kills the whole process tree."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TERM_GRACE = 5.0  # seconds a timed-out POSIX child gets after SIGTERM before SIGKILL
DRAIN_TIMEOUT = 10.0  # seconds to collect remaining output after the kill

# Process groups of children started by run() that are still in flight (POSIX).
# Every child gets its own session, so a parent killing this process's group
# does not reach them; the SIGTERM handler below kills them explicitly.
_ACTIVE_GROUPS: set[int] = set()


@dataclass
class ProcResult:
    cmd: list[str]
    returncode: int | None
    output: str
    timed_out: bool
    duration: float
    log_path: Path | None

    def tail(self, n: int = 20) -> list[str]:
        return self.output.splitlines()[-n:] if n > 0 else []


def _killpg(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        try:
            proc.kill()
        except OSError:
            pass
    else:
        # start_new_session makes the child its own group leader: pgid == pid.
        if not _killpg(proc.pid, signal.SIGKILL):
            try:
                proc.kill()
            except OSError:
                pass


def kill_active_groups(sig: int | None = None) -> None:
    """Signal (default SIGKILL) every in-flight child process group (POSIX only)."""
    if sys.platform == "win32":
        return
    sig = signal.SIGKILL if sig is None else sig
    for pgid in list(_ACTIVE_GROUPS):
        _killpg(pgid, sig)


def _on_sigterm(signum, frame) -> None:  # noqa: ARG001
    kill_active_groups()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(2)


def install_sigterm_handler():
    """On SIGTERM, kill every in-flight child group, then exit 2 (POSIX only).

    Returns the previous handler (or None if nothing was installed) so the caller
    can restore it. SIGINT/KeyboardInterrupt handling is left untouched.
    """
    if sys.platform == "win32":
        return None
    try:
        return signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:  # not the main thread
        return None


def _drain(proc: subprocess.Popen, timeout: float, partial: bytes) -> bytes:
    try:
        raw, _ = proc.communicate(timeout=timeout)
        return raw or partial
    except subprocess.TimeoutExpired as exc:
        return exc.output or partial


def _stop_on_timeout(proc: subprocess.Popen, partial: bytes) -> bytes:
    if sys.platform == "win32":
        _kill_tree(proc)
        return _drain(proc, DRAIN_TIMEOUT, partial)
    # SIGTERM first so a nested rvtest child can kill its own child groups,
    # then SIGKILL whatever is left of the group. Every wait is bounded.
    pgid = proc.pid
    if not _killpg(pgid, signal.SIGTERM):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        raw, _ = proc.communicate(timeout=TERM_GRACE)
        done = True
        partial = raw or partial
    except subprocess.TimeoutExpired as exc:
        done = False
        partial = exc.output or partial
    _killpg(pgid, signal.SIGKILL)  # stragglers that ignored SIGTERM
    if done:
        return partial
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    return _drain(proc, DRAIN_TIMEOUT, partial)


def run(cmd: list[str], timeout: float = 120.0, log_path: Path | None = None,
        env: dict | None = None, cwd: Path | None = None) -> ProcResult:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(cwd) if cwd is not None else None, **kwargs)
    _ACTIVE_GROUPS.add(proc.pid)
    timed_out = False
    try:
        try:
            raw, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            raw = _stop_on_timeout(proc, exc.output or b"")
        except BaseException:
            _kill_tree(proc)
            _drain(proc, DRAIN_TIMEOUT, b"")
            raise
    finally:
        _ACTIVE_GROUPS.discard(proc.pid)
    duration = time.monotonic() - start
    output = (raw or b"").decode("utf-8", errors="replace")
    returncode = None if timed_out else proc.returncode
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        code = "TIMEOUT" if timed_out else str(returncode)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"$ {shlex.join(cmd)}\nexit: {code}\n"
                    f"duration: {duration:.2f}s\n\n{output}")
    return ProcResult(list(cmd), returncode, output, timed_out, duration, log_path)
