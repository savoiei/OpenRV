# session

Regression tests for RV session saving: `save`, `save as copy`, odd and relative paths, the saved file's content, and reloading it. Everything runs headless through `rvio -init` with generated `.movieproc` sources. No media files and no goldens are needed.

`save_copy_preserves_working_name` guards the fix from OpenRV PR #1303 (SG-43710): saving a copy must not change the session's working name.

Ported from `rv-session-test` (2026-11-03). The legacy entry is retired from `suites.json`. The 6 legacy cases keep their operations, expectations and round-trip file. Five `paths` cases are new. `--filter` works on the case name (`session/<section>/<name>`).

## What it asserts

Each case makes three kinds of rvio call, each in its own folder under the case directory, and every call has the same blocking checks. Labels are prefixed with the phase: `write:`, `ref:`, `roundtrip:` or `structure:`.

Blocking checks on every rvio call:

| Check | Passes when |
|---|---|
| `rvio exit` | rvio exits 0 (a timeout is a failure) |
| `no ERROR lines` | no output line starts with `ERROR:` |
| `no GL_ERROR` | no `GL_ERROR` line in the output |
| `no uncaught exception` | no `uncaught exception` line |

### Write phase

rvio loads the case's sources and a generated Mu script (`write/ops.mu`) that runs the case's operations in order and prints one `##RVSESSION## <op> | actual=<sessionFileName()>` line per operation, then `##RVSESSION## DONE`.

