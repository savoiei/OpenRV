# rv-viewing-modes-test OpenRV Fork Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove out the mechanics of contributing a test into the OpenRV fork (file placement, standalone CI wiring, branch/push/draft-PR) by landing the already-working `rv-viewing-modes-test` suite, unmodified, as the guinea pig.

**Architecture:** Copy the suite's four source files into a new `src/test/rv-viewing-modes-test/` folder (not wired into `src/test/CMakeLists.txt`). Add a standalone GitHub Actions workflow that builds OpenRV via the existing `build-macos` composite action and runs the suite against the freshly built `rvio`. Push the already-checked-out `dev/savoiei/new-tests` branch and open a draft PR within the fork.

**Tech Stack:** Python 3 (stdlib only, suite's own runner), GitHub Actions (YAML), `gh` CLI, git.

## Global Constraints

- No changes to the suite's own code, helpers, or goldens — copy verbatim (spec: "File placement").
- Do not add `rv-viewing-modes-test` to `src/test/CMakeLists.txt` — it stays unwired from the CMake/CTest build (spec: "File placement").
- Do not modify `.github/workflows/ci.yml`, `ci-macos.yml`, or any existing workflow — this is a new, standalone workflow file only (spec: "CI wiring").
- macOS only for this trial — no Linux/Windows equivalents (spec: "Explicitly out of scope").
- The draft PR targets `savoiei/OpenRV`'s own `main`, never `AcademySoftwareFoundation/OpenRV` (spec: "Git workflow", "Success criteria").
- Work happens on the existing `dev/savoiei/new-tests` branch — do not create a new branch (spec: "Git workflow").

---

### Task 1: Copy the suite into `src/test/rv-viewing-modes-test/`

**Files:**
- Create: `OpenRV/src/test/rv-viewing-modes-test/rv_viewing_modes_test.py`
- Create: `OpenRV/src/test/rv-viewing-modes-test/cases.py`
- Create: `OpenRV/src/test/rv-viewing-modes-test/README.md`
- Create: `OpenRV/src/test/rv-viewing-modes-test/.gitignore`
- Create: `OpenRV/src/test/rv-viewing-modes-test/goldens/**` (73 files, copied as a tree)

**Interfaces:**
- Produces: a runnable suite at `src/test/rv-viewing-modes-test/rv_viewing_modes_test.py`, invoked as `python3 src/test/rv-viewing-modes-test/rv_viewing_modes_test.py --rv <path-to-RV.app>` — Task 2's CI step depends on this exact path and CLI shape.

- [ ] **Step 1: Copy the suite tree, excluding build/run artifacts**

```bash
cd /Users/savoiei/Documents/git/savoieiOpenRV/OpenRV
mkdir -p src/test/rv-viewing-modes-test
rsync -a \
  --exclude='__pycache__/' \
  --exclude='out/' \
  --exclude='.DS_Store' \
  /Users/savoiei/Documents/git/rv-test-suite/rv-viewing-modes-test/ \
  src/test/rv-viewing-modes-test/
```

- [ ] **Step 2: Verify only the expected files were copied**

```bash
find src/test/rv-viewing-modes-test -type f | grep -v '^src/test/rv-viewing-modes-test/goldens/' | sort
```

Expected output (exactly these 4 files, no `__pycache__`, `out/`, or `.DS_Store`):

```
src/test/rv-viewing-modes-test/.gitignore
src/test/rv-viewing-modes-test/README.md
src/test/rv-viewing-modes-test/cases.py
src/test/rv-viewing-modes-test/rv_viewing_modes_test.py
```

- [ ] **Step 3: Verify the golden tree copied completely**

```bash
find src/test/rv-viewing-modes-test/goldens -type f | wc -l
```

Expected: `73`

- [ ] **Step 4: Sanity-run the suite from its new location against the local RV install**

This confirms the copy didn't break anything (relative-path assumptions, missing files) before it's wired into CI.

```bash
python3 src/test/rv-viewing-modes-test/rv_viewing_modes_test.py --rv /Applications/RV.app
```

Expected: exits 0, output ends with all cases passing (mirrors the suite's own README claim of "24/24 PASS" — 12 logical cases × 2 mechanisms).

- [ ] **Step 5: Confirm `src/test/CMakeLists.txt` was not touched**

```bash
git status --porcelain src/test/CMakeLists.txt
```

Expected: empty output (no changes).

- [ ] **Step 6: Commit**

```bash
git add src/test/rv-viewing-modes-test/
git commit -m "$(cat <<'EOF'
test: add rv-viewing-modes-test suite (trial)

Unmodified copy from rv-test-suite, landed at src/test/ alongside the
existing C++ unit tests. Not wired into src/test/CMakeLists.txt --
standalone Python/rvio harness, run directly rather than via CTest.
EOF
)"
```

---

### Task 2: Add the standalone GitHub Actions workflow

**Files:**
- Create: `OpenRV/.github/workflows/rv-viewing-modes-test.yml`

**Interfaces:**
- Consumes: `src/test/rv-viewing-modes-test/rv_viewing_modes_test.py --rv <path>` from Task 1.
- Consumes: `./.github/actions/build-macos` composite action (existing, unmodified) with inputs `arch-type`, `build-type`, `qt-version`, `qt-version-short`, `python-version`, `cmake-version`, `vfx-platform` — same input names as used in `ci-macos.yml`'s `macos-pr` job.
- Consumes: the composite action's install output at `_install/RV.app` (from its `cmake --install _build --prefix $(pwd)/_install` step).

- [ ] **Step 1: Write the workflow file**

```yaml
name: OpenRV Viewing Modes Test (trial)

on:
  pull_request:
  workflow_dispatch:

jobs:
  viewing-modes-test:
    name: 'rv-viewing-modes-test (macos-14 arm64 CY2025 Release)'
    runs-on: macos-14
    steps:
      - name: Check out repository code
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # 6.0.2
        with:
          submodules: recursive

      - uses: ./.github/actions/build-macos
        with:
          arch-type: "arm64"
          build-type: "Release"
          qt-version: "6.5.3"
          qt-version-short: "6.5"
          python-version: "3.11"
          cmake-version: "3.31.6"
          vfx-platform: "CY2025"

      - name: Install oiiotool
        run: brew install openimageio
        shell: bash

      - name: Verify built RV.app location
        run: ls -la "$(pwd)/_install/RV.app/Contents/MacOS/rvio"
        shell: bash

      - name: Run rv-viewing-modes-test
        run: python3 src/test/rv-viewing-modes-test/rv_viewing_modes_test.py --rv "$(pwd)/_install/RV.app"
        shell: bash
```

Write this to `.github/workflows/rv-viewing-modes-test.yml`.

Note: the "Verify built RV.app location" step exists because the exact
`_install/` bundle path was inferred, not observed (see spec's CI-wiring
open item). If it fails, `ls -la "$(pwd)/_install"` in the Action log
will show the real layout — fix the path in the final step and re-push;
this doesn't change anything else in this plan.

- [ ] **Step 2: Validate the YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/rv-viewing-modes-test.yml')); print('OK')"
```

Expected: `OK` (if `pyyaml` isn't installed locally, run `pip3 install --user pyyaml` first — this is a one-time local tooling check, not a project dependency).

- [ ] **Step 3: Confirm no existing workflow files were modified**

```bash
git status --porcelain .github/workflows/
```

Expected: only one line, `?? .github/workflows/rv-viewing-modes-test.yml` (untracked, new file — nothing else in that directory shows as modified).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/rv-viewing-modes-test.yml
git commit -m "$(cat <<'EOF'
ci: add standalone rv-viewing-modes-test workflow (trial)

Builds OpenRV via the existing build-macos composite action
(macos-14/arm64/Release/CY2025) and runs rv-viewing-modes-test against
the resulting rvio. Deliberately not part of ci.yml's workflow_call
graph, and deliberately without the repository_owner ==
'AcademySoftwareFoundation' gate those workflows use -- that gate would
make this a silent no-op on this fork.
EOF
)"
```

---

### Task 3: Push and open the draft PR

**Files:** none (git/GitHub operations only).

**Interfaces:**
- Consumes: commits from Task 1 and Task 2 on `dev/savoiei/new-tests`.

- [ ] **Step 1: Confirm branch state before pushing**

```bash
git log --oneline origin/main..HEAD
```

Expected: 4 commits, oldest-to-newest: the two spec-doc commits (`cc43396`, `02cb411`) already on the branch, then the Task 1 commit, then the Task 2 commit.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin dev/savoiei/new-tests
```

Expected: push succeeds; output shows `dev/savoiei/new-tests -> dev/savoiei/new-tests` and sets up tracking (`Branch 'dev/savoiei/new-tests' set up to track 'origin/dev/savoiei/new-tests'`).

- [ ] **Step 3: Open the draft PR within the fork**

```bash
gh pr create \
  --repo savoiei/OpenRV \
  --base main \
  --head dev/savoiei/new-tests \
  --draft \
  --title "Trial: land rv-viewing-modes-test as a workflow test" \
  --body "$(cat <<'EOF'
Trial run of the test-contribution workflow: copies the already-passing
rv-viewing-modes-test suite (from rv-test-suite) into src/test/, unmodified,
and adds a standalone GitHub Actions workflow to run it against a fresh
build. Not wired into src/test/CMakeLists.txt or the existing ci.yml
matrix. Draft -- this is a mechanics trial confined to this fork, not a
proposal to merge upstream.

See docs/superpowers/specs/2026-07-22-rv-viewing-modes-test-openrv-trial-design.md
for the design.
EOF
)"
```

Expected: prints a PR URL under `https://github.com/savoiei/OpenRV/pull/...`.

- [ ] **Step 4: Verify the PR is a fork-internal draft, not targeting upstream**

```bash
gh pr view --repo savoiei/OpenRV dev/savoiei/new-tests --json url,baseRefName,headRefName,isDraft,baseRepository
```

Expected JSON fields: `"baseRefName": "main"`, `"headRefName": "dev/savoiei/new-tests"`, `"isDraft": true`, and `baseRepository` naming `savoiei/OpenRV` (not `AcademySoftwareFoundation/OpenRV`).

- [ ] **Step 5: Confirm the new workflow actually triggered**

```bash
gh run list --repo savoiei/OpenRV --branch dev/savoiei/new-tests --workflow rv-viewing-modes-test.yml --limit 3
```

Expected: at least one run listed with status `queued` or `in_progress` (a full macOS build realistically takes 30-60 minutes to reach a final `completed`/`success` or `completed`/`failure` state — this step only confirms the trigger worked, not the outcome).

---

## Self-Review

**Spec coverage:**
- File placement (unmodified copy, exclusions, `src/test/` location, unwired from CMakeLists) → Task 1.
- CI wiring (standalone workflow, no `repository_owner` gate, `build-macos` reuse, `brew install openimageio`, run against `_install/RV.app`, open path-verification item) → Task 2.
- Git workflow (existing branch, commit, push, fork-internal draft PR) → Task 3.
- Success criteria (push lands, PR opens, workflow triggers) → Task 3, Steps 2-5.
- Explicitly-out-of-scope items (no suite-code changes, no `ci.yml`/`ci-macos.yml` edits, macOS only, no upstream target) → enforced via Global Constraints and verification steps in Tasks 1-3.

**Placeholder scan:** no TBD/TODO markers; every step has literal commands and expected output.

**Type/interface consistency:** the `--rv` flag and suite path (`src/test/rv-viewing-modes-test/rv_viewing_modes_test.py`) are identical across Task 1's Step 4 sanity check and Task 2's workflow run step. The `build-macos` input names in Task 2 match `ci-macos.yml`'s existing `macos-pr` job. The branch name (`dev/savoiei/new-tests`) and repo (`savoiei/OpenRV`) are consistent across all three tasks.
