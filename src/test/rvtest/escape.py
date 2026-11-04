"""String escaping helpers for generated session files."""
from __future__ import annotations


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def gto_string(s: str) -> str:
    """Wrap s in double quotes, escaping backslashes and double quotes."""
    return _quote(s)


def mu_string(s: str) -> str:
    """Wrap s in a Mu string literal, escaping backslashes and double quotes.

    Nothing else is escaped: Mu string literals take other characters (spaces,
    single quotes, non-ASCII) verbatim.
    """
    return _quote(s)
