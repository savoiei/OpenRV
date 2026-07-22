# rv-viewing-modes-test

Regression suite for RV's viewing modes — **Stack**, **Layout**, **Sequence**,
**Retime**. Renders multi-source `.movieproc` configurations via `rvio` and
pixel-diffs against checked-in golden EXRs with `oiiotool`.

Tests both RV access paths that real users hit:

- **Mu command surface** — `commands.setViewNode(...)` + property overrides
  applied via a generated `-init` script
- **`.rv` GTO session-file persistence** — full session file authored per case

Divergence between the two on the same logical configuration is itself a
finding: the Mu API and the GTO format should agree on what they describe.

Sibling to [`rv-render-test`](../rv-render-test/) and
[`rv-ocio-test`](../rv-ocio-test/). The `rvio_binary()` resolver and the
`oiio_diff()` wrapper are duplicated from `rv-render-test` for now; once a
third or fourth viewing/rendering area lands we'll extract them into a
shared `rv-test-common` helper.

## Requirements

- An RV install with `rvio` (default looks at `/Applications/RV.app`)
- `oiiotool` on PATH (`brew install openimageio`)
- Python 3.9+ (stdlib only)

## Usage

    python rv_viewing_modes_test.py --generate          # build goldens once
    python rv_viewing_modes_test.py                     # test
    python rv_viewing_modes_test.py --filter stack      # subset
    python rv_viewing_modes_test.py --filter mu         # one mechanism

Outputs:
- `goldens/<section>/<mechanism>/<name>.exr` — reference frames (under VCS)
- `out/<section>/<mechanism>/<name>.exr` + `.log` — test renders + rvio output

## Coverage

- [x] Stack — default + composite `add`, `difference`
- [x] Layout — packed (default), row, column, grid 2x2
- [x] Sequence — 2-source concat, 3-source concat (multi-frame goldens)
- [x] Retime — `visual.scale` works; `visual.offset` is a documented no-op (see below)
- [x] Multi-frame rendering with `name.NNNN.exr` per-frame goldens
- [x] `--validate` with cross-mechanism agreement check
- [x] Session-file backend on par with Mu backend (`0 mu/session divergences` across all cases)

12 logical cases × 2 mechanisms = 24 cases. All --test PASS.

## Known no-ops

`retime/retime_offset_source0` (both mechanisms): `visual.offset = 2.0`
writes back correctly and the source genuinely animates per-frame, but the
override produces no visible change across the rendered frame range. Could
be units interpretation (frames vs. seconds), interaction with `output.fps`,
or input-start clamping. Left in the suite so a future RV build that
honors the override will flip this to OK.

## Validate

`python rv_viewing_modes_test.py --validate` runs two checks (no rvio):

1. **Silent no-op**: each non-baseline case must differ from its same-
   mechanism + same-view-mode + same-source-count baseline on at least one
   overlapping frame. (Checking all frames matters here — retime cases can
   legitimately match baseline on frame 1 because output frame 1 typically
   still maps to input frame 1.)
2. **Cross-mechanism agreement**: for each logical case, the Mu version's
   goldens must be bit-identical to the session version's goldens across all
   frames. Disagreement means RV's runtime command surface and its GTO
   persistence format are describing the same configuration differently
   — which is itself a finding worth catching.

Current state: 2 silent no-ops (both retime_offset flavors, documented),
0 mu/session divergences.
