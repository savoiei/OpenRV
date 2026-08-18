# rv-viewing-modes-test OpenRV fork trial — design

## Purpose

Try the mechanics of contributing a test into the OpenRV fork end-to-end
(file placement, CI wiring, branch/push/draft-PR) using one already-working
suite as the guinea pig. This is a workflow trial, not a test-engineering
effort — the suite's own code is not modified.

Guinea pig: `rv-viewing-modes-test` from the sibling `rv-test-suite` repo
(regression suite for RV's Stack/Layout/Sequence/Retime viewing modes, 24
cases, all passing there).

Scope is contained to this fork (`savoiei/OpenRV`). The resulting PR targets
this fork's own `main`, not upstream `AcademySoftwareFoundation/OpenRV`.

## File placement

Copy, unmodified, from `~/Documents/git/rv-test-suite/rv-viewing-modes-test/`
into a new folder alongside the existing C++ unit tests:

```
src/test/rv-viewing-modes-test/
  rv_viewing_modes_test.py
  cases.py
  goldens/
  README.md
```

Excluded: `__pycache__/`, `out/`, `.DS_Store` (build/run artifacts, not
suite source).

No changes to test code or helpers — `rvio_binary()` / `oiio_diff()` stay
duplicated inline as they are in the source suite.

Note: `src/test/` today holds only CMake-based C++ unit tests
(`CrashDumpSmokeTest`, `CrashHandlerTest`, `FastMemcpyTest`,
`LoadingSharedLibrariesTest`, `QFontTest`), each wired into
`src/test/CMakeLists.txt` via `ADD_SUBDIRECTORY`. This suite is **not**
added to that `CMakeLists.txt` — it's a standalone Python/rvio harness
that happens to live in the same directory, not part of that CMake build.

## CI wiring

Finding: the existing `.github/workflows/ci.yml` gates every job with
`if: github.repository_owner == 'AcademySoftwareFoundation'`. On this fork
that condition is false, so the existing Linux/macOS/Windows CI currently
no-ops on every PR here. A new workflow without that gate is required for
anything to actually execute on this fork.

Add `.github/workflows/rv-viewing-modes-test.yml`, standalone (not part of
`ci.yml`'s `workflow_call` graph):

- Triggers: `pull_request`, `workflow_dispatch`
- One job: `macos-14`, `arm64`, `Release`, `vfx-platform: CY2025` — same
  shape as the `macos-pr` job in `ci-macos.yml`
- Steps:
  1. Checkout (`submodules: recursive`)
  2. `uses: ./.github/actions/build-macos` with the matrix values above
     (this action already runs `ctest` on the existing C++ suites, builds
     the app, and installs it to `_install/` via
     `cmake --install _build --prefix $(pwd)/_install`)
  3. `brew install openimageio` (for `oiiotool`, required by the suite's
     pixel-diff checks)
  4. `python3 src/test/rv-viewing-modes-test/rv_viewing_modes_test.py --rv "$(pwd)/_install/RV.app"`

Open item to verify on the first real run: the exact bundle path under
`_install/`. It's inferred from `RV_APP_ROOT}/RV.app/Contents` in
`cmake/globals/rv_globals.cmake`, matching the suite's own default
`--rv /Applications/RV.app`, but hasn't been observed directly in an
`_install/` tree. If the path differs, the run step's `--rv` argument gets
a one-line fix — no design impact.

Known cost: a full macOS build via this composite action realistically
takes 30–60 minutes and consumes this fork's Actions minutes, even scoped
to a single OS/arch/config combination.

## Git workflow

- Branch: `dev/savoiei/new-tests` (already checked out, already even with
  `origin/main` — no new branch needed)
- Commit: the copied test folder + the new workflow file
- Push: `git push -u origin dev/savoiei/new-tests`
- PR: `gh pr create --draft` from `dev/savoiei/new-tests` → `main`, within
  `savoiei/OpenRV`. Explicitly NOT opened against
  `AcademySoftwareFoundation/OpenRV`.

## Success criteria

- Push lands on the fork's remote.
- Draft PR opens and the new workflow triggers on it.
- The workflow builds, and the suite runs to completion inside the Action
  (log shows a pass/fail report). A green run is the goal but not a hard
  gate for calling the trial itself successful — the point is proving the
  contribution mechanics work, since the suite is already known-passing
  locally.

## Explicitly out of scope

- Modifying `rv-viewing-modes-test`'s own code, helpers, or goldens.
- Wiring into the existing `ci.yml` / `ci-macos.yml` matrix.
- Any change to `AcademySoftwareFoundation/OpenRV` (upstream). All actions
  in this trial are confined to the `savoiei/OpenRV` fork, per the
  standing rule against unsolicited vendor-repo edits — this trial is the
  user's explicit, scoped exception to that rule.
- Linux/Windows CI equivalents — macOS only for this trial.
