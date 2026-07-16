# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**QUAM State Manager** is a desktop + web tool for inspecting and editing quantum machine (QUAM) state files. It reads `state.json` + `wiring.json`, resolves custom JSON pointer references, and provides a CLI, Flask web UI, and pywebview desktop app. It also generates fresh QUAM configurations (Generate Config wizard) and previews `machine.generate_config()` output (Config Viewer).

## Commands

**Run tests** (requires the project's conda env, `qm_mng`, on the maintainer's WSL setup):
```bash
conda run -n qm_mng python -m pytest tests/ -q
# Single test file:
conda run -n qm_mng python -m pytest tests/test_pointer_resolver.py -v
# Single test:
conda run -n qm_mng python -m pytest tests/test_loader.py::test_load_basic -v
```

**Run web dev server** (use a non-default port — stale dev servers stack up):
```bash
conda run -n qm_mng python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```

**Run CLI:**
```bash
python -m quam_state_manager.cli show qA1 -f "path/to/quam_state/"
python -m quam_state_manager.cli --version
python -m quam_state_manager.cli --help
```

**Run desktop app:**
```bash
python -m quam_state_manager
```

**Build standalone executable:**
```bash
pyinstaller build/quam-manager.spec
# Output: dist/quam-manager/quam-manager.exe
```
Uses **onedir mode** (not onefile) for instant cold start — onefile extracts to temp on every launch (3–10s overhead).

## Architecture

### Core Pipeline

```
state.json + wiring.json
    → safe_io.read_state_wiring (FILE_SHARE_DELETE + mtime-bracketed pair read)
        → loader.py (QuamStore — owns per-instance pointer cache)
            → pointer_resolver.py (resolves #/, #../, #./ references via store cache)
            → search_index.py (prefix map + trigram index)
            → query.py (flattens nested JSON → qubit/pair dicts)
            → modifier.py (type-coerced edits, undo, rollback)
            → saver.py (atomic writes via safe_io.atomic_write_json, .bak rotation)
            → differ.py (2-way diffs, N-way trends)
            → history.py (Param History: SQLite property index, snapshots)
            → config_view.py (Config Viewer — preview generate_config())
            → config_generator.py (Generate wizard subprocess driver)
```

### QUAM Pointer System

QUAM files avoid value duplication via three pointer types:
- `#/qubits/qA1/f_01` — absolute from root
- `#../x180/length` — relative to parent
- `#./self_ref` — relative to self

`pointer_resolver.py` resolves these **on-read, never in-place**. The cache is **per-`QuamStore`** (each store owns its own dict + lock), so two chips loaded in the same process with same-named qubits never share resolutions. Unresolvable pointers return raw strings + log warnings (real data has dangling pointers). See `docs/32_red_team_phase_2.md` finding 0.1 for the chip-isolation bug this fixes.

### QuamStore (Thread-Safe)

`core/loader.py` holds merged state+wiring dicts, change log, search index, and per-store pointer cache in a single `QuamStore`. All mutations are guarded by `threading.RLock` (Flask workers + pywebview UI thread share state). Loads route through `safe_io.read_state_wiring` so the read never blocks an experiment's atomic save on Windows.

### Working-Copy / Live-File Safety

Experiment programs read and write the same `state.json` + `wiring.json` the app watches; on Windows a plain `open()` for read blocks their atomic `os.replace` save. So the app operates on a **working copy** under `instance/working_state/` (`core/working_copy.py`): the `QuamStore` and `Saver` are built on the copy, background change detection is `os.stat`-only, and the live files are read only on an explicit user **sync** and written only on an explicit **apply to live**. All live-file content I/O — across `loader.py`, `scanner.py`, `history.py`, `working_copy.py`, and the Saver — goes through `core/safe_io.py` (share-delete reads, `ReplaceFileW` writes, transient retry, mtime-bracketed pair read). LRU eviction in `_activate_quam` keeps the on-disk working folder so a re-load rehydrates the user's unapplied edits via `working_copy.load`. See `docs/28_conflict_safe_io.md`.

