# Changelog

## v0.1.0 (2026-04-05)

Initial release.

### Core

- JSON pointer resolution engine (`#/`, `#../`, `#./`) with cycle detection and caching
- QuamStore: thread-safe loader merging state.json + wiring.json with RLock
- Type-coerced inline editing with undo, batch rollback, and change log
- Atomic saves via tmp file + `os.replace()` with timestamped .bak backups
- 2-way diff with float tolerance, N-way experiment trend analysis
- Real-time search: prefix map + trigram index (<1ms keystroke latency)
- Workspace scanner with LRU cache (max 10 stores, ~40MB)

### Web UI (53 routes, 47 templates)

- Chip Status dashboard with topology cards, heatmap coloring, auto-fit scaling
- Explorer: full JSON tree with lazy loading and pagination
- Qubits/Pairs tables with chain filtering and color-coded fidelity cells
- Property table with grouped selector and CSV/Markdown export
- Instrument wiring diagram
- Diff viewer with side-by-side comparison
- Dataset browser with HDF5 multi-select plotting, bookmarks, tags, notes
- Trend dashboard with sparklines and N-way experiment comparison
- Global search with category tabs
- Pending changes tray with per-change discard
- History panel: auto-snapshot on file change, timeline, snapshot comparison
- Live monitoring: mtime-based polling with configurable interval
- Folder browser dialog with recent folders and path autocomplete

### CLI (10 commands)

- `show`, `list`, `search`, `set`, `diff`, `compare`, `export`, `scan`, `trend`, `table`

### Desktop

- pywebview wrapper with random port assignment and health check
- PyInstaller onedir bundle (instant cold start)

### Quality

- 680 tests across 14 test files
- Error handling for malformed JSON, missing files, Windows file locking
- Path validation guardrails for browse/load endpoints
- HTMX race condition prevention with `hx-sync`
- Loading indicators on all clickable rows

## v0.5.0 (2026-07-16)

Generate-Config wizard: customer feedback batch r3 (`docs/53_generate_feedback_r3.md`).

### Wizard

- CZ pairs auto-orient by frequency: higher-RF_freq qubit = control (per-pair `manual` pin; CR/regenerate never flip; build-time warning safety net)
- User-settable qubit naming: scheme presets (q1…, q0…, grid letters qA1/qB2, custom prefix) + per-qubit rename with one-pass identity remap
- As-you-type inline validation in the Populate step: hardware reach, bands, LO window/demod hole, |amp|>1, immediate feedline Σ|amp|>1 clip, FSP bounds — unit-aware, on the keystroke
- Absolute-dBm power entry (Power input toggle): pulse powers in dBm, port FSP auto-allocated (−20 dBm → FSP 0 / amp 0.1); readout feedline Σ|amp|>1 clip warning now fires in BOTH power modes
- Default-value presets archive: named server-side sets of populate defaults (save/apply/delete from step 6; `instance/gen_presets/`)
- Editable Python build-script export: step-7 toggle writes `01_make_wiring.py` / `02_build_machine.py` / `03_generate_config.py` / `README.md` with the chip's values inlined — verified to rebuild JSON-identical state/wiring in a real QM env

### Fixes

- Folder browser: fetch timeout + Retry, stale-response guard, POSIX breadcrumbs (Linux navigation was broken), mkdir double-submit guard, per-input last-folder memory; `/browse` reports unreadable folders instead of listing empty; POSIX default listing is `$HOME`
- Output/scripts folder paths survive a lost browser session (localStorage mirror)
- Step-4 pair dropdowns re-render on step entry (stale Control/Target after external reorder)
- Qubit renumber now also remaps TWPA qubit lists

### CLI

- New `qsm` console alias + `qsm browser` command (serve + auto-open browser)
