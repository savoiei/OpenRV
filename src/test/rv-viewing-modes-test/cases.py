"""Test cases for rv_viewing_modes_test.

Coverage groups (planned):
  stack/      multi-source blend in defaultStack
  layout/     defaultLayout: packed/grid/row/column arrangements
  sequence/   defaultSequence: concatenation, EDL, cuts
  retime/     RVRetime.visual.scale / visual.offset per source

Each Case is rendered via TWO mechanisms when applicable:
  mechanism="mu"      -> commands.setViewNode + property overrides via -init
  mechanism="session" -> generated .rv GTO file (not yet implemented)

Cross-mechanism divergence on the same logical config is itself a finding:
the Mu command surface and the GTO persistence format should agree.

Relevant nodes (from /tmp/rvrt_sample.rv -- run rvio with -init that calls
commands.saveSession to regenerate the reference):

  RVLayoutGroup (defaultLayout):
    layout.mode      String   "packed" / "grid" / "row" / "column" / "manual"
    layout.spacing   Float
    layout.gridRows  Int      0 = auto
    layout.gridColumns Int

  RVStackGroup (defaultStack):  -- contains an RVStack render node:
  RVStack (defaultStack_stack):
    composite.type        String   "over" / "add" / "multiply" / "difference"
                                    / "dissolve" / ...
    composite.dissolveAmount Float
    mode.alignStartFrames Int
    mode.strictFrameRanges Int

  RVSequenceGroup (defaultSequence):  -- contains:
  RVSequence (defaultSequence_sequence):
    edl.source   Int[]   per-cut source index
    edl.frame    Int[]   output frame per cut
    edl.in       Int[]   in-points
    edl.out      Int[]   out-points
    mode.autoEDL Int     1 = auto-build EDL from sources

  RVRetime (one per source per view group -- e.g. defaultStack_rt_sourceGroup000000):
    visual.scale  Float  1.0 = normal, 0.5 = slow, 2.0 = fast
    visual.offset Float  time shift in frames
    output.fps    Float
    warp.active   Int    enable keyframe-based retime
"""

from rv_viewing_modes_test import Case, Source

# Distinct-colored sources so stacked / laid-out / sequenced output is visible.
_RED   = Source(kind="solid", width=128, height=72, depth="16f",
                start=1, end=1, red=1.0, green=0.0, blue=0.0, alpha=1.0)
_GREEN = Source(kind="solid", width=128, height=72, depth="16f",
                start=1, end=1, red=0.0, green=1.0, blue=0.0, alpha=1.0)
_BLUE  = Source(kind="solid", width=128, height=72, depth="16f",
                start=1, end=1, red=0.0, green=0.0, blue=1.0, alpha=1.0)


def _both_mechanisms(name: str, **kwargs) -> list[Case]:
    """Emit the same logical case under both backends so we exercise the Mu
    command surface and the .rv GTO format on identical configurations.
    Goldens should be bit-identical between the two; mismatches surface
    divergence between RV's runtime and persistence paths."""
    return [
        Case(name, mechanism="mu", **kwargs),
        Case(name, mechanism="session", **kwargs),
    ]


# Semi-transparent variants so composite blend modes have something to do.
_RED_HALF  = Source(kind="solid", width=128, height=72, depth="16f",
                    start=1, end=1, red=1.0, green=0.0, blue=0.0, alpha=0.5)
_BLUE_HALF = Source(kind="solid", width=128, height=72, depth="16f",
                    start=1, end=1, red=0.0, green=0.0, blue=1.0, alpha=0.5)

# Multi-frame source with per-frame variation -- smptebars + hpan slides the
# bars across the frame, so frame N looks different from frame M. Required to
# make Retime visible (with constant-color sources retime is a silent no-op).
_BARS_PAN8 = Source(kind="smptebars", width=128, height=72, depth="16f",
                    start=1, end=8, hpan=16)


