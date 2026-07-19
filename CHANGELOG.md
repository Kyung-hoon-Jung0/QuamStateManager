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

## v0.6.0 (unreleased — feat/typed-edit-env)

### Typed editing + environment validation (docs/56)

- Every list/matrix element is editable via dot-form numeric paths (`confusion_matrix.0.1`) in the Explorer, All values, and livediff accept — with a strict index gate (negative/malformed indices rejected, out-of-range = clean 400)
- Per-key expected types, layered: the selected python env's quam class schemas (introspected in-env, cached version+commit-keyed) > click-to-assign user types (⚙ in the Explorer; env overrides need an explicit confirm) > value inference; wrong-type writes are BLOCKED with provenance ("expected int — quam schema: DragCosinePulse.length")
- State↔env validation in Diagnostics: unknown fields / unimportable classes / missing required fields (the exact things that make `Quam.load()` fail) as aggregated error findings with Explorer deep-links; type/version mismatches as warnings; Probe + deep Validate (real `Quam.load` in the env) from the new card
- Explorer: add key (＋, with the class's missing-schema-keys suggestions), delete key (✕, with pointer blast-radius count), expected-type chips in the editor, server rejection reasons shown inline (no more silent red flash)
- All values v2: arrays + empty containers visible and editable (✎ JSON editor), pointer rows edit-through to their resolved target with a shared-by hint, per-row type chips
- Pulses: the selected env's pulse-class roster overlays the static catalog — env-verified classes lose the caution banner and re-enter DAC linting; false "unmodeled field" warnings for renamed fields disappear
- Fixed (latent since v0.1): the Explorer live-diff overlay always failed with "Could not render the live diff"; Accept-all now applies per-row so one rejected value can't roll back the rest
- Fixed: Infinity/NaN can no longer be written into state.json (invalid strict JSON)

### Cross-platform path handling (Windows / macOS / Linux audit)

- One canonical folder-identity helper (`path_match.fs_key`: resolve → NFC → case-fold only on case-insensitive hosts) now backs the working-copy key, the chip context/build-lock cache, scheduler folder verdicts, and workspace membership — on Linux two case-different folders no longer share (and cross-write!) one working copy; on macOS NFD-typed paths no longer split one folder into two copies; existing working copies migrate automatically
- Loading a chip through a symlink and through its real path now yields ONE context and ONE build lock (they used to race each other over the same working folder)
- Re-generate refuses a case-variant spelling of the source chip as the output on macOS (it would have rebuilt INTO the source and silently lost calibrations); history snapshot ids are validated against traversal-shaped input on Windows
- Folder browser: permission errors can no longer 500 the dialog; `~` is expanded everywhere paths are typed; build output/scripts paths must be absolute (a replayed Windows path on a mac/Linux server used to silently build into a literal `D:\...` directory under CWD); truncated listings say so; dot-folders are completable when you type the dot; POSIX names containing a backslash no longer corrupt every breadcrumb
- New-folder names are checked for cross-platform portability (Windows-reserved names, trailing dots, `<>:"|?*`)
- Datasets: same-tick in-place rewrites on coarse-clock filesystems (SMB/FAT) are now detected (size-aware fingerprints; Rescan forces a true re-check); the sidebar staleness check no longer trusts the local clock (skewed network mounts froze or thrashed it); on macOS two case-variant registrations of one folder dedupe by inode (they used to double every run and break HDF5 lock serialization); symlinked archive folders are discovered like the Datasets page always did (with cycle + runaway-walk bounds)
- macOS conda installs are discovered (`/opt/*`, Homebrew Caskroom, `~/.conda/environments.txt`); selecting a Windows `python.exe` from a mac/Linux server is refused with guidance (its features could never read POSIX work files) except under WSL
- Saves are rename-durable on POSIX (parent-directory fsync); rapid same-second saves keep every `.bak`; shared settings files write atomically; read-only dataset folders return a clear message instead of a 500
