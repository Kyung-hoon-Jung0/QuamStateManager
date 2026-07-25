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

## v0.7.0 (2026-07-25)

### Autofit — one-button automatic fitting scheduler (docs/56_autofit_scheduler.md)

- New sidebar page `/autofit` (below Scheduler): one button runs a plan of calibration nodes over chosen targets, gates every fit deterministically, LLM-audits only the residual suspects, reverts rejected node writes, re-measures with adapted parameters, and reports everything from an append-only ledger with per-target keep/applied/retry/defer decisions
- Doctrine (docs/47, binding): the LLM **never emits a number** — its schema is `{verdict, failure_mode, reason}` (numeric emissions are discarded and flagged); corrections are ① deterministic gates ② revert via the node's own `patches[].old` (compare-and-swap, `coerce=False`) ③ re-run with family-keyed parameter adaptations — the calibration numbers always come from the node's own fitter, and a deterministic gate fail can never be overridden by an LLM accept
- Gate ladder per fit: node outcome → physical bands → raw-data cross-check (argmax only where provably valid, spectral signal-presence for oscillation/decay/2-D) → metric consistency/error ratios → history jump limits vs the pre-update anchor; per-family registry is code-curated (`core/autofit/families.py`), with iq_blobs verify-only
- Autonomy modes: `full` (apply with revert guarantee + pre-plan pinned snapshot) and `review` (restore-at-end); while a plan runs, the scheduler edit-lock engages and every `/scheduler/*` control route 409s (no two masters); all writes go through one writer path (build lock → `batch_set` → save → apply-to-live, one pull+re-stage retry)
- Ground-truth sim backend: `/autofit/start {backend: sim}` runs the whole loop hardware-free through the REAL write path (synthetic run folders indistinguishable to every SM reader, corruption modes wrong_peak/no_signal/noisy/out_of_band/drift); a CI accuracy ledger pins per-family false-accept coverage; RealBackend drives the Scheduler chassis with a lost-wakeup watchdog and normalized-prefix + time-window run attribution

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

### Generate Config wizard + Live State Edit polish (review-r7)

