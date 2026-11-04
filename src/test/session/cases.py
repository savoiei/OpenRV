"""Session suite case table (data only) and its static self-consistency check.

Ported from the legacy rv-session-test cases: the 6 legacy cases keep their
ops and expectations (tests/fixtures/session_legacy.json pins them) and gained
structural expectations (movies; save_compressed and save_path_with_spaces
gained a structure check). Five `paths` cases fill the legacy relative-path gap with values observed
on RV 2026.1.0 (spike 2026-11-03, rvio cwd = the case work dir).

Every case has a structural check on the saved GTO (gto_expect). For text GTO
it asserts source count and movie names in source order. save_compressed
writes gzip-wrapped binary GTO (gto_format="gzip-binary"): the runner can only
assert gzip-readable, magic 0x29f and each expected movie name present.
"""
from __future__ import annotations

import posixpath

from model import Case, Op, Source


def _ancestors(d: str) -> set[str]:
    out, cur = set(), posixpath.normpath(d)
    while cur not in ("", "."):
        out.add(cur)
        cur = posixpath.dirname(cur)
    return out


def _escapes(token: str) -> bool:
    n = posixpath.normpath(token)
    return token.startswith("/") or n == ".." or n.startswith("../")


def _prefix_dirs(raw: str) -> list[str]:
    """Directories the OS must resolve for `raw`, in order (excluding '..' hops)."""
    parts = [p for p in raw.split("/")[:-1] if p not in ("", ".")]
    out = []
    for i in range(len(parts)):
        if parts[i] != "..":
            out.append("/".join(parts[: i + 1]))
    return out


def validate_cases(cases: list[Case]) -> list[str]:
    """Static self-consistency checks; returns human-readable problems."""
    problems: list[str] = []
    for c in cases:
        dirs: set[str] = set()
        for d in c.precreate_dirs:
            if not d or _escapes(d):
                problems.append(f"{c.name}: bad precreate_dirs entry {d!r}")
            else:
                dirs |= _ancestors(d)

        def need_dir(raw: str, what: str) -> None:
            for p in _prefix_dirs(raw):
                if posixpath.normpath(p) not in dirs:
                    problems.append(f"{c.name}: {what} {raw!r} needs directory "
                                    f"{p!r} in precreate_dirs")
                    return

        written: set[str] = set()
        for i, op in enumerate(c.ops):
            tag = f"{c.name}: op {i} ({op.kind})"
            if op.kind in ("save", "save_as_copy", "set_filename") and not op.path:
                problems.append(f"{tag} missing path")
            if op.kind in ("save", "save_as_copy") and not op.expect_file:
                problems.append(f"{tag} missing expect_file")
            if op.expect_name is None:
                problems.append(f"{tag} missing expect_name")
            if op.path:
                if _escapes(op.path):
                    problems.append(f"{tag} path {op.path!r} escapes the work dir")
                else:
                    need_dir(op.path, "path")
                if (not op.relative and op.kind in ("save", "set_filename")
                        and op.expect_name is not None and op.expect_name != op.path):
                    problems.append(f"{tag} expect_name {op.expect_name!r} must equal "
                                    f"absolute path token {op.path!r}")
            if op.expect_file:
                written.add(op.expect_file)
                if _escapes(op.expect_file):
                    problems.append(f"{tag} expect_file {op.expect_file!r} escapes the work dir")
                else:
                    need_dir(op.expect_file, "expect_file")
                if op.path and op.kind in ("save", "save_as_copy") and \
                        posixpath.normpath(op.path) != posixpath.normpath(op.expect_file):
                    problems.append(f"{tag} expect_file {op.expect_file!r} does not match "
                                    f"normalised path {posixpath.normpath(op.path)!r}")
        for label, token in (("roundtrip", c.roundtrip), ("gto_file", c.gto_file)):
            if token and token not in written:
                problems.append(f"{c.name}: {label} {token!r} never written by any op")
        if c.gto_expect is not None:
            if not (c.gto_file or c.roundtrip):
                problems.append(f"{c.name}: gto_expect set but no gto_file/roundtrip")
            movies = c.gto_expect.get("movies")
            if movies != [s.to_filename() for s in c.sources]:
                problems.append(f"{c.name}: gto_expect movies {movies!r} do not equal "
                                f"the source filenames")
            if c.gto_expect.get("sources") != len(c.sources):
                problems.append(f"{c.name}: gto_expect sources "
                                f"{c.gto_expect.get('sources')!r} != {len(c.sources)} sources")
            target = c.gto_file or c.roundtrip
            compressed = any(op.compressed for op in c.ops
                             if op.expect_file == target and op.kind in ("save", "save_as_copy"))
            want = "gzip-binary" if compressed else "text"
            if target and c.gto_format != want:
                problems.append(f"{c.name}: gto_format {c.gto_format!r} but the saved file "
                                f"{target!r} is {want}")
        else:
            problems.append(f"{c.name}: no gto_expect (every case needs a structural check)")
    return problems


