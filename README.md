# QUAM State Manager

Desktop + web tool for inspecting, editing and calibrating quantum machine
(QUAM) state files. Reads `state.json` + `wiring.json`, resolves QUAM's JSON
pointer references (`#/`, `#../`, `#./`), and ships a CLI, a Flask web UI, a
pywebview desktop app — and a cockpit for driving a calibration **agent** on
your own hardware.

Built for researchers running superconducting qubit experiments who need to
browse, compare and tune parameters across hundreds of qubits.

---

## The one rule

SM never edits your instrument's files behind your back.

| | What it is |
|---|---|
| **Live chip** | the `state.json` / `wiring.json` your instrument and QUAlibrate actually read |
| **Working state** | your private editable copy — every edit lands here first |
| **Snapshot** | an immutable timestamped archive (State History) you can roll back to |

**The live chip is written only when you press *Apply to live*** — one explicit
press. And that press is reversible: SM snapshots the pre-apply live first, so
**↺ Revert last apply** is always there.

Everything else — typing, filling, pasting, undo/redo, staging a snapshot,
loading a run's state — stages into the working state and is listed in the
Review tray with its before → after.

Two ways to grant that standing, both **off by default** and both visible on
every page while on:

- **Auto-Sync** — auto-push edits to the chip, auto-pull when something outside
  SM changes it, or both. A pull only discards unapplied edits when you tick
  *replace*; otherwise SM refuses and asks.
- **Auto-apply** — leaving an edited field writes it straight to the live chip.
  Every applied change keeps its own **✕** to put it back, and the moment the
  chip moves underneath you SM stops, turns the mode off, and asks.

Worth knowing: `Ctrl+Z` / `Ctrl+Shift+Z` work **across saves** (undo past a save
stages the inverse into the tray rather than touching the chip); and when an
experiment writes the live files while you are looking at them, SM says the chip
drifted and offers *↓ take live* / *↑ keep mine* / *⇄ merge* — it never swaps
what you are looking at without asking.

New here? The in-app **Help** page (sidebar, or `/help`) has the same model plus
a feature tour and every keyboard shortcut.

---

## The Agent cockpit

SM can drive your own **Claude Code** or **Codex** CLI as a calibration agent,
against your real chip, with the safety rules in front of you rather than buried
in a prompt.

**Rule 0: only a person's click starts hardware.** The agent can read
everything, propose anything, and start nothing.

- **Agent** (sidebar) — a chat panel over your logged-in CLI. Its answers, its
  tool calls, the plans it proposes and the runs it asks for all arrive as cards
  in one feed. You press **Start**; it never does.
- **Modes**, per chip: `ask-all` (every run needs a press) · `ask-writes` (runs
  freely, every write to the chip needs a press) · `auto` (writes inside the
  limits you set). The mode is on screen at all times.
- **Approvals** — a held write is a card showing the old value, the new one and
  the Δ, editable before you accept. An approval decides the writes it proposed,
  and nothing else.
- **Limits**, per chip — max writes per plan, max |Δ| per parameter family,
  stop-loss per target and per plan, a wall-clock `stop_by`, and a refusal to
  run when a human ran something too recently.
- **Arm / Stop** — arming says the agent *may* start hardware runs; *Stop after
  this run*, *Stop now* and *End session* are always one press away.
- **Calibration log** (`/journal`) — one plain `.md` per chip per day, in a
  folder you pick (an Obsidian vault is fine). The agent must record **why**,
  not just what. Run numbers and dot-paths become links back into SM; a person
  can claim or correct the authorship of any run and leave a note.
- **The pill**, in the topbar of every page — one line of truth about what the
  agent is doing right now, or that it is waiting for you.

SM registers itself with the CLI as an MCP server (`quam-state-manager`,
25 tools: read the state, search it, read runs and diagnostics, propose a plan,
request a run, stage an edit). Setup is a page (`/agent/setup`) that probes what
is actually installed and tells you which step is missing — it never sees or
stores your login.

---

## Install & run

```bash
git clone https://github.com/Kyung-hoon-Jung0/QuamStateManager.git
cd QuamStateManager
pip install .

qsm browser            # web UI at http://127.0.0.1:5050, opens your browser
qsm serve              # same, without opening a browser
python -m quam_state_manager    # desktop app (its own window)

qsm --help             # every CLI command
qsm show qA1 -f "path/to/quam_state/"
```

Then point the app at a `quam_state/` folder (containing `state.json` +
`wiring.json`) via **State Load**, or open a QUAlibrate project from the landing
page.

