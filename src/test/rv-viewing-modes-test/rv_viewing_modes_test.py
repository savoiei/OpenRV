#!/usr/bin/env python3
"""rv_viewing_modes_test -- regression suite for RV's viewing modes (Stack,
Layout, Sequence, Retime).

Each Case is declarative: a list of .movieproc sources, a view_mode, a
mechanism ("mu" -> generate a -init script that calls setViewNode and applies
property overrides; "session" -> generate a .rv GTO session file), and an
optional frame range. The runner drives rvio and pixel-diffs the result(s)
against golden EXRs via oiiotool.

This tests *both* RV access paths real users hit: the Mu command surface and
the .rv session-file persistence format. Divergence between the two on the
same logical configuration is itself a finding.

  python rv_viewing_modes_test.py --generate     # build goldens once
  python rv_viewing_modes_test.py                # test against goldens
  python rv_viewing_modes_test.py --validate     # detect silent no-ops

Sibling to rv-render-test and rv-ocio-test; helpers (rvio_binary, oiio_diff)
are duplicated for now -- factor out into rv-test-common once stabilized.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
GOLDENS_DIR = HERE / "goldens"
OUT_DIR = HERE / "out"


# ---------------------------------------------------------------------------
# Source spec (same .movieproc grammar as rv-render-test)
# ---------------------------------------------------------------------------


@dataclass
class Source:
    """A .movieproc source spec -- see OpenRV MovieProcedural.h for the grammar."""

    kind: str = "smptebars"
    width: int = 256
    height: int = 144
    depth: str = "16f"
    fps: float = 24.0
    start: int = 1
    end: int = 1
    red: Optional[float] = None
    green: Optional[float] = None
    blue: Optional[float] = None
    alpha: Optional[float] = None
    hpan: Optional[int] = None        # horizontal pan pixels/frame -- animates content

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
        for name, v in (
            ("red", self.red), ("green", self.green),
            ("blue", self.blue), ("alpha", self.alpha),
            ("hpan", self.hpan),
        ):
            if v is not None:
                parts.append(f"{name}={v}")
        return ",".join(parts) + ".movieproc"


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------


VIEW_NODES = {
    "stack": "defaultStack",
    "layout": "defaultLayout",
    "sequence": "defaultSequence",
}


@dataclass
class Tolerance:
    max_abs: float = 0.001
    mean_abs: float = 0.0001


@dataclass
class Case:
    name: str
    sources: list[Source]
    view_mode: str                                # "stack" | "layout" | "sequence"
    mechanism: str = "mu"                         # "mu" | "session"
    props: dict[str, Any] = field(default_factory=dict)
    # Inclusive frame range. (1, 1) renders one frame as name.0001.exr.
    # Multi-frame (e.g. (1, 5)) is needed for Sequence/Retime to actually
    # exercise time mechanics.
    frames: tuple[int, int] = (1, 1)
    tolerance: Tolerance = field(default_factory=Tolerance)
    section: str = "misc"

    def frame_numbers(self) -> range:
        return range(self.frames[0], self.frames[1] + 1)

    def _file_for(self, root: Path, n: int) -> Path:
        return root / self.section / self.mechanism / f"{self.name}.{n:04d}.exr"

    def golden_for_frame(self, n: int) -> Path:
        return self._file_for(GOLDENS_DIR, n)

    def out_for_frame(self, n: int, root: Path) -> Path:
        return self._file_for(root, n)

    def output_pattern(self, root: Path) -> str:
        """rvio -o pattern, using '#' for the frame placeholder."""
        return str(root / self.section / self.mechanism / f"{self.name}.#.exr")


# ---------------------------------------------------------------------------
# rvio resolution (copied from rv-render-test)
# ---------------------------------------------------------------------------


def rvio_binary(rv_path: Path) -> Path:
    p = Path(rv_path)
    is_windows = sys.platform.startswith("win")

    def is_exe(x: Path) -> bool:
        if not x.is_file():
            return False
        if is_windows:
            return x.suffix.lower() == ".exe"
        return os.access(x, os.X_OK)

    if is_exe(p):
        return p

    names = ["rvio.exe", "rvio"] if is_windows else ["rvio"]
    for sub in (Path("Contents") / "MacOS", Path("bin"), Path()):
        for n in names:
            c = p / sub / n
            if is_exe(c):
                return c
    raise FileNotFoundError(f"no rvio found under {rv_path}")


def rvio_label(rvio_path: Path) -> str:
    for parent in rvio_path.parents:
        if parent.name.endswith(".app"):
            return parent.name[: -len(".app")]
    if rvio_path.parent.name in ("bin", "MacOS"):
        return rvio_path.parent.parent.name or rvio_path.stem
    return rvio_path.parent.name or rvio_path.stem


# ---------------------------------------------------------------------------
# Mu -init generation
# ---------------------------------------------------------------------------


def _mu_value(value: Any) -> tuple[str, str]:
    """Return (Mu setter name, literal expression) for `value`."""
    if isinstance(value, bool):
        return "setIntProperty", f"int[]{{{1 if value else 0}}}"
    if isinstance(value, int):
        return "setIntProperty", f"int[]{{{value}}}"
    if isinstance(value, float):
        return "setFloatProperty", f"float[]{{{value!r}}}"
    if isinstance(value, str):
        return "setStringProperty", f"string[]{{{json.dumps(value)}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("empty list value")
        if all(isinstance(v, bool) for v in value):
            xs = ",".join("1" if v else "0" for v in value)
            return "setIntProperty", f"int[]{{{xs}}}"
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            xs = ",".join(str(v) for v in value)
            return "setIntProperty", f"int[]{{{xs}}}"
        if all(isinstance(v, (int, float)) for v in value):
            xs = ",".join(repr(float(v)) for v in value)
            return "setFloatProperty", f"float[]{{{xs}}}"
        if all(isinstance(v, str) for v in value):
            xs = ",".join(json.dumps(v) for v in value)
            return "setStringProperty", f"string[]{{{xs}}}"
    raise TypeError(f"unsupported property value: {value!r}")


def generate_mu_init(case: Case, out: Path) -> Path:
    """Build a -init.mu that switches view and applies property overrides."""
    view_node = VIEW_NODES.get(case.view_mode)
    if view_node is None and case.view_mode != "default":
        raise ValueError(
            f"unknown view_mode {case.view_mode!r}; expected stack/layout/sequence"
        )

    lines = [
        "// auto-generated by rv_viewing_modes_test.py -- do not edit",
        "require commands;",
        "",
        '\\: apply (void;)',
        "{",
        '    print("rvt_viewing: applying setup\\n");',
    ]
    if view_node:
        lines.append(f'    commands.setViewNode("{view_node}");')

    for path, value in case.props.items():
        setter, literal = _mu_value(value)
        if path.startswith("#"):
            type_name, prop = path[1:].split(".", 1)
            lines.append(
                f'    for_each (n; commands.nodesOfType("{type_name}"))'
            )
            lines.append(
                f'        commands.{setter}(n + ".{prop}", {literal}, true);'
            )
        else:
            lines.append(
                f'    commands.{setter}("{path}", {literal}, true);'
            )

    lines += [
        "}",
        "",
        "\\: apply_ev (void; Event ev) { if (ev neq nil) ev.reject(); apply(); }",
        "",
        "apply();",
        'commands.bind("default", "global", "source-group-complete",'
        ' apply_ev, "rvt_viewing");',
        'commands.bind("default", "global", "after-session-read",'
        ' apply_ev, "rvt_viewing");',
    ]
    out.write_text("\n".join(lines) + "\n")
    return out


# ---------------------------------------------------------------------------
# Session file (.rv GTO) generation
# ---------------------------------------------------------------------------
#
# Minimum viable session file (empirically verified):
#   - rv : RVSession block with session.viewNode
#   - one sourceGroup000NNN_source : RVFileSource block per source with
#     media.movie pointing at the .movieproc filename
# RV fills in every other default node automatically.
#
# For property overrides, the case must use absolute node paths because the
# session file targets specific named nodes. Standard view-mode nodes:
#   defaultStack          (RVStackGroup)
#   defaultStack_stack    (RVStack -- composite.type, composite.dissolveAmount)
#   defaultLayout         (RVLayoutGroup -- layout.mode, gridRows, ...)
#   defaultSequence       (RVSequenceGroup)
#   defaultSequence_sequence (RVSequence -- edl.*)
#   defaultStack_rt_sourceGroup000000 (RVRetime -- visual.scale, visual.offset)


def _gto_value(value: Any) -> tuple[str, str]:
    """Return (GTO type, GTO literal) for `value`."""
    if isinstance(value, bool):
        return "int", "1" if value else "0"
    if isinstance(value, int):
        return "int", str(value)
    if isinstance(value, float):
        return "float", repr(value)
    if isinstance(value, str):
        return "string", f'"{value}"'
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("empty list value")
        if all(isinstance(v, bool) for v in value):
            xs = " ".join("1" if v else "0" for v in value)
            return "int", f"[ {xs} ]"
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            xs = " ".join(str(v) for v in value)
            return "int", f"[ {xs} ]"
        if all(isinstance(v, (int, float)) for v in value):
            xs = " ".join(repr(float(v)) for v in value)
            return f"float[{len(value)}]", f"[ [ {xs} ] ]"
        if all(isinstance(v, str) for v in value):
            xs = " ".join(f'"{v}"' for v in value)
            return "string", f"[ {xs} ]"
    raise TypeError(f"unsupported GTO value: {value!r}")


# Map node-type aliases to their canonical node names in the default session.
# Used when a Case's props key starts with "#NodeType" -- we resolve to a real
# node path so the session block can be emitted. View-mode-specific (e.g.
# "#RVStack" resolves differently depending on view_mode).
_NODE_TYPE_RESOLUTION = {
    "stack": {
        "RVStack":         "defaultStack_stack",
        "RVStackGroup":    "defaultStack",
        "RVRetime0":       "defaultStack_rt_sourceGroup000000",
        "RVRetime1":       "defaultStack_rt_sourceGroup000001",
    },
    "layout": {
        "RVLayoutGroup":   "defaultLayout",
        "RVStack":         "defaultLayout_stack",
        "RVRetime0":       "defaultLayout_rt_sourceGroup000000",
        "RVRetime1":       "defaultLayout_rt_sourceGroup000001",
    },
    "sequence": {
        "RVSequence":      "defaultSequence_sequence",
        "RVSequenceGroup": "defaultSequence",
        "RVRetime0":       "defaultSequence_rt_sourceGroup000000",
        "RVRetime1":       "defaultSequence_rt_sourceGroup000001",
    },
}


# Real RV node type for each canonical default-session node name. Required by
# the session emitter -- declaring a node with the placeholder "RVNode" causes
# rvio to silently ignore the property block (no error, just no effect).
_NODE_TYPES = {
    # view groups
    "defaultStack":            "RVStackGroup",
    "defaultLayout":           "RVLayoutGroup",
    "defaultSequence":         "RVSequenceGroup",
    # view-group internals
    "defaultStack_stack":      "RVStack",
    "defaultLayout_stack":     "RVStack",
    "defaultSequence_sequence": "RVSequence",
}


def _node_type_for(node_name: str) -> str:
    """Look up the GTO node type for `node_name` (e.g. defaultStack_stack -> RVStack)."""
    if node_name in _NODE_TYPES:
        return _NODE_TYPES[node_name]
    # Retime nodes are named like defaultStack_rt_sourceGroup000000.
    if "_rt_sourceGroup" in node_name:
        return "RVRetime"
    raise ValueError(
        f"unknown node {node_name!r}; add to _NODE_TYPES or use a known node path"
    )


def _resolve_node_path(path: str, view_mode: str) -> str:
    """Map "#NodeType.prop" -> "absoluteNodeName.prop" for session emission."""
    if not path.startswith("#"):
        return path
    type_name, rest = path[1:].split(".", 1)
    table = _NODE_TYPE_RESOLUTION.get(view_mode, {})
    node = table.get(type_name)
    if node is None:
        raise ValueError(
            f"can't resolve #{type_name} for view_mode={view_mode!r} in a "
            f"session file; either map it in _NODE_TYPE_RESOLUTION or use an "
            f"absolute node path."
        )
    return f"{node}.{rest}"


def generate_session_file(case: Case, out: Path) -> Path:
    """Emit a minimal .rv GTO session file describing the Case."""
    view_node = VIEW_NODES.get(case.view_mode, case.view_mode)

    # Group absolute property paths into node blocks: {node_name: {component: {prop: literal}}}
    nodes: dict[str, dict[str, dict[str, tuple[str, str]]]] = {}
    for raw_path, value in case.props.items():
        path = _resolve_node_path(raw_path, case.view_mode)
        parts = path.split(".")
        if len(parts) < 3:
            raise ValueError(
                f"session props need 'node.component.prop' form, got {path!r}"
            )
        node, component, prop = parts[0], parts[1], ".".join(parts[2:])
        gto_type, gto_literal = _gto_value(value)
        nodes.setdefault(node, {}).setdefault(component, {})[prop] = (gto_type, gto_literal)

    lines: list[str] = [
        "GTOa (4)",
        "",
        "rv : RVSession (4)",
        "{",
        "    session",
        "    {",
        f'        string viewNode = "{view_node}"',
        "    }",
        "}",
        "",
    ]

    for i, source in enumerate(case.sources):
        lines += [
            f"sourceGroup{i:06d}_source : RVFileSource (1)",
            "{",
            "    media",
            "    {",
            f'        string movie = "{source.to_filename()}"',
            "    }",
            "}",
            "",
        ]

    for node, comps in nodes.items():
        # The GTO type MUST match what RV expects for this node name -- declaring
        # an existing node with a placeholder type causes rvio to silently drop
        # the property block.
        node_type = _node_type_for(node)
        lines.append(f"{node} : {node_type} (1)")
        lines.append("{")
        for comp, props in comps.items():
            lines.append(f"    {comp}")
            lines.append("    {")
            for prop, (gto_type, gto_literal) in props.items():
                lines.append(f"        {gto_type} {prop} = {gto_literal}")
            lines.append("    }")
            lines.append("")
        lines.append("}")
        lines.append("")

    out.write_text("\n".join(lines))
    return out


# ---------------------------------------------------------------------------
# rvio runner
# ---------------------------------------------------------------------------


def _frame_arg(frames: tuple[int, int]) -> str:
    return f"{frames[0]}-{frames[1]}" if frames[0] != frames[1] else str(frames[0])


def _check_rvio_result(case: Case, output_root: Path, proc: subprocess.CompletedProcess,
                       cmd: list[str], session_text: Optional[str] = None
                       ) -> tuple[bool, str]:
    # All per-frame files share the same dir; use the first frame's path for the log.
    first_out = case.out_for_frame(case.frames[0], output_root)
    log_path = first_out.with_suffix(".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"$ {' '.join(cmd)}\n"
        + (f"--- session file ---\n{session_text}\n" if session_text else "")
        + f"exit: {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n"
    )
    if proc.returncode != 0:
        return False, f"rvio exit {proc.returncode}; see {log_path}"
    missing = [n for n in case.frame_numbers()
               if not case.out_for_frame(n, output_root).is_file()]
    if missing:
        return False, (
            f"rvio exit 0 but missing frames {missing}; see {log_path}"
        )
    return True, proc.stdout


def run_rvio_mu(rvio: Path, case: Case, output_root: Path,
                init_mu: Path, verbose: bool = False) -> tuple[bool, str]:
    """Render a Case via the Mu -init mechanism."""
    cmd = [str(rvio)]
    cmd.extend(s.to_filename() for s in case.sources)
    cmd.extend([
        "-init", str(init_mu),
        "-outhalf",
        "-err-to-out",
        "-t", _frame_arg(case.frames),
        "-o", case.output_pattern(output_root),
    ])
    if verbose:
        print("  $", " ".join(cmd))

    first_out = case.out_for_frame(case.frames[0], output_root)
    first_out.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as e:
        return False, f"rvio timed out: {e}"

    return _check_rvio_result(case, output_root, proc, cmd)


def run_rvio_session(rvio: Path, case: Case, output_root: Path,
                     session: Path, verbose: bool = False) -> tuple[bool, str]:
    """Render a Case via a generated .rv session file."""
    cmd = [
        str(rvio),
        str(session),
        "-outhalf",
        "-err-to-out",
        "-t", _frame_arg(case.frames),
        "-o", case.output_pattern(output_root),
    ]
    if verbose:
        print("  $", " ".join(cmd))

    first_out = case.out_for_frame(case.frames[0], output_root)
    first_out.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as e:
        return False, f"rvio timed out: {e}"

    return _check_rvio_result(case, output_root, proc, cmd,
                              session_text=session.read_text())


# ---------------------------------------------------------------------------
# oiiotool diff (copied from rv-render-test)
# ---------------------------------------------------------------------------


@dataclass
class DiffResult:
    ok: bool
    max_abs: float
    mean_abs: float
    message: str


_MAX_RE = re.compile(r"Max error\s*=\s*([\d.eE+-]+)")
_MEAN_RE = re.compile(r"Mean error\s*=\s*([\d.eE+-]+)")


def oiio_diff(a: Path, b: Path, tol: Tolerance) -> DiffResult:
    cmd = ["oiiotool", str(a), str(b), "--diff"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout + proc.stderr

    if proc.returncode == 0:
        return DiffResult(ok=True, max_abs=0.0, mean_abs=0.0,
                          message="match (oiiotool PASS)")

    max_m = _MAX_RE.search(out)
    mean_m = _MEAN_RE.search(out)
    max_err = float(max_m.group(1)) if max_m else float("inf")
    mean_err = float(mean_m.group(1)) if mean_m else float("inf")

    ok = max_err <= tol.max_abs and mean_err <= tol.mean_abs
    msg = f"max={max_err:.3e}, mean={mean_err:.3e}"
    if not ok:
        msg += (
            f" (limits max={tol.max_abs}, mean={tol.mean_abs})\n"
            f"--- oiiotool ---\n{out}"
        )
    return DiffResult(ok=ok, max_abs=max_err, mean_abs=mean_err, message=msg)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def render_case(rvio: Path, case: Case, output_root: Path,
                tmp_path: Path, verbose: bool) -> tuple[bool, str]:
    if case.mechanism == "mu":
        init = generate_mu_init(case, tmp_path / f"{case.name}.mu")
        return run_rvio_mu(rvio, case, output_root, init, verbose=verbose)
    if case.mechanism == "session":
        session = generate_session_file(case, tmp_path / f"{case.name}.rv")
        return run_rvio_session(rvio, case, output_root, session, verbose=verbose)
    return False, f"unknown mechanism: {case.mechanism}"


def cmd_generate(rvio: Path, cases: list[Case], verbose: bool) -> int:
    fails = 0
    with tempfile.TemporaryDirectory(prefix="rvvt_") as tmp:
        tmp_path = Path(tmp)
        for c in cases:
            n_frames = len(list(c.frame_numbers()))
            print(f"[generate] {c.section}/{c.mechanism}/{c.name}  "
                  f"({n_frames} frame{'s' if n_frames != 1 else ''})")
            ok, msg = render_case(rvio, c, GOLDENS_DIR, tmp_path, verbose)
            if not ok:
                print(f"  FAIL: {msg}")
                fails += 1
    return 0 if fails == 0 else 1


def cmd_validate(cases: list[Case]) -> int:
    """Two checks, neither runs rvio:
      1. Silent no-op: each case's goldens differ from the same-section/same-
         mechanism/same-view-mode baseline (the case with empty props).
      2. Cross-mechanism agreement: for each case name, the mu and session
         goldens are bit-identical (or within tolerance).
    """
    # Build lookups. Baseline match is (mechanism, view_mode, num_sources)
    # so a retime case (section=retime, view_mode=sequence, 3 sources) finds
    # its empty-props counterpart (section=sequence, view_mode=sequence,
    # 3 sources) -- the "same setup minus the override under test".
    baselines: dict[tuple, Case] = {}
    by_name_mech: dict[tuple, Case] = {}  # (section, name, mechanism) -> Case
    for c in cases:
        by_name_mech[(c.section, c.name, c.mechanism)] = c
        if not c.props:
            baselines.setdefault(
                (c.mechanism, c.view_mode, len(c.sources)), c
            )

    fails = 0

    # ---- check 1: silent no-ops -----------------------------------------
    # Iterate ALL overlapping frames: a case might be a no-op only on the first
    # frame and differ on later frames (e.g. retime's first output frame often
    # legitimately maps to the source's first input frame regardless of scale/
    # offset). Only flag NO-OP if EVERY overlapping frame matches.
    no_ops: list[str] = []
    for c in cases:
        if not c.props:
            continue
        base = baselines.get((c.mechanism, c.view_mode, len(c.sources)))
        if base is None or base.name == c.name:
            continue
        overlap = sorted(set(c.frame_numbers()) & set(base.frame_numbers()))
        if not overlap:
            continue
        any_diff = False
        for n in overlap:
            g = c.golden_for_frame(n)
            b = base.golden_for_frame(n)
            if not g.is_file() or not b.is_file():
                continue
            proc = subprocess.run(["oiiotool", str(g), str(b), "--diff"],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                any_diff = True
                break
        if any_diff:
            print(f"[validate] OK     {c.section}/{c.mechanism}/{c.name}  (differs from {base.name})")
        else:
            no_ops.append(f"{c.section}/{c.mechanism}/{c.name}  =  {base.name}")
            print(f"[validate] NO-OP  {c.section}/{c.mechanism}/{c.name}  =  {base.name}  (all {len(overlap)} frame(s) identical)")

    # ---- check 2: cross-mechanism agreement -----------------------------
    print()
    diverges: list[str] = []
    seen_names = set()
    for c in cases:
        key = (c.section, c.name)
        if key in seen_names:
            continue
        seen_names.add(key)
        mu_c = by_name_mech.get((c.section, c.name, "mu"))
        ss_c = by_name_mech.get((c.section, c.name, "session"))
        if mu_c is None or ss_c is None:
            continue  # only one mechanism for this case; nothing to cross-check

        frame_diffs = 0
        for n in mu_c.frame_numbers():
            mg = mu_c.golden_for_frame(n)
            sg = ss_c.golden_for_frame(n)
            if not mg.is_file() or not sg.is_file():
                continue
            proc = subprocess.run(["oiiotool", str(mg), str(sg), "--diff"],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                frame_diffs += 1
        tag = "AGREE   " if frame_diffs == 0 else f"DIVERGE ({frame_diffs} frame(s))"
        print(f"[xcheck]  {tag}  {c.section}/{c.name}")
        if frame_diffs:
            diverges.append(f"{c.section}/{c.name}")

    print()
    print(f"Summary: {len(no_ops)} silent no-ops; {len(diverges)} mu/session divergences")
    if no_ops or diverges:
        return 1
    return 0


def cmd_test(rvio: Path, cases: list[Case], verbose: bool) -> int:
    fails = 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rvvt_") as tmp:
        tmp_path = Path(tmp)
        for c in cases:
            missing_goldens = [n for n in c.frame_numbers()
                               if not c.golden_for_frame(n).is_file()]
            if missing_goldens:
                print(
                    f"[test] {c.section}/{c.mechanism}/{c.name}: SKIP "
                    f"(missing goldens for frames {missing_goldens}; run --generate)"
                )
                continue

            ok, msg = render_case(rvio, c, OUT_DIR, tmp_path, verbose)
            if not ok:
                print(f"[test] {c.section}/{c.mechanism}/{c.name}: rvio FAIL: {msg}")
                fails += 1
                continue

            frame_fails = 0
            worst_msg = ""
            for n in c.frame_numbers():
                diff = oiio_diff(c.golden_for_frame(n),
                                 c.out_for_frame(n, OUT_DIR),
                                 c.tolerance)
                if not diff.ok:
                    frame_fails += 1
                    worst_msg = f"frame {n}: {diff.message}"
            if frame_fails:
                fails += 1
                print(f"[test] {c.section}/{c.mechanism}/{c.name}: FAIL "
                      f"({frame_fails} frame(s)) -- {worst_msg}")
            else:
                print(f"[test] {c.section}/{c.mechanism}/{c.name}: PASS  "
                      f"({len(list(c.frame_numbers()))} frame(s))")
    return 0 if fails == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--rv", default="/Applications/RV.app",
        help="Path to RV install (.app, install root, or rvio binary). "
             "Default: /Applications/RV.app",
    )
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--validate", action="store_true",
                    help="Check goldens for silent no-ops and mu/session divergence; does not run rvio.")
    ap.add_argument("--filter", default="",
                    help="Run only cases whose name or section contains this substring.")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    try:
        rvio = rvio_binary(Path(args.rv))
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2

    if not shutil.which("oiiotool"):
        print("oiiotool not on PATH. Install OpenImageIO.", file=sys.stderr)
        return 2

    print(f"rvio: {rvio}  ({rvio_label(rvio)})")

    sys.path.insert(0, str(HERE))
    import cases as case_module

    all_cases: list[Case] = case_module.CASES
    cases = (
        [c for c in all_cases
         if args.filter in c.name or args.filter in c.section
         or args.filter in c.mechanism]
        if args.filter else all_cases
    )
    print(f"{len(cases)}/{len(all_cases)} cases")

    if args.validate and args.generate:
        print("--validate and --generate are mutually exclusive.", file=sys.stderr)
        return 2
    if args.validate:
        return cmd_validate(cases)
    if args.generate:
        return cmd_generate(rvio, cases, args.verbose)
    return cmd_test(rvio, cases, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
