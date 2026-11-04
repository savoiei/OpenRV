"""Declarative model for the session suite (data only, no rvtest imports).

Ported from the legacy rv-session-test model and extended so every op states
three separate things about its path:

  path         the string given to saveSession/setSessionFileName
  relative     False: `path` is a token under the case work dir and the runner
               prefixes the work dir, so RV receives an absolute path.
               True: `path` is passed to RV exactly as written and resolves
               against the rvio cwd (the case work dir).
  expect_name  the expected sessionFileName() afterwards. Absolute ops:
               a token under the work dir (the runner prefixes the work dir).
               Relative ops: the literal string RV reports.
  expect_file  the file that must exist after the op, relative to the work dir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

OP_KINDS = ("save", "save_as_copy", "clear", "set_filename")
GTO_FORMATS = ("text", "gzip-binary")


@dataclass
class Source:
    """A .movieproc source spec (see OpenRV MovieProcedural.h grammar)."""
    kind: str = "smptebars"
    width: int = 256
    height: int = 144
    depth: str = "16f"
    fps: float = 24.0
    start: int = 1
    end: int = 1

    def to_filename(self) -> str:
        parts = [
            self.kind,
            f"width={self.width}",
            f"height={self.height}",
            f"depth={self.depth}",
            f"fps={self.fps}",
            f"start={self.start}",
            f"end={self.end}",
        ]
        return ",".join(parts) + ".movieproc"


@dataclass
class Tolerance:
    max_abs: float = 0.001
    mean_abs: float = 0.0001


@dataclass
class Op:
    """One session mutation and its expected post-state.

    kind: "save" | "save_as_copy" | "clear" | "set_filename"
    expect_name: sessionFileName() after this op ("" for untitled/cleared).
    expect_file: file that must exist on disk after this op (work-dir relative).
    """
    kind: str
    path: Optional[str] = None
    relative: bool = False
    compressed: bool = False
    sparse: bool = True
    expect_name: Optional[str] = None
    expect_file: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in OP_KINDS:
            raise ValueError(f"unknown op kind {self.kind!r}")


@dataclass
class Case:
    """name is session/<section>/<short>.

    precreate_dirs: directories (work-dir relative) created before the run.
    roundtrip / gto_file: work-dir relative files produced by an op's expect_file.
    gto_expect: {"sources": n, "movies": [movieproc filenames in source order]}.
    gto_format: format of the saved structural-check file. "text" (GTOa): the
      runner checks source count and movie names in order. "gzip-binary"
      (compressed save): the runner can only assert gzip-readable, magic 0x29f
      (little-endian u32) and each expected movie name present in the
      decompressed bytes; exact count and order need a binary GTO parser.
    """
    name: str
    section: str
    sources: list[Source]
    ops: list[Op]
    roundtrip: Optional[str] = None
    gto_file: Optional[str] = None
    gto_expect: Optional[dict[str, Any]] = None
    gto_format: str = "text"
    precreate_dirs: list[str] = field(default_factory=list)
    frames: tuple[int, int] = (1, 1)
    tolerance: Tolerance = field(default_factory=Tolerance)
    tier: str = "ci"

    def __post_init__(self) -> None:
        if self.gto_format not in GTO_FORMATS:
            raise ValueError(f"unknown gto_format {self.gto_format!r}")
        parts = self.name.split("/")
        if len(parts) != 3 or parts[0] != "session" or parts[1] != self.section or not parts[2]:
            raise ValueError(f"case name {self.name!r} must be session/{self.section}/<short>")

    @property
    def short(self) -> str:
        return self.name.split("/")[2]

    def frame_numbers(self) -> range:
        return range(self.frames[0], self.frames[1] + 1)
