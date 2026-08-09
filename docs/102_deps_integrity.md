# 102 — Dependency integrity for the pip user (1.0-prep)

*2026-08-10. Question: is state sync/loading standing on any broken or
undeclared dependency, and does the real `pip install` path work? Verified
with an AST import-classification over all 144 modules, a live import loop
(128/128), `pip check`, a CLEAN venv install, and a wheel build. Three real
defects fixed; the floors now REPAIR a broken env instead of accepting it.*

## Fixed

- **`qsm serve --help` / `browser --help` crashed on cp949 consoles** —
  the ASCII-banner rule (docs/72 r16) covered `typer.echo` but not the
  command DOCSTRINGS, which typer renders as help text; both carried an
  em-dash. Reproduced with `PYTHONIOENCODING=cp949` (UnicodeEncodeError
  before any command ran), de-dashed, and re-verified OK. This is the same
  crash class the pin has now caught **three** times — the rule is: nothing
  typer renders may be non-ASCII.
- **`quam_state_manager/__main__.py` ran `main()` unguarded** — anything
  that walks the package (pkgutil, sphinx, PyInstaller analysis, some pytest
  configs) imports `__main__` too, and a mere import launched the full
  desktop app (Flask + a pywebview window). Reproduced live, guarded with
  `if __name__ == "__main__":`, re-verified: import → no launch.
- **Version floors that let a broken pair survive**: `numpy>=1.24` +
  `scipy>=1.11` were both already satisfied by the numpy-1.25 + scipy-1.17
  combination scipy itself rejects (`pip check` fails; a UserWarning on
  every import; degraded synth) — so installing SM into such an env was a
  no-op that KEPT the broken pair. Floors raised to scipy's own requirement
  (`numpy>=1.26.4`) and the numpy-2 ABI line (`h5py>=3.11`); pip now
  repairs the env. SM source was separately verified numpy-2-clean (zero
  hits for every removed 1.x alias). **MarkupSafe + Werkzeug declared** —
  both are imported directly but were only transitive.

## Verified end-to-end (clean venv, this branch)

`pip install .` → resolves `numpy 2.4.6 / scipy 1.17.1 / h5py 3.16` (a
working pair) → `qsm --version` = 0.9.6 → `qsm serve` binds and answers 200
→ cp949 `--help` OK → package-walk import launches nothing. Wheel build:
**9 judge-pack JSONs present** (the one previously artifact-unverified
packaging claim), templates + static all in, `tests/` excluded.

## Verified, no action

Generator subprocess scripts: 13/14 stdlib-pure at import under a
meta-path blocker (`iplot_extract` is foreign-env by design and imported by
nothing in the SM process). The r9 typer-leak fix still holds (only
`cli.py` imports typer). `tomllib/tomli` guard matches the
`python_version < '3.11'` marker; no 3.11-only syntax anywhere. Sync/
loading pin suites green on this branch (roundtrip, sync robustness, live
replace, scheduler scope migration, CLI — 98 passed), and the 0.9.5→0.9.6
upgrade path holds: the only instance-dir migrations are flag-gated and
covered by `test_scheduler_scope`.

## Recommended, deliberately not done here

Moving `pywebview` (which drags pythonnet/cffi on Windows) into a
`[desktop]` extra would slim `qsm serve`-only installs but silently break
`python -m quam_state_manager` for everyone upgrading — a 1.0 decision, not
a night-shift one. `run_waveform_golden.py` still ships in the wheel with a
dev-only `tests/` import (inert for users). A stale local `egg-info` can
make `qsm --version` lie under `pip install -e .` (regenerate it after
version bumps).