| Check | Passes when |
|---|---|
| `write: ops ran` | the `DONE` marker is present (the script ran to the end) |
| `write: op N sessionFileName` | the working name after operation N equals the expected one (the operation's `expect_name`) |
| `write: op N file written` | the file named by `expect_file` exists in the case work directory |

### Round trip

Only for cases with a `roundtrip` file. The case's original sources are rendered directly in the same run (`ref/`), then the saved session is rendered (`roundtrip/`). There are no goldens: the reloaded session must equal the live render.

| Check | Passes when |
|---|---|
| `ref: frame N exists` | the reference render wrote the frame |
| `ref: frame N not blank` | the reference frame is not a constant image (`oiiotool --stats`). Without this, two blank renders could agree |
| `roundtrip: session file` | the saved file exists |
| `roundtrip: frame N exists` | the reload wrote the frame |
| `roundtrip: frame N equals reference` | `oiiotool --diff` is within the case tolerance (max 1e-3, mean 1e-4) |

An `oiiotool` failure makes the case an `error`, unless a blocking check already failed (then it stays `fail`, with the comparison error in the message). A missing frame or a blank reference gives `not compared` on the dependent checks.

### Structure

The saved session is read as a file, with no rvio call.

- Text GTO (every case but `save_compressed`): the number of `RVFileSource` objects equals the source count, and each source's `media.movie` equals the expected `.movieproc` filename, in order. Check label `structure: gto`.
- Compressed save (`save_compressed`, gzip-wrapped binary GTO): reduced check only. The file gunzips, the first four bytes are magic `0x29f` (little-endian), and every expected movie name occurs in the decompressed bytes. It does not check the count or the order. Check label `structure: gzip-binary (magic + movie names)`. Exact count and order need a binary GTO parser.

## Cases

11 cases, all tier `ci`. Sections: `save_as_copy` (1), `save` (1), `save_as` (1), `options` (2), `paths` (6).

| Case | What it guards |
|---|---|
| `save_as_copy/save_copy_preserves_working_name` | save `work.rv`, save a copy `copy.rv` (working name stays `work.rv`), save `work2.rv` (working name is now `work2.rv`). Round trip on `copy.rv`. The OpenRV PR #1303 / SG-43710 guard |
| `save/save_sets_working_name` | a save to `a.rv` sets the working name. Round trip on `a.rv` |
| `save_as/save_as_retargets_working_name` | two sources; save `one.rv`, then `two.rv`: the working name follows the second save. Round trip on `two.rv`, structure asserts both movie names in order |
| `options/save_compressed` | `compressed=true` writes gzip binary GTO and still reloads and renders like the original |
| `options/save_complete_nonsparse` | `sparse=false` (a complete save) reloads and renders like the original |
| `paths/save_path_with_spaces` | a space in the file name (`my session.rv`) |
| `paths/save_relative_path` | `rel.rv` |
| `paths/save_dot_relative_path` | `./dot.rv` |
| `paths/save_parent_segment_path` | `sub/../dotdot.rv`, with `sub/` created first |
| `paths/save_double_separator_path` | `sub//double.rv`, with `sub/` created first |
| `paths/save_path_with_quote` | an absolute path whose file name contains a double quote (`quote"name.rv`) |

All round trips render 256x144 `smptebars` (16-bit half float, 1 frame) sources, except `save_as_retargets_working_name`, whose second source is 64 pixels wide; the reference and the reload both render source 1 on frame 1.

## Relative paths: behaviour observed 2026-11-03

On RV 2026.1.0, with rvio's working directory set to the case work directory, `saveSession` and `sessionFileName()` give:

| Path given to `saveSession` | `sessionFileName()` afterwards | File written |
|---|---|---|
| `rel.rv` | `rel.rv` | `<work>/rel.rv` |
| `./dot.rv` | `./dot.rv` (kept verbatim) | `<work>/dot.rv` |
| `sub/../dotdot.rv` | `sub/../dotdot.rv` (kept verbatim) | `<work>/dotdot.rv` |
| `sub//double.rv` | `sub/double.rv` (the double separator is collapsed) | `<work>/sub/double.rv` |
| `quote"name.rv` (absolute, under `<work>`) | the same absolute path | written |

The cases assert exactly these values, as observed. A change is a review trigger, not an expectation to edit. Relative-path cases pass the string to RV unchanged (`relative=True` in the case table); the round trip then reloads the saved file by its `expect_file` token (for example `dot.rv` or `dotdot.rv`), relative to the work directory.

rvio runs with its working directory set to the case work directory (`<case dir>/work`), so a relative path resolves there. Absolute paths are the work directory joined with the case's path token; the case directory is resolved first (no symlinks, no dependence on the Python working directory).

## Path escaping in Mu

Every path written into the generated Mu script goes through `rvtest.escape.mu_string`, which wraps the path in double quotes and escapes backslashes and double quotes. Nothing else is escaped. The legacy suite inserted paths as `"{path}"` with no escaping, so a quote in a path broke the script. `save_path_with_quote` covers it.

## Driving idioms

Confirmed by the legacy spike and ported unchanged (see the module docstring of `mugen.py`):

- The load-complete events (`source-group-complete`, `new-source`, `after-progressive-loading`) never fire under headless `rvio -init`. The script binds to `render`, which fires once per output frame with the sources already loaded.
- `commands.unbind` has only the 3-argument form (mode, table, event) on this build, so the script also keeps a module-global one-shot guard (`rvt_session_ran`). The unbind call is redundant but harmless.
- The script waits until `commands.nodesOfType("RVFileSource").size()` reaches the case's source count before running its operations.

## Run

    python3 suites/session/run.py --rv /Applications/RV.app
    python3 suites/session/run.py --rv /Applications/RV.app --filter paths
    python3 suites/session/run.py --list

Common flags are in the root `README.md`. This suite's `--timeout` defaults to 180 s. Needs `oiiotool` on `PATH` or `--oiiotool`. Results go to `suites/session/out/` (`report.json`, `junit.xml`, `artifacts/<case>/`). There are no `--generate` or `--promote` modes.

`--sw-renderer` selects software rendering. On macOS it needs `-swRenderer`, which is not an upstream rvio flag: it exists only in the fork-only patch `dacb1e1` (savoiei/OpenRV, branch `dev/savoiei/new-tests`), and the suite exits 2 when the build rejects it. On Linux the flag uses upstream `rvio_sw`. Windows has no software path (exit 2). The session checks compare renders from the same build, so the renderer does not change expectations.

## Validate

    python3 suites/session/run.py --validate [--filter X]

Static self-consistency of the case table. It needs no rvio, no `oiiotool`, no RV and no re-exec under RV's Python, and writes no reports (console only). For each case it checks that:

- every operation has the path and `expect_name` it needs, and no path or file escapes the work directory;
- every directory a path passes through is in `precreate_dirs`;
- an absolute path's `expect_name` equals its path token, and a save's `expect_file` matches its normalised path;
- the round-trip and structure files are written by some operation;
- `gto_expect` movie names and source count match the sources, and `gto_format` matches whether the save is compressed.

Each case prints one `self-consistent` line. Exit codes: 0 when no problems, 1 when problems are found, 2 when no cases are selected. Each case is checked on its own, so every problem is attributed to its case; a problem that only the whole-selection check reports is shown as an extra failing `session/validate` result.

## Deferred coverage

- Mixed separators and Windows paths (backslashes, drive letters). On macOS and Linux a backslash is an ordinary file name character, so the case only means something on Windows. Phase 2.
- Tier 3: session merge and version upgrade, and the `rvui.mu` menu handlers (`cloneSession`, save, save as). The suite covers the command layer only.
- `clearSession` and `setSessionFileName` cases: supported by the model and generator, no cases yet.
- An exact binary GTO parser, so `save_compressed` can assert source count and order.
