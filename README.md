# QUAM State Manager

Desktop + web tool for inspecting and editing quantum machine (QUAM) state files. Reads `state.json` + `wiring.json`, resolves custom JSON pointer references (`#/`, `#../`, `#./`), and provides a CLI, Flask web UI, and pywebview desktop app.

Built for researchers running superconducting qubit experiments who need to browse, compare, and tune parameters across hundreds of qubits.

## Quick Start

```bash
# Clone + install (editable, with dev deps)
git clone https://github.com/Kyung-hoon-Jung0/statemanager.git
cd statemanager
pip install -e ".[dev]"

# After installation, `qsm` (short) and `quam-manager` are both on your PATH — no extra setup
qsm --help
qsm --version
qsm show qA1 -f "path/to/quam_state/"

# Web UI in your browser
qsm serve                 # serves at http://127.0.0.1:5050
qsm browser               # same, and opens your browser automatically

# Desktop app (its own window)
python -m quam_state_manager

# Tests
python -m pytest tests/ -q
```

> The Generate / Re-generate Config wizard shells out to a conda/venv env that has the QM stack (`qm-qua`, `quam`, `quam_builder`, `qualang_tools`); the app itself never imports it.

## CLI Commands

Run as `qsm <command>` (or `quam-manager <command>`). Add `--help` to any command for its options, `--version` for the version, and `--json` where supported for scripting.

| Command | What it does |
|---------|--------------|
| `serve` | Run the web UI at `http://HOST:PORT` (default `127.0.0.1:5050`) |
| `browser` | Same as `serve`, and open it in your default browser |
| `show` | Show all properties of a qubit or qubit pair |
| `table` | Comparison table of selected properties across all qubits |
| `wiring` | Show the full port wiring map for all qubits |
| `search` | Search all values and keys in the QUAM state |
| `set` | Set a single value by dot-path |
| `save` | Save the current state to disk (with a `.bak` backup) |
| `diff` | Compare two `quam_state` folders and show differences |
| `export` | Export a qubit summary as CSV or Markdown |
| `scan` | Scan folder trees for `quam_state` directories + experiments |
| `trend` | Show how properties change across experiment snapshots |

## Features

- **JSON Pointer Resolution** -- resolves QUAM's `#/`, `#../`, `#./` references on-read with caching
- **Real-Time Search** -- prefix map + trigram index for <1ms keystroke search across all parameters
- **Chip Status Dashboard** -- topology view with heatmap-colored qubit cards, auto-fit scaling, coupler edges
- **Inline Editing** -- type-coerced edits with undo, rollback, and atomic saves (.bak backups)
- **Diff & Compare** -- 2-way diffs with float tolerance, N-way trends across experiments
- **Dataset Browser** -- HDF5 plotting, N-D interactive viewer, run comparison, bookmarks, tags, notes
- **Generate / Re-generate Config** -- wizard builds fresh QUAM configs, or rebuilds structure while preserving calibrated values
- **Pulses** -- full pulse CRUD with in-process live waveform preview
- **Compare Hub** -- same chip over time, same design across devices, different devices
- **Param / State History** -- timestamped snapshots, trend index, view + restore
- **CLI** -- inspection, editing, export (CSV/Markdown), comparison
- **Desktop App** -- pywebview wrapper, PyInstaller onedir bundle for standalone distribution

## Architecture

```
state.json + wiring.json
    -> loader.py (QuamStore)
        -> pointer_resolver.py (resolves #/, #../, #./ references)
        -> search_index.py (prefix map + trigram index)
        -> query.py (flattens nested JSON -> qubit/pair dicts)
        -> modifier.py (type-coerced edits, undo, rollback)
        -> saver.py (atomic writes, .bak backups, CSV/MD export)
        -> differ.py (2-way diffs, N-way trends)
```

See [`CLAUDE.md`](CLAUDE.md) for detailed architecture docs, key files table, and developer guide. Full module documentation is in the [`docs/`](docs/) directory.

## Build Standalone Executable

```bash
pyinstaller build/quam-manager.spec
# Output: dist/quam-manager/quam-manager.exe
```

Uses onedir mode for instant cold start (no temp extraction overhead).

## Tech Stack

- **Backend:** Flask, Jinja2, Typer, Rich
- **Frontend:** HTMX, Pico CSS, Split.js, Plotly.js (all bundled, no CDN)
- **Desktop:** pywebview
- **Data:** h5py (HDF5 reading)
- **Tests:** pytest (3000+ tests)

## License

[MIT](LICENSE)