The **load path is content-hash-aware** (fixes the stale-chip bug — replacing a live folder's files out-of-band used to keep showing the old chip even across restarts): each sync point records `synced_live_hash` (sha256 of parsed state+wiring) in the meta sidecar, and `reconcile_with_live` runs on load/select (never in background polls) — live replaced + working copy provably clean ⇒ auto-pull; replaced + (possibly) edited ⇒ keep + `live_diverged` banner (never clobber, all sync/save/apply mutators serialize on the per-folder build RLock); unreadable live ⇒ never treated as replaced. Working-copy **GC** (`/api/working-copies/scan|gc` + threshold banner) deletes only provably-clean copies — transient read failures classify as kept-unverifiable, never deletable.

### Scanner + LRU Cache

`core/scanner.py` discovers experiment folders lazily. `QuamStore` instances are LRU-cached (max 10, ~40MB total) — only loaded when selected in the UI. `node.json` reads also go through `safe_io.read_json` so workspace navigation can't break a still-active experiment's write.

### Web UI (HTMX)

Flask routes return either full pages or partial `_*.html` template fragments for `hx-swap`. Partials are named `_*.html`; full pages are named without underscore. No React/Vue — HTMX + ~5,100 lines of vanilla JS for UX polish + ~3,000 lines for the Generate-Config wizard. Routes in `web/routes.py` (~3,800 lines, ~90 endpoints). Templates: 63 files (17 full pages + 46 partials). All colors are parameterized as ~430 CSS custom properties with light/dark theme support.

Clicking a Plotly data point in a dataset figure opens a **confirmation popup** (one row per affected dot-path, with editable new value, per-row Apply + Apply All); replaces the old auto-apply flow. Per-row Apply uses `/field/edit`; Apply All uses `/field/edit-batch` backed by `modifier.batch_set`-style atomic rollback. See `docs/36_plot_click_confirm_popup.md`.

The qubit and pair inspector panes (`/qubit/<name>`, `/pair/<name>`) carry a **sticky in-panel search bar** in `_inspector_header.html` (gated by `inspector_type in ('qubit','pair')`). The JS `filterDetailPanel` in `app.js` filters `<tr>` rows across all `<details>` sections by AND-tokens, includes section names + editable `<input>.value` in the haystack, auto-opens matching sections, hides empty ones, and shows "X of Y" — same UX as `/compare`'s diff filter but spans multiple tables.

Comparison lives in the **Compare hub** (`/compare-hub`, sidebar "Compare") — one URL-canonical surface (the basket IS the query string: `src=` ref tokens `ws:/run:/hist:/drop:/working:` + `bucket` + `preset` + `ref` + `map`) with three user-declared contexts: ① same chip over time, ② same design/different device (grid auto-map + dropdown mapping editor, A1-persisted per network-token + device-stable anchors), ③ different devices (chip cards). Engine = `core/compare.py` + `core/compare_sources.py` (isolated source pool — never the scanner LRU / `_quam_cache`); 3-preset tolerance (Exact/Lab default/Wide); hairline rows with lazy per-group loading; structure strips via `TopoGraph.renderStatic`; drag-drop stash under `instance/compare_drops/`. The legacy `/diff`, `/compare` (POST) and `/chip-compare` redirect into the hub (HTMX callers get `HX-Redirect`); their tab fragments are still served until the redirects soak. Snapshot-vs-snapshot diff for a single chip stays in Param History (`/api/history/<ts>/diff`, `/api/history/compare`); State History rows and the Chip Status drawer carry additive `⇄ Compare…` deep links. See `docs/49_compare_hub_redesign.md` (binding amendments) and `docs/35_chip_compare.md` (legacy).

UI state persisted in `localStorage`: `quam_font_size`, `quam_sidebar_collapsed`, `quam_tray_open`, `quam_split_sizes`, `quam_theme`, `quam_exp_list_compact`, `recentFolders`.

### Multi-Context Registry

`app.config["contexts"]` maps names → context dicts with a `type` field (currently `"quam"`). Designed to accept HDF5/dataset types without restructuring. Routes use helpers (`_store()`, `_engine()`, `_modifier()`, etc.) to pull from the active context transparently.

### Param History

`core/history.py` (`HistoryManager`, ~2,300 lines) keeps timestamped snapshots of each chip under `instance/history/<chip>/` plus a per-chip SQLite property index (`index.sqlite`) for fast trend queries. Content-hash dedup, fingerprint-aware multi-chip routing, alignment scan against the workspace, chip-decisions persistence (atomic + locked), v1 + v2 migrations. Triggers: `save` / `manual` / `auto` / `experiment`. The v2 migration uses a build-once `{ChipFingerprint → chip_dir_name}` index with a purity-ratio tie-breaker so mixed-attribution dirs are resolved correctly in O(N) instead of the older buggy O(N×M×S) per-snap scan. See `docs/20_param_history.md`, `docs/21_multi_chip_support.md`, `docs/23_param_history_performance.md`.

The auto-incremental backfill on page open is **single-attempt per chip per browser-tab session** (sessionStorage marker set in `_paramHistoryPollBackfill`'s `done`/`error` branches; checked by `paramHistoryMaybeAutoBackfill`). Without this guard, per-entry ingest failures (file locks, missing source state.json) leave the workspace-vs-index gap open and the `htmx:afterSwap` re-fires the backfill forever. Failed entries are captured per-backfill in `_backfill_state[key]["failed_entries"]` (cap 50) and surfaced via an amber banner on `/param-history` with a "Retry import" button that clears the session marker. The capture happens in `_ingest_entries_into`'s real-failure branches (missing state.json, `OSError`/`ValueError` during copy) — not for content-hash dedups or timestamp dedups.

### State History

`/state-history` (sidebar, below Bulk Edit) is a **view + restore layer** over the same `HistoryManager` snapshot store as Param History (no new capture path). It lists full `state.json`+`wiring.json` snapshots over time, framed by the experiment that produced each, with diff/compare (reuses `/api/history/<ts>/diff` + `/api/history/compare`) and two restore modes. **Mode 1 stage** (`/state-history/<ts>/stage`) loads a snapshot into the working copy for review→Apply; **Mode 2 restore-live** (`/state-history/<ts>/restore-live`) replaces the live chip directly. Both write through `working_copy.apply_to_live`/`safe_io` under the **single build lock** and rebuild every derived cache via the shared `_rebuild_after_working_copy_replaced(ctx)`. restore-live's safety gates are **independent**: origin≠live (archive→409), unsaved edits (`force_pending=1`), and wiring-topology `align()` mismatch (`force_align=1`) — one token never collapses two gates, so the topology warning is always shown before live wiring is overwritten; the current live is snapshotted first (restore aborts if that fails) so a restore is reversible. All mutators bind the lock + snapshot source to the **captured** `ctx["path"]` (`_active_wc_lock(ctx)`), never the live-active context, so a concurrent `/load` can't cross-wire them. Cross-menu: stage/restore emit `HX-Trigger: pulses-changed, stateRestored` (the latter closes a stale inspector open on another menu); `/load` + `/workspace/select` return `HX-Redirect` so the chip-identity tray + origin badge refresh on switch. SnapshotMeta gained `label`/`pinned` (pinned = prune-exempt); `_prune` reads per-snapshot meta only when over budget. See `docs/41_state_history.md`. The gates here were hardened by a pre-customer multi-role audit (DOM-XSS in pulse Verify, the collapsible force gate, the stale tray-on-switch — all fixed).

### Dataset Support

`core/dataset.py` (DatasetStore, ~1,400 lines) + `core/experiment_data.py` (ExperimentContext) load experiment runs from `node.json` + `data.json` + HDF5 files. Reads route through `safe_io.read_json` so a still-active experiment's writeback isn't blocked. A separate `_h5_lock` guards h5py reads (not thread-safe). Tag/bookmark/note mutations are guarded by `_tags_lock` and roll back the in-memory change if the disk write fails. `_data_json_cache` is an LRU bounded at 200 entries (so 10k-run workspaces don't pin multi-GB of parsed JSON). `get_figure_path` containment uses `Path.is_relative_to`. 19 dataset routes handle browsing, detail view, HDF5 plotting (multi-select), bookmarking, tagging, notes, comparison, and trend dashboards. Incremental rescan keyed on per-folder mtime fingerprint, virtual-scroll table fed by a compact JSON payload + delta-poll endpoint, DatasetStore LRU (max 5).

### Interactive Data (ndview + click contracts)

The dataset detail page's **Data tab** is `core/ndview.py` + `web/static/ndview.js` (+ `plot-theme.js` house theme): a generic, crash-free N-D viewer over every variable of every `ds_*.h5` file — h5py-only `DIMENSION_LIST` deref for true axis names (real files have no `_ARRAY_DIMENSIONS`), name-based dim classification (entity/categorical/sweep/synthetic), peak-preserving decimation with kept-index click-snap, byte-aware budgets (no cube ships >~4–5 MB), and a bytes-caching LRU (24 entries / 64 MB; per-request `uid/click` block byte-spliced around the cached cube). `/dataset/<uid>/ndview[/data]` always returns HTTP 200 with classified fallbacks — enforced by corpus-invariant tests over the real archives. The client cube cache is uid-scoped with a mount-generation token (run-flipping can't cross-render). ndview calls `window._plotlyRender` **positionally** and attaches click handlers inside the render promise; `tests/ndview_selfcheck.cjs` loads the *real* app.js renderer to pin the convention.

The **Interactive tab**'s figures carry **click contracts** (`core/interactive_plots/contracts.py` + `recipes/`): each recipe bakes the calibration node's own update formula into the figure's `clickable` block (per-target affine + named transforms `dbm_to_amp`/`ceil4`/`wrap01`, with a provenance block shown in the popup), so a click stages the **node-update value**, not the raw coordinate. Key rules: `pre_update_value` is patches-first with qubit-scoped suffixes (the run snapshot is POST-update when patches exist); `run_operation` reads flattened `run.parameters` top-level first; relative axes (detuning/prefactor) are always view-only; recipes must gate on `_normalize_node_name`, never raw `1Q_/2Q_` prefixes. Round-trip goldens (`tests/test_click_contracts.py`, `tests/test_cz_contracts.py`) pin click-at-fit-optimum == the node's own `patches[].value` on real archive runs. `core/click_targets.py` ranks ndview write-candidates (absolute axes only, positive dim-name binding resolved client-side by name — never by row index). Everything stages through the audited `_openPlotApplyPopup` → `/field/peek` → `/field/edit[-batch]` path with the chip-identity 409 gate and non-blocking |amp|>1 / |offset|>0.5 V warnings. `build_interactive_figure` has a 4-run bundle-input LRU (mtime-fingerprint keyed; warm tile ≈20 ms). See `docs/48_ndview_click_contracts.md`.

### Pulses Page

**Pulses** (sidebar, below Pairs) is the first-class pulse management surface: every pulse on the chip (qubit channel operations + pair-gate flux slots) in one sortable table with server-side SVG sparklines, a detail inspector with instant per-field commit + a **live waveform preview** (typing → debounced stateless `/api/pulse/synth` → dashed overlay), full-catalog create (~15 quam classes), and delete/rename/duplicate with reverse-pointer (`used_by`) safety. The preview is computed **in-process** by `core/waveform_synth.py` — a numpy+scipy transcription of every `quam.components.pulses` `waveform_function()`, pinned bit-for-bit (rtol=1e-9) against the user's LabC env by `tests/test_waveform_golden.py` + `generator/run_waveform_golden.py` (regenerate the committed golden after a quam upgrade). `core/pulse_catalog.py` is the single source of truth for class schemas/defaults/inferred-length math; `core/pulse_index.py` does enumeration + the resolved-absolute-path reverse-pointer index + pointer rewriting for duplicate/rename. Pointer edits are explicit 3-mode (`value` follows to the target with impact disclosure, `literal` breaks the link typed-correctly via `coerce=False`, `pointer` re-links). "Verify vs config" overlays `machine.generate_config()` ground truth with honest staleness (`store.mutation_seq` stamped into `generated_config_meta`). The sync replay map is **op-tagged** (`{path: (set|create|delete, value)}`) so created/deleted pulses survive pull-with-reapply. See `docs/40_pulses_page.md`.

### Generate Config + Config Viewer

`core/config_generator.py` drives the **Generate Config** wizard: discovers conda envs, probes for the QM stack (`qualang_tools`, `quam_builder`, `quam`), spawns `generator/run_build.py` in a user-selected env, and reads the resulting `_result.json`. The State Manager process never imports the heavy QM libraries. `generator/run_build.py` + `generator/run_generate_config.py` are standalone scripts run by the chosen interpreter (shared stdlib-only helpers live in `generator/_script_common.py`, imported after a defensive `sys.path` insert). Both runners share `_run_script_outcome()`; the wizard's build-result step has a **Preview config** button (`POST /generate/preview-config`) whose result is stashed and transplanted onto the store by `/generate/load` when the content hash matches (seed-on-load).

After `machine.save()`, the build flow runs a post-save fix-up that rewrites each readout MW input port's `downconverter_frequency` as a JSON pointer to its paired MW output port's `upconverter_frequency` (`_link_input_downconverters_to_outputs` in `generator/run_build.py`). Hardware-wise these always share one physical LO; encoding the input as a pointer locks the constraint so it can't drift under later edits. Matches the example-9q-rack encoding already in production.

The Populate step is **absolute-first**: RF frequencies are the primary input, with the LO auto-derived per MW-FEM port pair by `solveLoWindow` (`generate.js`) — minimize max|IF| subject to the ±400 MHz IF window, a Nyquist band covering every member under `bandOf`'s band-1-first precedence, and the 5 MHz resonator demod hole (xy may sit at IF=0); named warnings + midpoint fallback on infeasibility. A **Power input** toggle (`quam_gen_power_mode`) adds an absolute-dBm mode: pulse powers typed in dBm, the port `full_scale_power_dbm` auto-allocated (strongest pulse picks the integer FSP `ceil(P+6.02)`, preferred [0,10], floored at 0 / capped at 18, amp band 0.01–0.5; readout is a bank edit — one dBm per feedline under an `n·amp ≤ 0.5` coherent-sum budget). Stored spec representation is identical in both modes (fsp + amps; `P = FSP + 20·log10(amp)` is lossless), so `run_build.py`, `validate_spec` and old drafts are untouched. The Σ|amp| > 1 feedline clip warning is **mode-independent** (a DAC physical fact — it fires in manual mode too); the 0.5-headroom advisory and per-tone findings stay absolute-only. Pinned by `tests/generate_power_selfcheck.cjs` (user example: saturation −20 dBm → FSP 0 / amp 0.1).

The r3 feedback batch (see `docs/53_generate_feedback_r3.md`) added: **as-you-type inline validation** in the Populate step (debounced per-cell `validateCellValue` — hardware-reach/band/window/demod-hole/amp/feedline-Σ/FSP checks, unit-aware, red/amber cell decoration; JS↔Py constants parity pinned against `diagnostics`/`spec_constraints`); **CZ automatic control/target orientation** (higher-RF_freq qubit = control; `czAutoOrient` flips the stored pair + its whole identity web on every frequency commit, `cz_order: manual` pins, CR/regenerate never flip, `run_build._cz_order_warning` is the build-time safety net); **user-settable qubit naming** (step-4 scheme presets `q1…`/`q0…`/grid-letters/custom + per-qubit rename via one-pass `applyQubitIdMap`; name rule `^q[A-Za-z0-9_]+$` mirrored in `validate_spec`); a **default-value presets archive** (`core/gen_presets.py`, `instance/gen_presets/`, `/generate/presets` routes, capture uniform→defaults / differing→overrides, apply fill-only-empty or overwrite); and **editable Python build-script export** (`core/script_emitter.py` — step-7 toggle writes `01_make_wiring.py`/`02_build_machine.py`/`03_generate_config.py`/`README.md` with values inlined; fidelity = build_connectivity insertion-order mirroring + machinery extracted verbatim from `run_build.py` via `inspect.getsource`; end-to-end JSON-equality pinned by `test_script_emitter_live.py`).

`core/config_view.py` is the **Config Viewer**: shows the QM config dict and per-pulse waveform plots that a calibration script would receive after `machine.generate_config()`. Same subprocess pattern as the wizard. In the sidebar it nests under Generate Config as a collapsed-by-default sub-item (`#config-subnav`); the per-qubit/pair detail sections carry inline Regenerate buttons. `waveform_for_operation()` returns `{element, operation, pulse, traces:[...]}` (I+Q both; `length_inferred` flags the 16-sample placeholder). The cache is staleness-checked at render time: `meta["basis_hash"]` (hash of the working-copy files at regenerate) vs `working_copy.content_hash(store.state, store.wiring)` — see `_config_stale()` in `web/routes.py`. See `docs/27_config_generator.md` and `docs/30_config_viewer.md`.

### Re-generate Config

**Re-generate config** (sidebar, nested under Generate Config; `/regenerate`) lets a user take a chip they already built, **edit its structure** in the wizard (move ports, change bands, add/remove qubits or pairs, gate family), rebuild it fresh, and **keep every calibrated value** — plus walk away with a single editable **Python build recipe**. Pipeline (`core/regenerate.py`): `regen_spec.reconstruct_spec` inverts the chip → spec (wiring pinned from port pointers, pairs from `state.qubit_pairs`, populate by inverting `apply_populate` through pointer chains) → user edits in the wizard (`mode="regenerate"`) → `config_generator.run_generator` builds fresh structure into a NEW folder (never the source) → `regen_merge.merge_states` carries calibrated values (tier-1) + grafts user-added op/macro subtrees (tier-2) → `regen_script.emit_build_script` writes `build_<chip>.py`. The State Manager process never imports quam/quam_builder; everything here is pure JSON/string work + the subprocess build.

Merge fidelity details: **entity collections** (`qubits`/`qubit_pairs`/`ports`/`octaves`/`mixers`) are never resurrected. **TWPAs build natively** — modern `quam_builder` exposes `Connectivity.add_twpa_lines`, so `reconstruct_spec` pins each pump line from `wiring.twpas`, `run_build` builds them, and the merge carries the OLD pump calibration via tier-1 (LabA: 4 TWPAs, 71 config elements). Only the legacy 0.2.0 builder (no TWPA wiring registry) can't; there `graft_twpa_wiring` carries the OLD `twpas` + `wiring.twpas` + referenced ports as a fallback so the chip still compiles. **Pair ids** are reconciled by `(control, target)` membership (builder may emit `qA2-A1` vs source `qA2-qA1`). Redundant OLD ops the rebuild re-expressed under new names (unreferenced + broken pointers) are **pruned**. Transparency counters (`carried`/`grafted`/`superseded`/`residual_lost`/`pruned_ops`/`twpa_wiring_carried`/`dangling_grafts`) surface in the build-result panel. An exact-spec **sidecar** at `<out>/.regen/generate_spec.json` (hash-keyed, subfolder so `Quam.load()` ignores it) is preferred on a later re-generate. The emitted **recipe** combines QM's generate + populate into one file (public idiom only: `qualang_tools.wirer` + `quam_builder` + `quam_config.Quam` + `pair_gates`), pins ports, seeds real bands (never hardcoded), and branches fixed vs tunable coupler at runtime — verified building both chip classes end-to-end in the LabB env. It's a *recipe* (structure + design-time seeds), not a calibration snapshot. See `docs/51_regenerate_config.md`.

### Environment Capability Validation

A build only works if the selected conda/venv env exposes the exact functions the chip needs, and **package versions lie** (LabB ships `quam_builder 0.2.0` yet has `add_twpa_lines`). So we detect by introspection and tell the user — before building — what this env can/can't make. `generator/probe_capabilities.py` runs in the env (stdlib-only at import; a module-level `CATALOG` of `{id: locator}`, no QM imports at load) and `detect()` emits `{id: {available, detail}}` via `import + hasattr`; `run_build.py` imports `CATALOG_IDS` from it so detector and consumer can't drift. `core/capabilities.py` (SM side) owns `REGISTRY` (label/package/symbol/**produces**/**fix**/severity), `required_capabilities(spec)` (pure; context by *inclusion* — e.g. `pair.fixed_pair` only under `cz_fixed`), and `assess(spec, manifest) → {buildable, ok, blockers, warnings, inventory}`. `config_generator.probe_capabilities()` runs the deep probe for the *selected* env with a **version-keyed** cache (the older probe cache keys on interpreter mtime — stale after a `pip install`; not inherited). Three buckets: **blocker** (build would fail → hard-block), **degrade** (build succeeds, feature dropped/falls back → soft-block behind `ack_degrades`), available. `/generate/build` + `/regenerate/build` enforce server-side, independent of the stray-JSON `force` (one ack never collapses two gates). UI: report card in `enterReviewStep` + "Re-check environment". The `REGISTRY` id set is pinned equal to `CATALOG_IDS` by a test. Pulse-class locators are **multi-home** (`_PULSE_HOMES`: quam → quam_builder arch → quam_builder common — quam 0.6.0/quam_builder 0.4.0 moved SNZ/Erf/GaussianFiltered* out of `quam.components.pulses`); the same home list lives in `run_build._pulse_class` and `run_waveform_golden`, pinned in sync by `TestPulseHomesInSync`. See `docs/52_env_capabilities.md` + `docs/53_qop37_alignment.md`.

### Type Coercion Philosophy

`modifier.py` casts new values to the original field's type but **never validates ranges**. Real QUAM data has coupler amplitudes >1, negative T2, angles outside [-π, π] — trust researcher input.

## Key Files

| File | Purpose |
|------|---------|
| `quam_state_manager/core/safe_io.py` | Conflict-safe live-file I/O — share-delete reads, `ReplaceFileW` writes, mtime-bracketed pair read, atomic JSON writes |
| `quam_state_manager/core/working_copy.py` | Per-chip working copy; sync/apply between it and the live files |
| `quam_state_manager/core/pointer_resolver.py` | Core innovation — resolves QUAM JSON pointers; cache lives per `QuamStore` |
| `quam_state_manager/core/loader.py` | QuamStore: loads via safe_io, merges, validates state+wiring; owns the per-instance pointer cache |
| `quam_state_manager/core/scanner.py` | Workspace discovery (safe_io-routed), LRU cache |
| `quam_state_manager/core/search_index.py` | <1ms keystroke search (prefix map + trigram) |
| `quam_state_manager/core/query.py` | Flattens nested JSON into qubit/pair property dicts |
| `quam_state_manager/core/modifier.py` | Edits with undo + two-phase rollback; clears the store's pointer cache on mutate |
| `quam_state_manager/core/saver.py` | Atomic writes (delegates to safe_io), `.bak` rotation, CSV/MD export |
| `quam_state_manager/core/pulse_catalog.py` | Pulse-class registry (params/defaults/inferred-length math) — single source of truth for Pulses page + add-pulse forms |
| `quam_state_manager/core/pair_columns.py` | Live State Edit **pair grid** — derives editable columns from the chip's real pair leaves (gate macros, flux/coupler pulses, CR/ZZ), lab-flexible (no hardcoded gate/leaf names); drops all-null components, anchored pair-name strip, self-ref→read-only. Cells resolve through the same `_build_bulk_cell` pipeline as qubits; UI in the isolated `web/static/pair-edit.js` (qubit `bulk-edit.js` untouched) |
| `quam_state_manager/core/waveform_synth.py` | In-process numpy+scipy waveform synthesis (live preview; golden-pinned to the LabC env's quam) |
| `quam_state_manager/core/pulse_index.py` | Pulse enumeration + reverse-pointer (used_by) index + duplicate/rename pointer rewriting |
| `quam_state_manager/generator/run_waveform_golden.py` | LabC-env subprocess: dumps quam ground-truth waveforms → `tests/golden/waveform_golden.json` |
| `quam_state_manager/core/differ.py` | 2-way diffs, float tolerance, N-way trends |
| `quam_state_manager/core/history.py` | Param History: snapshots + SQLite trend index, chip fingerprints, alignment scan, migrations |
| `quam_state_manager/core/dataset.py` | DatasetStore: discovers + indexes experiment runs; per-file h5py lock |
| `quam_state_manager/core/ndview.py` | Data-tab N-D cube builder — DIMENSION_LIST axis truth, decimation, byte-budget, bytes LRU |
| `quam_state_manager/core/click_targets.py` | ndview click→write-target candidates (absolute-axis-gated, dim-name-bound) |
| `quam_state_manager/core/interactive_plots/contracts.py` | Click-contract math — patches-first pre-update anchors, RF-at-run, increment/assign/wrap semantics |
| `quam_state_manager/core/interactive_plots/registry.py` | Recipe dispatch (two-tier name matcher) + bundle-input LRU |
| `quam_state_manager/core/experiment_data.py` | Loads node.json + data.json, extracts metadata/fit results |
| `quam_state_manager/core/config_generator.py` | Generate-Config wizard: env discovery, probe, subprocess runner |
| `quam_state_manager/core/config_view.py` | Config Viewer: cached `generate_config()` preview + waveform plots |
| `quam_state_manager/core/regenerate.py` | Re-generate orchestration: reconstruct → subprocess build → value merge → recipe emit + exact-spec sidecar |
| `quam_state_manager/core/regen_spec.py` | Invert a chip → build spec (pinned wiring, populate via pointer chains); exact-spec sidecar read/write |
| `quam_state_manager/core/regen_merge.py` | Value-preserving merge (tier-1 carry / tier-2 graft), pair-id reconciliation, redundant-op prune, TWPA wiring+ports carry |
| `quam_state_manager/core/regen_script.py` | Emit the single-file editable Python build recipe (generate + populate combined) |
| `quam_state_manager/core/capabilities.py` | Env-capability model: `REGISTRY` + `required_capabilities(spec)` + `assess` (blocker/degrade/ok buckets) |
| `quam_state_manager/generator/probe_capabilities.py` | In-env detection catalog (`CATALOG`/`CATALOG_IDS`) + `detect()` → capability manifest |
| `quam_state_manager/generator/run_build.py` | Standalone QM-stack subprocess script (allocate / build modes) |
| `quam_state_manager/generator/run_generate_config.py` | Standalone QM-stack subprocess script (config preview) |
| `quam_state_manager/web/routes.py` | ~90 Flask/HTMX routes |
| `quam_state_manager/web/static/app.js` | ~5,100 lines of vanilla JS for UI polish |
| `quam_state_manager/web/static/generate.js` | ~3,000 lines for the Generate-Config wizard frontend |
| `quam_state_manager/cli.py` | 10 Typer + Rich CLI commands (`--version`, `--json` for scripting) |
| `quam_state_manager/main.py` | pywebview desktop launcher |
| `build/quam-manager.spec` | PyInstaller onedir bundle config |
| `docs/00_overview.md` | Architecture deep-dive — start here |
| `docs/28_conflict_safe_io.md` | Working-copy + armored I/O (read this before touching file paths) |
| `docs/32_red_team_phase_2.md` | Phase 2 red-team findings + which are fixed |
| `docs/53_qop37_alignment.md` | Modern-stack (quam 0.6.0 / quam_builder 0.4.0) ground truth: class moves, `_PULSE_HOMES`, padding_length rename, ParametricCZGate gating — read before touching pulse-class or build-script imports |

## Tests

Tests use `tmp_path` synthetic fixtures. Some tests additionally probe real data from `<data-root>\...` and auto-skip when that path is absent. No test config files — pytest uses default discovery. **1871 pass, 96 skip, 0 fail** on the `qm_mng` env (96 skipped tests are real-data tests gated by the ExampleChip folder's presence; the waveform-golden live-regeneration test additionally needs the Windows conda `LabC` env at `<qm-env>/python`).

## Dependencies

Core: Flask, Jinja2, Typer, Rich. Desktop: pywebview. Data: h5py (HDF5 reading), numpy + scipy (Pulses-page waveform synthesis — scipy gives bit-exact window/filter parity with qualang_tools). Frontend: HTMX, Pico CSS, Split.js, Plotly.js — all bundled in `web/static/` (no CDN). The Generate-Config wizard shells out to a user-selected conda env that has the heavy QM stack (`qualang_tools`, `quam_builder`, `quam`, `qm`) installed; the State Manager process itself never imports them.
