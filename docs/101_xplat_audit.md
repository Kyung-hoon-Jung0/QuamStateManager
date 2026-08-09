# 101 — Cross-platform audit of the docs/67–97 range (1.0-prep)

*2026-08-10. A focused audit of everything added since the v0.7.0
cross-platform pass, hunting the classes that bit before (cp949 subprocess
capture, path dialects, Windows-only/POSIX-only APIs, case identity, EOL).
Five confirmed defects and eight of the plausible ones are fixed here; the
audit's clean-check list (what was verified NOT broken) is as important as
the fixes and is summarized at the end.*

## Fixed (confirmed)

- **C1 — `verification.sm_analysis_rev` hashed raw bytes**, so with
  `core.autocrlf` the SAME commit produced a different `analysis_rev` per OS
  (measured: 534 CRs in the Windows checkout of `gates.py`, 0 in the blob) and
  `comparable()` refused to reconcile identical gate analyses across
  platforms. Now EOL-normalized (`\r\n`→`\n`) before hashing; **P4** applies
  the same normalization to `run_fit_audit._gate_hash` over the lab tree.
  *Recommended follow-up (not done here): a `.gitattributes` with
  `*.py text eol=lf` — deferred because flipping checkout EOL policy under
  live sessions is disruptive.*
- **C2 — figure_gen's tempdir under the WSL→Windows bridge**: a POSIX
  `/tmp/sm_figgen_*` handed to a Windows `.exe` interpreter is invisible to
  the child (it recreates the literal path under its own drive; the parent
  404s every figure). When bridging, the workdir now allocates under the
  instance dir (`figgen_tmp/`), which both dialects reach. **C3**: those dirs
  outlive the call by design (the caller reads the PNGs), so day-old siblings
  are swept instead of leaking one per judge call.
- **C4 — `/env-schema` baseline key traversal**: the request-supplied key
  reached a `Path` join, where a drive-absolute segment REPLACES the base on
  Windows and `../` escapes everywhere. `load_baseline` now accepts only the
  `[A-Za-z0-9._-]{1,120}` alphabet `env_key()` emits.
- **C5 — 33 test files ran node selfchecks with `text=True` and no
  `encoding`** — the documented cp949 mojibake class (UTF-8 stdout decoded
  through the ANSI codepage). All subprocess captures in `tests/` now pass
  `encoding="utf-8"` (both the inline and line-wrapped spellings).
- **P1 — sourceroot staging rename TOCTOU** between two SM windows:
  Windows raises `FileExistsError` where POSIX may succeed; the winner's tree
  is the same immutable SHA, so the loser now uses it and removes its
  staging (which also stops the `.staging` leak).
- **P2 — zip archives drop symlinks and exec bits**: `materialize` now uses
  `git archive --format=tar` + `tarfile(filter="tar")` on POSIX (symlinks +
  modes preserved; absolute/parent-escape refused), zip stays on Windows.
- **P3 — bare `git` inherited `GIT_DIR`/`GIT_WORK_TREE`**, which silently
  retargets `-C <root>` (a hook environment would materialize the WRONG tree
  under a valid-looking revision). `_git` scrubs `GIT_*` from the env.
- **P8/P9 — `python -m quam_state_manager` on a headless box**: pywebview
  now imports lazily inside `main()` (module-level name kept as a patchable
  sentinel for the tests), missing → a one-line "use qsm serve" hint; and
  `webview.start()` — where a missing GTK/Qt/pyobjc backend actually raises —
  is inside the `_fatal_startup_error` funnel instead of outside it.
- **P10 — `_is_system_path` on macOS**: casefolded comparison + the macOS
  system roots (`/System`, `/Library`, `/private`).
- **P11 — locate-candidates deduped paths with unconditional `lower()`** —
  the documented Linux hazard; now `path_match.fs_key`. **P12**: its POSIX
  branch scanned only `/mnt/c/Users` (a WSL-ism); native Linux `/home/*` and
  macOS `/Users/*` are scanned too.
- **P13 — `autofit.js` basename split on `[\\/]`** truncated legal POSIX
  names containing a backslash; now dialect-aware (`pathBase`, the app.js
  rule).
- **P6 — corpus double-counted runs** reachable through case-variant or
  symlinked spellings of one root; run folders dedup by `fs_key`.
- **P7 — judge-pack leakage lint had no POSIX-path alternative**; a pack
  authored on Linux/macOS could ship `/home/...` past the rule that exists to
  stop exactly that.

## Known, deliberately not fixed here

- `.gitattributes` EOL policy (above). — `envmatrix._source_sig` mtime
  granularity across the drvfs bridge (cache-miss cost only). — the
  pre-v0.7.0 sites the audit flagged outside its range
  (`config_generator.py:431` conda capture, `routes.py` `wslpath` capture,
  `autofit/replay.py` `_to_native` placement): same classes, older code,
  folded into the backlog rather than this pass.

## Verified clean (absence as evidence, abbreviated)

Every in-range `open()`/`read_text` carries `encoding="utf-8"` (CSV/newline
cases correct); all four in-range subprocess spawners already pass
UTF-8+replace; generator child envelopes are ASCII-safe by `json.dumps`
default; Windows-only APIs all have POSIX branches and vice versa (zero hits
for fork/fcntl/winreg/startfile/shell=True in the package); `fs_key` is used
at all 24 routes keying sites (the two deviations are P6/P11 above); nothing
new bypasses `safe_io` for live files; snapshot/dir names avoid
Windows-illegal characters; new TOML-writing tests interpolate `as_posix()`;
all 20 new `.cjs` selfchecks resolve via `path.join(__dirname, …)`.

Pinned by the existing suites (verification/runner/autofit/main/search/
component-map all green post-fix — 250 tests in the affected slice; the
`test_main` webview-sentinel contract keeps `@patch("...main.webview")`
working).