CASES: list[Case] = [
    # ---- stack ------------------------------------------------------------
    # Default composite is "over" -> top source wins. With _RED on top the
    # output is solid red (1,0,0,1).
    *_both_mechanisms(
        "stack_default_2sources",
        sources=[_RED, _GREEN],
        view_mode="stack",
        section="stack",
    ),
    # Composite blend modes: changes how stacked sources combine. Visible only
    # when sources have non-trivial alpha or interactions.
    *_both_mechanisms(
        "stack_composite_add",
        sources=[_RED_HALF, _BLUE_HALF],
        view_mode="stack",
        props={"#RVStack.composite.type": "add"},
        section="stack",
    ),
    *_both_mechanisms(
        "stack_composite_difference",
        sources=[_RED_HALF, _BLUE_HALF],
        view_mode="stack",
        props={"#RVStack.composite.type": "difference"},
        section="stack",
    ),

    # ---- layout -----------------------------------------------------------
    # layout.mode controls how sources tile into the output frame.
    *_both_mechanisms(
        "layout_packed_default",
        sources=[_RED, _GREEN, _BLUE],
        view_mode="layout",
        section="layout",
    ),
    *_both_mechanisms(
        "layout_row",
        sources=[_RED, _GREEN, _BLUE],
        view_mode="layout",
        props={"#RVLayoutGroup.layout.mode": "row"},
        section="layout",
    ),
    *_both_mechanisms(
        "layout_column",
        sources=[_RED, _GREEN, _BLUE],
        view_mode="layout",
        props={"#RVLayoutGroup.layout.mode": "column"},
        section="layout",
    ),
    *_both_mechanisms(
        "layout_grid_2x2",
        sources=[_RED, _GREEN, _BLUE, _RED_HALF],
        view_mode="layout",
        props={
            "#RVLayoutGroup.layout.mode": "grid",
            "#RVLayoutGroup.layout.gridRows": 2,
            "#RVLayoutGroup.layout.gridColumns": 2,
        },
        section="layout",
    ),

    # ---- sequence ---------------------------------------------------------
    # Two 1-frame sources auto-EDL'd into a 2-frame sequence:
    #   frame 1 -> _RED, frame 2 -> _GREEN.
    # Smallest case that proves multi-frame rendering works.
    *_both_mechanisms(
        "sequence_concat_red_green",
        sources=[_RED, _GREEN],
        view_mode="sequence",
        frames=(1, 2),
        section="sequence",
    ),
    # Three-source sequence to confirm frame-to-source mapping holds for >2.
    *_both_mechanisms(
        "sequence_concat_3sources",
        sources=[_RED, _GREEN, _BLUE],
        view_mode="sequence",
        frames=(1, 3),
        section="sequence",
    ),

    # ---- retime -----------------------------------------------------------
    # RVRetime nodes sit per-source inside each view group. To target source 0
    # specifically, we use absolute paths (defaultStack_rt_sourceGroup000000).
    #
    # Retime is only observable on multi-frame sources whose content varies
    # frame-to-frame -- with 1-frame solid sources the override is a silent
    # no-op (validate'd against the baseline). smptebars + hpan gives us
    # per-frame variation cheaply (bars slide across over frames).
    *_both_mechanisms(
        "retime_baseline_panning_bars",
        sources=[_BARS_PAN8],
        view_mode="stack",
        frames=(1, 4),
        section="retime",
    ),
    # KNOWN NO-OP: visual.offset = 2.0 doesn't visibly change output here, even
    # though the property writes back as 2.0 and the source genuinely animates
    # per-frame (verified independently). retimedFrame() math in RetimeIPNode.cpp
    # subtracts offset from the computed input frame, but in practice we see
    # zero change vs. baseline across all 4 rendered frames. Possible causes
    # (not yet investigated): unit interpretation (frames vs seconds), an
    # interplay with output.fps, or input-start clamping in our specific setup.
    # Left in the suite so --validate keeps the regression visible -- if a
    # future RV build starts honoring this override, the no-op will flip to OK.
    *_both_mechanisms(
        "retime_offset_source0",
        sources=[_BARS_PAN8],
        view_mode="stack",
        props={"defaultStack_rt_sourceGroup000000.visual.offset": 2.0},
        frames=(1, 4),
        section="retime",
    ),
    *_both_mechanisms(
        "retime_scale_2x_source0",
        sources=[_BARS_PAN8],
        view_mode="stack",
        props={"defaultStack_rt_sourceGroup000000.visual.scale": 2.0},
        frames=(1, 4),
        section="retime",
    ),
]