- TWPA ports (pump + isolation/spectroscopy) are now recognized by the step-5 wiring diagram's drag&drop and the wiring table's "Auto-allocated" column — they used to silently reject every drop (the diagram's role-color infrastructure already anticipated TWPA, but the wizard's own allocation regroup never mapped qualang_tools' terse `p`/`i` line-type keys onto it)
- The JSON drill-down panel (Wiring JSON popups, the Generate Config preview) defaults taller and is resizable by dragging its top edge, persisted globally across every call site
- Auto-allocate / Load into app / Preview config now match the compact Back/Next button style instead of rendering as full-size Pico default buttons
- Live State Edit's Properties menu shows every derived column by default (curated AND dynamic) — the prior opt-in model buried fields the search couldn't find; a column is hidden only once the user explicitly unchecks it (empirically timed at ~60ms warm / 106 extra columns on a real 17-qubit chip — no caching layer needed)
- Fixed: unchecking a dynamic column in the Properties menu reloaded the table and silently collapsed the menu itself (the htmx swap replaced the whole pane, including the `<details>` the menu lives in)

### Resonators / Flux / Couplers nav pages (customer request)

- Three new sidebar pages alongside Qubits/Pairs, each a curated channel-scoped table: Resonators (readout frequency/amplitude/length/threshold/time-of-flight, per qubit), Flux (joint/independent offset, flux point, delay, per qubit), Couplers (decouple/interaction offset, delay, per pair) — same filter/sort/JSON-drill-down/pagination as Qubits/Pairs, and a row click opens the existing Qubit/Pair inspector
- A qubit/pair without that channel is simply not a row (`QueryEngine.get_qubit`/`get_pair` gained `has_resonator`/`has_z`/`has_coupler`, a structural — not resolved-value — presence check); Flux and Couplers additionally hide themselves from the sidebar entirely when NO qubit/pair on the chip has that channel (e.g. a fixed-frequency chip has no Flux nav item), computed once per request in `_ctx()`

### Chip Status topology: control/target orientation on the pair edges (customer feedback)

- Every pair edge that resolves to a real control/target now shows which end is which: the edge label's own target-facing EDGE is reshaped into a point (clip-path flag/price-tag shape — right/left/up/down, picked from the edge's dominant grid axis), with control→target text inside, always upright. Two review rounds got here: a separate in-line arrowhead on the line read as too small to notice (removed); a small triangle glued onto the label box then read as a decoration rather than a direction, so the whole target-facing side of the box became the point instead
- Fixed along the way: `QueryEngine.get_pair`/`get_topology` derived `qubit_control`/`qubit_target` by taking the last `/`-segment of the raw pointer string — correct for chips that point straight at the qubit (`"#/qubits/qA2"`), but real tunable-coupler chips route through a wiring-side indirection (`"#/wiring/qubit_pairs/<w-pair>/c/control_qubit"` → `"#/qubits/qA2"`), where the naive split returned the literal wiring key `"control_qubit"` instead of a qubit id — surfacing as `"control_qubit"→"target_qubit"` in the topology label, and in the Pairs/Couplers tables' Control/Target columns. Now fully resolved through the pointer chain (which already recurses through any depth) and read off the landed qubit's own `id`, falling back to the old heuristic for anything that doesn't resolve to a qubit (dangling pointers, already-bare ids)
- NOT fixed (flagged, out of scope): `core/cr_semantics.pair_endpoints()` has the identical bug independently (it can't reuse the fix — it takes a bare pair dict, no store/root to resolve pointers against) and feeds `physical_edge_key`/`directed_partner`/the Generate wizard's `_pair_arch` gate-family detection; none of these are rendered as visible text today, but any of them could show/behave wrong for chips using the wiring-indirection convention. A proper fix needs `pair_endpoints()`'s signature widened to accept a store/root — deferred, separate from this fix.

### Chip Status heatmap: default color palette (customer feedback)

- The default heatmap + distribution-bar color palette changed from GnBu (pale mint → teal → dark blue) to the existing-but-unused "Blues" palette (pale blue → navy) — customers found GnBu not a pretty color; Blues reads as simpler/more modern and coordinates with the app's own primary-blue accents. A user's saved palette choice (`quam_heatmap_palette`/`quam_bar_palette`) is unaffected; only the un-set default changed. Considered but not built: a bespoke palette anchored exactly on the app's primary-blue token (kept in mind if Blues doesn't land well in practice)
- The Overview panel's per-metric tile accents (1Q/2Q fidelity, readout fidelity, T1, T2echo, T2Ramsey, gate coverage) were a SEPARATE hardcoded 3-tier red/amber/green pass-fail scheme, untouched by the palette change above — customer feedback: "pass/fail is ultimately just a magnitude read anyway," so `cardColor()` now interpolates continuously through the SAME active palette (`dCfg.colorScale`) instead of stepping between 3 fixed RAG colors; the old threshold triplets are kept only as a `[hi, lo]` calibration range (the middle "warn" cutoff no longer has a distinct role on a continuous gradient)

### Chip Status topology: default value colours + edge tiers unified to the app-blue ramp (customer feedback)

- The default qubit-card metric chips still repainted every thresholded value (T1, T2, fidelities) with a discrete green/amber/red spec verdict ("Spec colours" mode, on by default) while the "... more" hover panel kept the continuous palette — users called the clashing RAG mix dated. The spec-verdict repaint and its Health-bar checkbox are gone: colour now ALWAYS means relative magnitude on the shared palette (Blues by default), identical across the default cards, the hover panel and every heatmap cell. Pass/warn/fail verdicts still live on their dedicated surfaces (Health tiles, verdict banner, worst-qubit list, report card) — they just no longer paint the numbers
- The pair-edge fidelity tiers (SVG lines + edge labels) moved from green/orange/red to the same single-hue blue ramp — deep blue = good, washed-out toward the page = bad — with a luminance-flipped ramp on the dark theme (brightest = best against a dark background). A single-hue luminance ramp is inherently colorblind-safe, so the colorblind toggle's special-cased edge-colour overrides are gone too (the toggle itself stays for its other effects)
- Fixed (pre-commit review): `cardColor()` was only half-migrated to its new flat `[hi, lo]` range and still indexed the old nested triplet shape — every Overview tile accent rendered the darkest palette stop regardless of the value
