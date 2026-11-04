"""Minimal GTO readers for the session suite's structural checks.

Not full GTO parsers.

Text (GTOa): extracts session.viewNode, the RVFileSource count, the
`media.movie` of every RVFileSource in file order, and scalar string/number
props addressed as "<object>.<component>.<prop>".

Gzip-wrapped binary (compressed saves): check_gzip_binary() only verifies the
container, it does not parse objects.
"""
from __future__ import annotations

import gzip
import re
import struct
import zlib
from dataclasses import dataclass, field

_OBJECT = re.compile(r"^\s*(\S+)\s*:\s*(\S+)\s*\(\d+\)\s*$")
_COMPONENT = re.compile(r"^\s*([A-Za-z_]\w*)\s*$")
_PROP = re.compile(r'^\s*\S+\s+(\w+)\s*=\s*(.+?)\s*$')
_VIEWNODE = re.compile(r'viewNode\s*=\s*"([^"]*)"')

GTO_MAGIC = 0x29F


@dataclass
class ParsedSession:
    view_node: str | None = None
    source_count: int = 0
    props: dict[str, str] = field(default_factory=dict)
    # media.movie of each RVFileSource, in file order.
    movies: list[str] = field(default_factory=list)


def parse_gto(text: str) -> ParsedSession:
    p = ParsedSession()
    cur_obj: str | None = None
    cur_type: str | None = None
    cur_comp: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        mv = _VIEWNODE.search(line)
        if mv:
            p.view_node = mv.group(1)
        mo = _OBJECT.match(line)
        if mo:
            cur_obj, cur_type = mo.group(1), mo.group(2)
            cur_comp = None
            if cur_type == "RVFileSource":
                p.source_count += 1
            continue
        if line.strip() in ("{", "}"):
            if line.strip() == "}":
                cur_comp = None
            continue
        mc = _COMPONENT.match(line)
        if mc and cur_obj is not None:
            cur_comp = mc.group(1)
            continue
        mp = _PROP.match(line)
        if mp and cur_obj is not None and cur_comp is not None:
            name = mp.group(1)
            val = mp.group(2).strip().strip('"')
            p.props[f"{cur_obj}.{cur_comp}.{name}"] = val
            if cur_type == "RVFileSource" and cur_comp == "media" and name == "movie":
                p.movies.append(val)
    return p


def check_gto_expect(parsed: ParsedSession, expect: dict) -> list[str]:
    fails: list[str] = []
    if "viewNode" in expect and parsed.view_node != expect["viewNode"]:
        fails.append(
            f"viewNode: expected {expect['viewNode']!r}, got {parsed.view_node!r}"
        )
    if "sources" in expect and parsed.source_count != expect["sources"]:
        fails.append(
            f"sources: expected {expect['sources']}, got {parsed.source_count}"
        )
    if "movies" in expect and parsed.movies != list(expect["movies"]):
        fails.append(
            f"movies: expected {list(expect['movies'])!r} (in order), got {parsed.movies!r}"
        )
    for key, want in (expect.get("props") or {}).items():
        got = parsed.props.get(key)
        if got != str(want):
            fails.append(f"prop {key}: expected {want!r}, got {got!r}")
    return fails


def check_gzip_binary(data: bytes, expect: dict) -> list[str]:
    """Weak structural check for a compressed (gzip-wrapped binary GTO) save.

    Verifies only: the bytes gunzip; the first 4 bytes are the GTO magic 0x29f
    as a little-endian u32; every name in expect["movies"] occurs somewhere in
    the decompressed bytes. It does NOT check the source count or the order of
    the movie names (that needs a binary GTO parser), so "sources" and the
    ordering of "movies" in `expect` are deliberately ignored. Returns problems
    (empty list = pass).
    """
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as e:
        return [f"gzip: cannot decompress saved session ({type(e).__name__}: {e})"]
    if len(raw) < 4:
        return [f"magic: decompressed data is {len(raw)} bytes, too short for a GTO header"]
    (magic,) = struct.unpack("<I", raw[:4])
    if magic != GTO_MAGIC:
        return [f"magic: expected {GTO_MAGIC:#x} (little-endian u32), got {magic:#x}"]
    return [
        f"movie {name!r} not found in decompressed session"
        for name in expect.get("movies") or []
        if name.encode("utf-8") not in raw
    ]
