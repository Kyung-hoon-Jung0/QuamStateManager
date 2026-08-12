# QUAM State Manager

Desktop + web tool for inspecting and editing quantum machine (QUAM) state files. Reads `state.json` + `wiring.json`, resolves custom JSON pointer references (`#/`, `#../`, `#./`), and provides a CLI, Flask web UI, and pywebview desktop app.

Built for researchers running superconducting qubit experiments who need to browse, compare, and tune parameters across hundreds of qubits.

## How it works — the one thing to know first

SM never edits your instrument's files behind your back. Three objects, one rule:

| | What it is |
|---|---|
| **Live chip** | the `state.json` / `wiring.json` your instrument and QUAlibrate actually read |
| **Working state** | your private editable copy — every edit lands here first |
| **Snapshot** | an immutable timestamped archive (State History) you can roll back to |

**The rule:** the live chip is written **only** when you press **Apply to live**
— one explicit press, never a dialog-chain, never behind your back. And that
press is reversible: SM snapshots the pre-apply live first, so **↺ Revert last
apply** is always there. Everything else (typing, filling, pasting, undo, redo,
staging a snapshot, loading a run's state) stages into the working state, listed
in the Review tray.

**Auto-apply** (default OFF) is the one way to change that rule, and you turn it
on yourself: press **⚡ Auto-apply** and from then on leaving an edited field
writes it straight to the live chip. While it is on the pill says so on every
page, every applied change is listed with its own **✕** to put it back, and the
moment the chip changes underneath you (an experiment writing to it) SM stops,
turns the mode off, and asks — it never overwrites a chip that moved.

Consequences worth knowing:

- `Ctrl+Z` / `Ctrl+Shift+Z` work **across saves** — undo past a save stages the
  inverse into the tray rather than touching the chip.
- An experiment writing the live files while you look at them never surprises
  you: SM tells you the chip drifted and offers *take live* / *keep mine* /
  *merge*, and it never swaps what you are looking at without asking.
- A run's frozen state can go to the chip in one click (**Apply to chip** on a
  run's State tab) precisely because that press is the explicit Apply, and it is
  revertible.

New here? The in-app **Help** page (sidebar, or `/help`) has the same model plus
a feature tour and every keyboard shortcut.

## Install & Run (users)

```bash
# From a clone (or a release archive) — a plain install is all you need:
git clone https://github.com/Kyung-hoon-Jung0/statemanager.git
cd statemanager
pip install .

# `qsm` (short) and `quam-manager` are now on your PATH:
qsm browser               # web UI at http://127.0.0.1:5050, opens your browser
qsm serve                 # same, without opening a browser
python -m quam_state_manager    # desktop app (its own window)

qsm --help                # every CLI command
qsm show qA1 -f "path/to/quam_state/"
```

Then point the app at a `quam_state/` folder (containing `state.json` +
`wiring.json`) via **State Load**, or open a QUAlibrate project from the
landing page.

**Where your data lives:** app state (working copies, Param/State History,
settings) is stored per user —
`%LOCALAPPDATA%\QUAM State Manager` on Windows,
`~/Library/Application Support/QUAM State Manager` on macOS,
`~/.local/share/QUAM State Manager` on Linux. (Running from a repo checkout
keeps the familiar repo-local `instance/` instead.) Your chip's live
`state.json`/`wiring.json` are only written on an explicit **Apply to live**.

> The Generate / Re-generate Config wizard shells out to a conda/venv env that has the QM stack (`qm-qua`, `quam`, `quam_builder`, `qualang_tools`); the app itself never imports it — pick the env inside the wizard.

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

## A typical session

1. **Open** a chip — *State Load* in the sidebar (or open a QUAlibrate project
   from the landing page, which brings its chip and data folders with it).
2. **Look** — *Chip Components* for the entity tables and the chip map,
   *Chip Status* for the heatmap dashboard, *Json Tree View* for anything else.
   Amplitudes carry their true output (dBm or volts) next to the raw number.
3. **Tune** — *Live State Edit* is the grid: type, `Ctrl+D` to fill a column
   selection, paste a column from a spreadsheet, `Ctrl+Z` to undo. Nothing has
   reached the chip yet.
4. **Review** — the tray at the bottom lists every pending change with its
   before → after and the difference; `✕` drops one (recoverable),
   *Discard all* drops them all (also recoverable).
5. **Apply to live** — one press. Reversible via *↺ Revert last apply*.
6. **Trace** — *State History* / *Param History* show what changed, when, and
   which experiment produced it; *Datasets* holds the runs themselves.

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

---

## For developers

```bash
# Editable install with dev tooling (keeps app state in the repo's instance/)
pip install -e ".[dev]"

# Tests (Windows: set PYTHONUTF8=1 — the node selfcheck drivers emit UTF-8)
python -m pytest tests/ -q
```

### Architecture

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

### Build a standalone executable

```bash
pyinstaller build/quam-manager.spec
# Output: dist/quam-manager/quam-manager.exe
```

Uses onedir mode for instant cold start (no temp extraction overhead).

### Tech stack

- **Backend:** Flask, Jinja2, Typer, Rich
- **Frontend:** HTMX, Pico CSS, Split.js, Plotly.js (all bundled, no CDN)
- **Desktop:** pywebview
- **Data:** h5py (HDF5 reading), numpy + scipy
- **Tests:** pytest (4000+ tests)

## License

[MIT](LICENSE)
