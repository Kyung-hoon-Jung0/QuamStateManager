# 33 — Development Setup

This document covers what's needed to get a fresh checkout of QUAM State Manager running locally, on Windows + WSL (the maintainer's setup) and on plain Linux/Mac.

## TL;DR

```bash
# 1. Clone
git clone <repo-url> state-manager
cd state-manager

# 2. (WSL only) Activate the project conda env. Python 3.11.
conda activate qm_mng
# Or create one: conda create -n qm_mng python=3.11

# 3. Editable install with dev tools
pip install -e ".[dev]"

# 4. (Optional) Wire up pre-commit
pre-commit install

# 5. Run the test suite
python -m pytest tests/ -q

# 6. Run the web app on a non-default port (stale Flask servers stack up otherwise)
python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```

Open <http://127.0.0.1:5050/> in a browser.

## Conda env: `qm_mng`

This project uses a conda env named `qm_mng` (Python 3.11). The name is referenced in `CLAUDE.md` and the maintainer's memory because the `generate-config` wizard subprocess assumes that env exists with `qualang_tools` and `quam_builder` installed.

If you only need the State Manager (no config-generation wizard), the conda env is optional — any Python ≥3.10 venv works.

If you do want the wizard, create the env with the QUA tooling:

```bash
conda create -n qm_mng python=3.11
conda activate qm_mng
pip install -e ".[dev]"
pip install qualang_tools quam_builder  # or the project's pinned versions
```

## Tests

```bash
python -m pytest tests/ -v               # full suite (~910 tests, ~50s on qm_mng)
python -m pytest tests/test_loader.py -v # single file
python -m pytest tests/test_loader.py::test_load_basic -v
```

Tests use `tmp_path` synthetic fixtures by default. A few tests also probe real data under `<data-root>\...` and **auto-skip** when that path is absent — that's normal on a fresh laptop, not a failure. On the maintainer's box this currently reads 910 pass / 96 skip / 0 fail.

## Running the CLI

```bash
python -m quam_state_manager.cli --help
python -m quam_state_manager.cli --version
python -m quam_state_manager.cli show qA1 -f path/to/quam_state/
python -m quam_state_manager.cli search "7639" --json | jq .
```

The `--json` flag on `show`, `search`, and `table` emits machine-readable output for scripting.

## Running the desktop app

```bash
python -m quam_state_manager
```

This launches pywebview against the Flask app. On Windows + WSL, pywebview needs the Windows-side Python (since pywebview talks to the OS GUI), so the desktop build only works from a Windows shell, not WSL.

## Building the standalone `.exe` (Windows only)

```bash
pyinstaller build/quam-manager.spec
# Output: dist/quam-manager/quam-manager.exe
```

Uses onedir mode (not onefile) for instant cold start — onefile extracts to temp on every launch (3–10 s overhead).

The `.spec` file lives in the `feat/conflict-safe-io` branch family — it's not on `main` yet because the build flow is still being stabilized.

## Pre-commit

```bash
pre-commit install            # one-time, sets up the git hook
pre-commit run --all-files    # run all hooks across the repo
```

The hooks (defined in `.pre-commit-config.yaml`) do:
- Trailing whitespace + EOF newline fixes
- YAML / TOML syntax checks
- Large-file guard (>512 KB)
- `ruff` lint + format

Ruff config is in `pyproject.toml` under `[tool.ruff]`. It's tuned conservatively — real bugs only, no opinionated style nags.

## Dev server port

Per maintainer convention, **don't use the default `5000`** — stale Flask processes accumulate on this machine. Pick a fresh port (5050, 5500, etc.) per session:

```bash
python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```

To find and kill leftover servers:

```bash
pgrep -af "create_app|werkzeug"
pkill -f "create_app"
```