**Where your data lives:** app state (working copies, Param/State History,
settings) is per user —
`%LOCALAPPDATA%\QUAM State Manager` (Windows),
`~/Library/Application Support/QUAM State Manager` (macOS),
`~/.local/share/QUAM State Manager` (Linux). A repo checkout keeps the familiar
repo-local `instance/` instead. Your chip's live files are only written on an
explicit **Apply to live**.

> The Generate / Re-generate Config wizard shells out to a conda/venv env that
> has the QM stack (`qm-qua`, `quam`, `quam_builder`, `qualang_tools`); the app
> itself never imports it — pick the env inside the wizard.

---

## A typical session

1. **Open** a chip — *State Load*, or a QUAlibrate project from the landing page.
2. **Look** — *Chip Components* for the entity tables and the chip map, *Chip
   Status* for the heatmap, *Json Tree View* for anything else. Amplitudes carry
   their true output (dBm or volts) beside the raw number.
3. **Tune** — *Live State Edit* is the grid: type, `Ctrl+D` to fill a selection,
   paste a column from a spreadsheet, `Ctrl+Z` to undo. Nothing has reached the
   chip yet.
4. **Review** — the tray lists every pending change; `✕` drops one, *Discard all*
   drops them all (both recoverable).
5. **Apply to live** — one press, reversible via *↺ Revert last apply*.
6. **Trace** — *State History* / *Param History* show what changed, when, and
   which experiment produced it; *Datasets* holds the runs themselves.

---

## CLI

`qsm <command>` (or `quam-manager <command>`). `--help` on any command,
`--version` for the version, `--json` where supported for scripting.

| Command | What it does |
|---------|--------------|
| `serve` / `browser` | Run the web UI (default `127.0.0.1:5050`); `browser` also opens it |
| `show` | All properties of a qubit or qubit pair |
| `table` | Comparison table of selected properties across all qubits |
| `wiring` | The full port wiring map |
| `search` | Search every value and key in the state |
| `set` / `save` | Set one value by dot-path; save to disk with a `.bak` backup |
| `diff` / `trend` | Compare two `quam_state` folders; show properties across snapshots |
| `export` / `scan` | Export a qubit summary (CSV/Markdown); scan trees for chips + experiments |

---

## Features

- **JSON pointer resolution** — `#/`, `#../`, `#./` resolved on read, cached per store
- **Real-time search** — prefix map + trigram index, <1 ms per keystroke, one grammar app-wide
- **Chip status + component map** — topology from the wiring itself, heatmap cards, coupler edges
- **Live State Edit** — spreadsheet grid: multi-select, fill-down, paste, pinned columns, per-column history
- **Diff workbench** — one comparison surface (tree or list, differences-only), 2-way and N-way
- **Datasets** — HDF5 plots, an N-D interactive viewer, click-to-stage calibration contracts, tags/notes
- **Generate / Re-generate Config** — build a fresh QUAM config, or rebuild the structure while keeping every calibrated value
- **Pulses** — full pulse CRUD with an in-process live waveform preview, pinned bit-for-bit against quam
- **Param / State History** — timestamped snapshots, a per-leaf change-point index, view + restore
- **Auto Calibrate** — a closed fitting loop with deterministic gates; the LLM never emits a number
- **Agent cockpit** — the section above
- **Desktop app** — pywebview wrapper, PyInstaller onedir bundle

---

## For developers

```bash
pip install -e ".[dev]"

# Windows: PYTHONUTF8=1 is required — the node selfcheck drivers emit UTF-8
python -m pytest tests/ -q
npm install && npm run selfcheck     # jsdom pins that drive the real shipped JS
```

`npm install` matters: the `.cjs` selfchecks skip — looking green — without
jsdom.

### Architecture

```
state.json + wiring.json
    -> loader.py (QuamStore)
        -> pointer_resolver.py   #/, #../, #./
        -> search_index.py       prefix map + trigram
        -> query.py              nested JSON -> qubit/pair dicts
        -> modifier.py           type-coerced edits, undo, rollback
        -> saver.py              atomic writes, .bak backups, CSV/MD export
        -> differ.py             2-way diffs, N-way trends
```

Frontend is HTMX + vanilla JS — no React/Vue. All assets bundled, no CDN.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture, the key-files table and
the developer guide; per-topic write-ups are in [`docs/`](docs/).

### Build a standalone executable

```bash
pyinstaller build/quam-manager.spec       # -> dist/quam-manager/quam-manager.exe
```

Onedir mode, for instant cold start (no temp extraction on every launch).

### Tech stack

Flask · Jinja2 · Typer · Rich · HTMX · Pico CSS · Split.js · Plotly.js ·
pywebview · h5py · numpy + scipy · pytest (5,700+ tests) + jsdom selfchecks

## License

[MIT](LICENSE)
