"""Parse ##RVSESSION## assertion lines emitted by the generated Mu script.

Ported unchanged in behaviour from the legacy rv-session-test.
"""
from __future__ import annotations

import re

_LINE = re.compile(r"^##RVSESSION## (\d+) \| actual=(.*)$")
_DONE = re.compile(r"^##RVSESSION## DONE\s*$")


def parse_assertions(stdout: str) -> dict[int, str]:
    """Return {op_index: reported sessionFileName()}. Last line wins per index."""
    out: dict[int, str] = {}
    for line in stdout.splitlines():
        m = _LINE.match(line.rstrip("\r"))
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def saw_done(stdout: str) -> bool:
    return any(_DONE.match(line.rstrip("\r")) for line in stdout.splitlines())