def _expect(*sources: Source) -> dict:
    return {"sources": len(sources), "movies": [s.to_filename() for s in sources]}


_S = Source()
_S64 = Source(width=64)


def _path_case(short: str, path: str, name: str, file: str, *, relative: bool = True,
               dirs: list[str] | None = None) -> Case:
    return Case(
        name=f"session/paths/{short}", section="paths", sources=[Source()],
        ops=[Op("save", path=path, relative=relative, expect_name=name, expect_file=file)],
        roundtrip=file, gto_expect=_expect(_S), precreate_dirs=dirs or [])


CASES: list[Case] = [
    # --- section: save_as_copy (the PR #1303 guard) --------------------------
    Case(
        name="session/save_as_copy/save_copy_preserves_working_name",
        section="save_as_copy",
        sources=[Source()],
        ops=[
            Op("save", path="work.rv", expect_name="work.rv", expect_file="work.rv"),
            Op("save_as_copy", path="copy.rv", expect_name="work.rv", expect_file="copy.rv"),
            Op("save", path="work2.rv", expect_name="work2.rv", expect_file="work2.rv"),
        ],
        roundtrip="copy.rv",
        gto_expect=_expect(_S),
    ),
    # --- section: save -------------------------------------------------------
    Case(
        name="session/save/save_sets_working_name",
        section="save",
        sources=[Source()],
        ops=[Op("save", path="a.rv", expect_name="a.rv", expect_file="a.rv")],
        roundtrip="a.rv",
        gto_expect=_expect(_S),
    ),
    # --- section: save_as (save to a second path retargets) ------------------
    Case(
        name="session/save_as/save_as_retargets_working_name",
        section="save_as",
        sources=[Source(), Source(width=64)],
        ops=[
            Op("save", path="one.rv", expect_name="one.rv", expect_file="one.rv"),
            Op("save", path="two.rv", expect_name="two.rv", expect_file="two.rv"),
        ],
        roundtrip="two.rv",
        gto_expect=_expect(_S, _S64),
    ),
    # --- section: options ------------------------------------------------------
    Case(
        name="session/options/save_compressed",
        section="options",
        sources=[Source()],
        ops=[Op("save", path="c.rv", compressed=True, expect_name="c.rv", expect_file="c.rv")],
        roundtrip="c.rv",  # compressed still reloads+renders identically
        gto_expect=_expect(_S),
        gto_format="gzip-binary",
    ),
    Case(
        name="session/options/save_complete_nonsparse",
        section="options",
        sources=[Source()],
        ops=[Op("save", path="full.rv", sparse=False, expect_name="full.rv",
                expect_file="full.rv")],
        roundtrip="full.rv",
        gto_expect=_expect(_S),
    ),
    # --- section: paths --------------------------------------------------------
    Case(
        name="session/paths/save_path_with_spaces",
        section="paths",
        sources=[Source()],
        ops=[Op("save", path="my session.rv", expect_name="my session.rv",
                expect_file="my session.rv")],
        roundtrip="my session.rv",
        gto_expect=_expect(_S),
    ),
    # Relative paths resolve against the rvio cwd (the case work dir). The
    # working names are the literal strings RV reported on 2026.1.0 (spike S6).
    _path_case("save_relative_path", "rel.rv", "rel.rv", "rel.rv"),
    _path_case("save_dot_relative_path", "./dot.rv", "./dot.rv", "dot.rv"),
    _path_case("save_parent_segment_path", "sub/../dotdot.rv", "sub/../dotdot.rv",
               "dotdot.rv", dirs=["sub"]),
    _path_case("save_double_separator_path", "sub//double.rv", "sub/double.rv",
               "sub/double.rv", dirs=["sub"]),
    # Absolute (under the work dir); the quote must be escaped in the Mu script.
    _path_case("save_path_with_quote", 'quote"name.rv', 'quote"name.rv', 'quote"name.rv',
               relative=False),
]
