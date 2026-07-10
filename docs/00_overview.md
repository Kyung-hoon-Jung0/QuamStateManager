# QUAM State Manager -- Project Overview

> **Start here.** This document explains what the project is, why it exists, how it's structured, and how to run it. Then read the numbered docs (`01_pointer_resolver.md` through `12_desktop.md`) for module-level details.

## What problem this solves

Quantum Machines (QM) uses a configuration format called **QUAM** (Quantum Abstract Machine) to describe superconducting qubit hardware. Every qubit experiment produces a snapshot of the machine state as two JSON files:

- **`state.json`** (~5,000-6,000 lines for 17 qubits): contains all qubit parameters (frequencies, coherence times, gate amplitudes, readout settings, fidelity metrics), qubit pair parameters (CZ gate calibration, coupler offsets), and metadata.
- **`wiring.json`** (~60 lines): maps each qubit's XY drive, readout resonator, and flux line to physical hardware ports (FEM IDs, port numbers).

Researchers need to inspect, search, compare, and modify these files daily. The files are too large and deeply nested for manual `Ctrl+F` in an IDE. This tool provides:

1. **Instant search** -- type "qA1 readout" and see results in real-time (<1ms)
2. **Structured queries** -- "show me all qubits where T2 > 1 microsecond"
3. **Inline editing** -- change a value, undo it, save with automatic backup
4. **Diff & trends** -- compare two states or track how f_01 drifted across 50 experiments
5. **Workspace monitoring** -- watch a folder of experiment data, browse like VS Code's sidebar

## The QUAM pointer system

QUAM state files use a custom JSON pointer syntax to avoid duplicating values:

| Pointer type | Example | Meaning |
|-------------|---------|---------|
| **Absolute** `#/` | `"#/qubits/qA1/anharmonicity"` | Navigate from the JSON root to this path |
| **Relative-up** `#../` | `"#../x180_DragCosine/length"` | Go to parent dict, then navigate down |
| **Self-ref** `#./` | `"#./inferred_intermediate_frequency"` | Runtime-computed by QUAM (never resolved by us) |

For example, a qubit's `x90` pulse length is `"#../x180_DragCosine/length"` -- meaning "use the same length as the x180 pulse." When we display this value, we show both the pointer and its resolved concrete value (e.g. `40` nanoseconds).

This pointer system is central to the entire tool's design. Every module that reads values must handle pointers. Every module that writes values must preserve them (never resolve in-place).

## Data flow through the system

```
                                   ┌──────────────────────────────────┐
                                   │  quam_state folder               │
                                   │  ├── state.json  (qubit params)  │
                                   │  └── wiring.json (port mapping)  │
                                   └──────────┬───────────────────────┘
                                              │
                                              ▼
                              ┌────────────────────────────┐
                              │  loader.py (QuamStore)      │
                              │  Load + merge + validate    │
                              │  pointers via               │
                              │  pointer_resolver.py        │
                              └──────┬──────────────────────┘
                                     │
                    ┌────────────────┼───────────────────┐
                    │                │                    │
                    ▼                ▼                    ▼
          ┌──────────────┐  ┌──────────────┐    ┌──────────────┐
          │ search_index │  │  query.py    │    │  modifier.py │
          │ Real-time    │  │  get_qubit() │    │  set_value() │
          │ keystroke    │  │  get_pair()  │    │  batch_set() │
          │ search       │  │  topology()  │    │  undo()      │
          └──────────────┘  └──────────────┘    └──────┬───────┘
                                                       │
                                                       ▼
                                               ┌──────────────┐
                                               │  saver.py    │
                                               │  Atomic write │
                                               │  + backup     │
                                               └──────────────┘

          ┌──────────────┐                     ┌──────────────┐
          │ scanner.py   │                     │  differ.py   │
          │ Workspace:   │────load_store()────▶│  2-way diff  │
          │ scan folders │                     │  N-way trend │
          │ parse meta   │                     └──────────────┘
          └──────────────┘

          ┌──────────────┐  ┌──────────────┐
          │  cli.py      │  │  web/        │
          │  Typer CLI   │  │  Flask +     │
          │  10 commands  │  │  HTMX + API  │
          └──────────────┘  └──────────────┘
```

## Module inventory

(Line counts re-measured on `fix/redteam-merged-into-lf-fem`, May 2026.)

| # | Module | File | Lines | What it does |
|---|--------|------|-------|-------------|
| 1 | Pointer resolver | `core/pointer_resolver.py` | 178 | Resolves `#/`, `#../`, `#./` pointers; cache is *per-`QuamStore`* (passed in via `cache=` / `lock=` kwargs) so two chips with same-named qubits never share resolutions — see `32_red_team_phase_2.md` finding 0.1 |
| 2 | Loader | `core/loader.py` | 296 | Loads `state.json` + `wiring.json` into `QuamStore` via `safe_io.read_state_wiring`; owns the per-instance pointer cache + lock |
| 3 | Scanner | `core/scanner.py` | 432 | Discovers `quam_state/` folders, parses `node.json` via `safe_io.read_json`, LRU cache, mtime-based staleness |
| 4 | Search index | `core/search_index.py` | 530 | Bounded prefix map + trigram index for <1ms keystroke search |
| 5 | Query engine | `core/query.py` | 886 | Flattens nested JSON into researcher-friendly dicts (30+ properties per qubit), instrument wiring; routes pointer resolution through the store's per-instance cache |
| 6 | Modifier | `core/modifier.py` | 468 | Type-coerced edits, batch with two-phase rollback, undo, discard; clears the store's pointer cache on mutate |
| 7 | Saver | `core/saver.py` | 232 | Atomic writes (delegates to `safe_io.atomic_write_json`), timestamped `.bak` with rotation, CSV/Markdown export |
| 7a | Safe I/O | `core/safe_io.py` | 333 | Conflict-safe live-file access: `FILE_SHARE_DELETE` reads, `ReplaceFileW` writes, transient retry, **mtime-bracketed pair read** so an experiment that updates both files atomically can't hand us a torn snapshot — see `28_conflict_safe_io.md` |
| 7b | Working copy | `core/working_copy.py` | 287 | Per-chip working copy under `instance/working_state/`: create / load / live-changed / sync / apply-to-live (narrowed TOCTOU, loud post-write errors, meta written before in-memory advance) — see `28_conflict_safe_io.md` |
| 8 | Differ | `core/differ.py` | 334 | 2-way diff with float tolerance; N-way trend; experiment parameter/fit-result comparison |
| 9 | Experiment data | `core/experiment_data.py` | 112 | Loads `data.json` + `node.json` into `ExperimentContext` dataclass |
| 10 | Dataset store | `core/dataset.py` | 1,326 | `DatasetStore` + `RunInfo`: discovers experiment runs, HDF5 data, bookmarks, tags, notes; incremental rescan with per-folder mtime fingerprint; `changes_since` + `list_runs_compact` for the virtual-scroll table |
| 11 | History manager | `core/history.py` | 2,311 | Param History engine: snapshot capture (save/manual/auto/experiment) via `safe_io`, per-chip SQLite property index, content-hash dedup, fingerprint-aware multi-chip routing, alignment scan, chip-decisions persistence, v1+v2 migrations |
| 12 | Config generator | `core/config_generator.py` | 594 | Generate-Config wizard engine: env discovery, QM-stack probe, subprocess driver for `generator/run_build.py` and `generator/run_generate_config.py` |
| 13 | Config viewer | `core/config_view.py` | 330 | Cached preview of `machine.generate_config()` + per-pulse waveform plotting (Config Viewer; see `30_config_viewer.md`) |
| 14 | Generator scripts | `generator/run_build.py` + `generator/run_generate_config.py` | (standalone) | QM-stack subprocess scripts; never imported by the State Manager process |
| 15 | CLI | `cli.py` | 551 | 10 Typer + Rich commands (show, table, search, set, diff, scan, trend...) plus `--version` / `--json` for scripting |
| 16 | Web backend | `web/app.py` + `web/routes.py` | 4,000+ | Flask app factory (with `instance_path` override + tmp leftover purge) + ~90 HTMX/API routes spanning Param History, datasets, Generate Config wizard, Config Viewer, gate ops, and state sync/apply |
| 17 | Web frontend | `web/static/app.js` | 5,092 | 50+ public functions: dataset plots, instrument wiring SVG, sticky state, tag picker, theme toggle, sparkline renderer, drawer chart, recent-paths dropdown, decision banner, chip-swap banner, Ctrl+K command palette; delegates dataset list filter/sort to `dataset-virtual.js` |
| 17a | Generate wizard frontend | `web/static/generate.js` | 3,003 | Hardware/qubit/wiring/Populate wizard: chassis nav, drag-editable wiring diagram, LO auto-assign, shared-LO group viz, LF-FEM delay chip strip, draft persistence |
| 17b | Dataset virtual scroller | `web/static/dataset-virtual.js` | 540 | Viewport-windowed renderer, in-memory filter/sort, idle-deferred delta-poll merge, sticky selection set |
| 18 | Web styles | `web/static/style.css` | ~3,400 | ~430 CSS custom properties (fully parameterized color tokens), light + dark theme, colorblind palette, sparkline grid, alignment banner (4 states), recents dropdown, archive section, `.datasets-scroll` (`contain: strict`) + 32 px fixed-height rows |

**Total**: ~17,000 lines of application code, ~10,000 lines of tests (~880 tests across 19 test files, 96 skip on machines without the ExampleChip data folder).

## Test suite

```bash
cd <project-root>
# WSL: activate the qm_mng conda env first
conda run -n qm_mng python -m pytest tests/ -q
```

Tests run against both **synthetic fixtures** (temporary JSON files in `tmp_path`) and **real data** from the ExampleChip repository:
- 3-qubit config: `<data-root>\...\quam_state\`
- 17-qubit config: `<data-root>\...\quam_state_ExampleChip_variant-B\`
- 53 experiment snapshots: `<data-root>\...\data\project_name\2026-02-19\`

Real-data tests are auto-skipped if those folders aren't present. Current run: **940 pass, 96 skip, 0 fail.**

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | 3.1.3 | Web backend |
| Jinja2 | 3.1.6 | HTML templates (installed with Flask) |
| Typer | 0.24.0 | CLI framework |
| Rich | (bundled with Typer) | Pretty terminal tables |
| h5py | any | HDF5 dataset reading (experiment raw/fit data) |
| pywebview | any | Native desktop window (wraps Flask in a webview) |
| Plotly | (bundled JS) | Topology graph, trend charts, HDF5 data plots (4.4 MB `plotly.min.js` in static/) |
| HTMX | (bundled JS) | HTML-over-the-wire interactivity (50 KB `htmx.min.js` in static/) |
| Pico CSS | (bundled CSS) | Classless CSS framework (82 KB `pico.min.css` in static/) |
| Split.js | (bundled JS) | Vertical resizable split panes (6.7 KB `split.min.js` in static/) |
| pytest | 9.0.2 | Testing |

**Not used (by design)**: pandas, numpy, streamlit. Keeping dependencies minimal for fast startup and small PyInstaller bundle.

## Key design decisions

1. **In-memory Python dicts** -- QUAM state files are <1MB even for 17 qubits. Loading them as dicts and indexing in memory is far simpler and faster than a database. Scales to 500+ qubits.

2. **Pointers are never resolved in-place** -- the store always holds raw pointer strings. Resolution happens on-demand. This means `json.dump` preserves pointer semantics when saving.

3. **Type coercion, not validation** -- when a researcher edits a value, we cast to the original type (float stays float) but don't enforce ranges. Real data has coupler amplitudes > 1, negative T2 from fits, angles outside [-pi, pi].

4. **HTMX over SPA** -- the web UI uses HTMX for interactivity (HTML fragments swapped via AJAX) instead of React/Vue. ~3,900 lines of vanilla JS for UX polish (instrument SVG, HDF5 plots, sticky state, tag picker, dark mode) on top of the HTMX core.

5. **Atomic saves** -- `json.dump` to `.tmp`, `os.fsync`, then `os.replace`. Crash between the two file writes leaves one file safe and the other recoverable from `.tmp`.

6. **Lazy loading with LRU** -- the workspace scanner discovers hundreds of experiment folders but only loads `QuamStore` when clicked (max 10 cached, ~40MB).

## Folder structure

```
<project-root>\
├── quam_state_manager/
│   ├── __init__.py                 # Package version
│   ├── __main__.py                 # `python -m quam_state_manager` desktop entry
│   ├── main.py                     # pywebview desktop launcher
│   ├── cli.py                      # Typer CLI (10 commands, --version, --json)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── safe_io.py              # FILE_SHARE_DELETE reads, ReplaceFileW writes, mtime-bracketed pair read
│   │   ├── working_copy.py         # Per-chip working copy (create / load / sync / apply-to-live)
│   │   ├── pointer_resolver.py     # #/ #../ #./ resolution (cache lives on each QuamStore)
│   │   ├── loader.py               # QuamStore — load via safe_io, merge, validate; owns the pointer cache
│   │   ├── scanner.py              # Workspace (safe_io-routed scan, parse, LRU cache)
│   │   ├── search_index.py         # Prefix map + trigram index
│   │   ├── query.py                # QueryEngine (get_qubit, topology, etc.)
│   │   ├── modifier.py             # Modifier (set, batch, two-phase rollback, undo)
│   │   ├── saver.py                # Saver — atomic save (delegates to safe_io), .bak rotation, export
│   │   ├── differ.py               # Differ (2-way diff, N-way trend, experiment compare)
│   │   ├── history.py              # HistoryManager — snapshots + SQLite Param History index
│   │   ├── experiment_data.py      # ExperimentContext (data.json + node.json)
│   │   ├── dataset.py              # DatasetStore + RunInfo (run discovery, HDF5, bookmarks, tags)
│   │   ├── config_generator.py    # Generate-Config wizard engine (env discovery, subprocess driver)
│   │   └── config_view.py          # Config Viewer (cached generate_config() preview + waveform plots)
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── run_build.py            # Standalone QM-stack script (allocate / build modes)
│   │   └── run_generate_config.py # Standalone QM-stack script (config preview)
│   └── web/
│       ├── __init__.py
│       ├── app.py                  # Flask app factory (~180 lines, instance_path override, tmp purge)
│       ├── routes.py               # ~90 routes (~3,800 lines)
│       ├── templates/              # 63 Jinja2 templates (17 full pages + 46 partials)
│       │   ├── base.html / qubits.html / qubit_detail.html / pairs.html / pair_detail.html
│       │   ├── table.html / wiring.html / instrument_wiring.html / compare.html / diff.html
│       │   ├── trends.html / datasets.html / dataset_detail.html / explorer.html
│       │   ├── generate.html / config.html / param_history.html
│       │   ├── _pending_tray.html / _state_review.html / _state_apply_conflict.html
│       │   └── _*.html             # Partials for HTMX swaps (one per inspector section)
│       └── static/
│           ├── app.js              # Main JS (~5,100 lines, Ctrl+K palette, dataset plots, etc.)
│           ├── generate.js         # Wizard frontend (~3,000 lines, wiring diagram, LO viz)
│           ├── dataset-virtual.js  # Virtual-scroll dataset table (~540 lines)
│           ├── style.css           # ~3,400 lines, ~430 CSS custom properties
│           ├── pico.min.css        # Pico CSS framework
│           ├── htmx.min.js         # HTMX 2.0.4
│           ├── split.min.js        # Split.js resizable panes
│           └── plotly.min.js       # Plotly.js 2.35.2
├── tests/
│   ├── test_pointer_resolver.py    # Includes per-store cache isolation regression
│   ├── test_loader.py              # Includes safe_io-routing + chip-isolated cache tests
│   ├── test_scanner.py             # Includes safe_io.read_json routing test
│   ├── test_search_index.py
│   ├── test_query.py
│   ├── test_modifier.py
│   ├── test_saver.py
│   ├── test_safe_io.py             # Share-delete reads, ReplaceFileW writes, retry, pair-read
│   ├── test_working_copy.py        # apply_to_live TOCTOU + meta-ordering tests
│   ├── test_differ.py
│   ├── test_experiment_data.py
│   ├── test_history.py
│   ├── test_run_build_delay.py     # LF-FEM delay generator helpers
│   ├── test_config_generator.py
│   ├── test_config_view.py
│   ├── test_cli.py
│   ├── test_web.py                 # Includes LRU eviction working-folder preservation
│   ├── test_main.py
│   └── test_integration.py
│                                   # Total: ~880 tests across 19 files
└── docs/
    ├── 00_overview.md              # This file
    ├── 01-09 ...                   # Module-level deep-dives (pointer resolver → CLI)
    ├── 10-26 ...                   # Web UI, datasets, multi-chip, perf, virtual scroll
    ├── 27_config_generator.md      # Generate-Config wizard
    ├── 28_conflict_safe_io.md      # Working copy + armored I/O (start here for file I/O changes)
    ├── 29_gate_operations.md       # CZ creation + pulse creation
    ├── 30_config_viewer.md         # Config Viewer subprocess + waveform plots
    ├── 31_lf_fem_delay.md          # Auto-set LF-FEM delay from MW-FEM band
    ├── 32_red_team_phase_2.md      # Phase 2 audit + Critical fixes shipped
    └── 33_dev_setup.md             # Conda env, pip install -e, pre-commit
```

## Build status

| # | Module | Status |
|---|--------|--------|
| 1-10 | Core + CLI + Web backend | **Done** |
| 11 | Template polish | **Done** -- pointer-aware editing, color-coded cells, category tabs, auto-toast |
| 12 | `main.py` | **Done** -- pywebview desktop launcher, random port, server health check |
| 13 | PyInstaller `.exe` | **Done** -- `onedir` build, bundled static assets, `.gitignore` |
| 14 | Integration tests | **Done** -- 22 end-to-end tests, package rename to `quam_state_manager` |
| 15 | Web UI redesign | **Done** -- HTMX stable containers, Split panes, save flow, inspector, fonts |
| 16 | Compare/Trend Tracker | **Done** -- tabbed diff, reference selector, delta annotations, Plotly charts |
| 17 | Folder Browser + Autocomplete | **Done** -- Browse dialog, path autocomplete, recent folders, deep detection |
| 18 | JSON Tree Viewer + Explorer | **Done** -- interactive tree, search, diff mode, `/explorer` route |
| 19 | Full Compare tab | **Done** -- unified tree, Diff Only, Ref Data with delta annotations |
| 20 | Pending Changes Tray | **Done** -- amber docked tray replaces floating `#changes-panel`; OOB HTMX updates on edit/save/discard |
| 21 | Dataset Explorer | **Done** -- run discovery, HDF5 data viewer, experiment metadata, figure gallery |
| 22 | Bookmarks + Tags + Notes | **Done** -- star bookmarks, tag picker, inline notes, sidebar panel |
| 23 | Multi-Select Compare + Trends | **Done** -- checkbox compare 2-5 runs, trend dashboard with Plotly charts |
| 24 | Sticky State + Pin & Browse | **Done** -- tab/figure/plot state persists across navigation, pin+browse split |
| 25 | Multi-Select HDF5 Plots | **Done** -- toggle-select multiple vars + qubits, stacked plots, graceful fallback |
| 26 | Auto-Refresh + Sidebar Integration | **Done** -- configurable poll interval, sidebar click loads dataset detail |
| 27 | Scalability (100+ qubits) | **Done** -- pagination with "All" option, lazy JSON tree, LRU cache |
| 28 | Chip Topology Dashboard | **Done** -- rich scrollable dashboard replacing Plotly scatter: summary cards, HTML/SVG topology with qubit property cards (primary always visible, secondary collapsible), heatmap grid, histograms, enriched pair summary (gate fidelities + confusion matrix off-diagonals), 15 per-metric detail panels |
| 29 | Live File-Change Detection | **Done** -- 3s mtime polling, amber "file changed" banner, reload with diff overlay (green/red/amber highlights + delta badges, fading after 5s) |
| 30 | Sidebar Reorder | **Done** -- Bookmarks → Nav tabs → Load → Workspace (researchers access bookmarks and navigation first) |
| 31 | Dark Mode + Parameterized Colors | **Done** -- all ~100 hardcoded hex colors extracted into CSS custom properties, `[data-theme="dark"]` overrides, toggle in settings, localStorage persistence, Plotly theme-awareness |
| 32 | Sidebar Sticky State | **Done** -- preserves `<details>` open/closed state and scroll position across auto-refresh polls via beforeSwap/afterSwap handlers |
| 33 | Figures Tab Enhancement | **Done** -- parameters (node.json) + fit results (data.json) shown above figures in 2-column layout; figures in 2-column grid; all values displayed (no truncation) |
| 34 | Topology Grid Y-Axis Fix | **Done** -- QUAM `grid_location` row 0 = bottom of chip; rendering now flips Y-axis so row 0 appears at bottom of screen |
| 35 | Colorblind Mode + Dark Compat | **Done** -- colorblind-safe palette with dark+colorblind combined overrides |
| 36 | Plot Click-to-Edit + 2Q RB Panels | **Done** -- click any Plotly point to auto-update `state.json` (`/field/edit`); dedicated 2Q StandardRB / InterleavedRB visualisation panels on Chip Status |
| 37 | Param History | **Done** -- new top-level tab. Tracks every modification of state.json across saves / manual edits / external edits / experiment runs. Per-chip SQLite property index, content-hash dedup, sparkline grid (1 SVG cell per qubit×prop), Plotly drawer with hover (trigger + run_id + experiment) + click-to-dataset, LTTB downsample for 100k snapshots. See `20_param_history.md`. |
| 38 | Multi-Chip Identity & Alignment | **Done** -- `ChipFingerprint` (network host + cluster + qubits + pairs); `align()` with aligned/renamed/different_chip/unknown outcomes; chip-stable `_key_for` so per-experiment loads consolidate. See `21_multi_chip_support.md`. |
| 39 | Multi-Chip UI (Selector + Alignment Banner) | **Done** -- chip selector with active (loaded) + collapsible archive (disk-only) sections; 4-state colored alignment banner (green/orange/yellow/red + info); cross-chip switch links default to `since=all`. |
| 40 | Live Chip-Swap Auto-Routing | **Done** -- `_resolve_snapshot_dir` routes a new snapshot to a different chip dir when the loaded path's content fingerprint diverges from the existing dir's; banner notifies user via `last_chip_swap` config + dismissable UI. |
| 41 | Backfill Ambiguity Prompts | **Done** -- workspace experiments grouped by `data_folder`; same-network-different-folder cases surface a "같은 chip / 다른 chip" prompt; decisions persist in `instance/chip_decisions.json` and silence the prompt on next backfill. |
| 42 | Different-Chip Auto-Routing in Backfill | **Done** -- `different_chip` workspace entries (was: silent skip) auto-route to their native chip dir via `_ingest_entries_into`; cumulative progress reporting across all chip groups. |
| 43 | Two Migrations (v1 path + v2 fingerprint) | **Done** -- v1 consolidates legacy per-experiment-keyed dirs into chip-named ones; v2 corrects v1's mis-routing by routing snapshots based on actual `state.json+wiring.json` content fingerprint, not the buggy `meta.source_path`. Both gated by their own flag files. |
| 44 | Session Persistence | **Done** -- `instance/last_session.json` remembers `last_quam_state_path` + LRU recents (cap 10) + `workspace_excluded`; auto-restore on first request via `@before_request` hook so the dashboard opens already loaded. Recents dropdown next to the Load button for 1-click project switch. See `22_session_persistence_and_workspace_auto_populate.md`. |
| 45 | Workspace Auto-Populate | **Done** -- `_maybe_auto_add_workspace_root` adds a per-experiment load's chip folder to the workspace tree; `_rehydrate_workspace_from_recents` walks recent paths on startup and re-adds their chip folders; respects `workspace_excluded`. |
| 46 | Test Fixture Isolation | **Done** -- `create_app(testing=True)` without explicit `instance_path` auto-allocates an OS tmp dir; `_purge_test_leftovers` strips `pytest-NN/` history dirs and tmpdir-prefixed paths from `workspace_roots.json` / `last_session.json` on every startup. |
| 47 | Dark Theme as Default | **Done** -- new browser profiles get dark by default; `data-theme="dark"` set inline in `<head>` to prevent FOUC; explicit user choice in localStorage takes precedence. |
| 48 | Param History Performance — Phase 1 | **Done** -- caching pass on `core/history.py`: alignment scan + fingerprint memoization (kills the 2.5 s workspace fingerprint loop), `list_chip_histories` + `index_summary` cached on a chip-dir version counter, `_ensure_index_fresh` cheap-path shortcut, `_open_index` race-safe one-time schema init, SQLite `cache_size`/`mmap_size`/`temp_store` pragmas, `MAX(timestamp)` instead of reverse-scan for "latest snapshot". Frontend D1+D2: server-side SVG rendering kills the 1.5 s `JSON.parse + innerHTML` loop in the browser. Bug fixes: race in `_open_index`, missing cache-invalidation on `rebuild_index` self-heal, fragile path comparison in `_ingest_entries_into`. See `23_param_history_performance.md`. |
| 49 | "QUAM STATE MANAGER" Slow-Route Loader | **Done** -- per-letter gradient-sweep CSS animation that appears 200 ms into any HTMX request to `/param-history*`. Pure CSS keyframes, Pico-token-themed, `prefers-reduced-motion` fallback. Replaces the "page froze" feeling on cold-cache visits. See `24_param_history_ux_polish.md`. |
| 50 | Param History First-Visit CTA + Auto-Incremental Backfill | **Done** -- empty-state shows a centered "Import N experiments from workspace" card when alignment scan found ingestible workspace data but the SQLite index is still empty. Backfill progress mirrored into the loader as `Importing… 234 / 2070`. After first import, a `localStorage["quam_imported_<chip>"]` flag enables silent auto-incremental backfill on revisit when the workspace gains 5+ new aligned experiments. See `24_param_history_ux_polish.md`. |
| 51 | Dataset Table Virtual Scroll | **Done** -- server ships rows as compact JSON in `<script id="ds-rows-data">`; `web/static/dataset-virtual.js` (540 lines) renders only the rows in the viewport (`OVERSCAN = 10`, fixed 32 px height, top/bottom spacer `<tr>`s). In-memory filter (cached `row._s`) + sort over 2 000 rows in ~5 ms; one delegated `click`/`change` handler on `<tbody>`. Selection set survives scroll/filter/sort. CSS `contain: strict` on the scroll container. See `25_dataset_virtual_scroll.md`. |
| 52 | Dataset Delta Poll | **Done** -- new `GET /datasets/changes-since?ts=…` returns only `updated[]` + `vanished[]` since the last poll instead of refetching the whole table. Backed by `RunInfo.last_parsed` per-row timestamps + a 30-min `_vanished` log on `DatasetStore`. Client merges deltas via `applyDelta()`; if the user interacted in the last 5 s the response is buffered in `pendingDelta` and replayed when idle, so the row under their finger never moves. Plain `fetch()` so the slow-route loader doesn't flash. See `25_dataset_virtual_scroll.md`. |
| 53 | Incremental Dataset Rescan | **Done** -- `DatasetStore._scan` keeps a per-folder mtime fingerprint (`folder_mt`, `node.json_mt`, `data.json_mt`, `run_id`) and re-parses only folders whose fingerprint moved. Unchanged 2 000-run rescan went from ~3 s to <50 ms. Vanished folders drop into `_vanished` for the delta-poll endpoint to surface. See `25_dataset_virtual_scroll.md`. |
| 54 | DatasetStore LRU Cache | **Done** -- `_dataset_store_lru()` keeps up to 5 `DatasetStore` instances under the active-pointer slot in `web/routes.py`, so workspace toggles or rescans no longer trigger a full cold scan on the next request — they reuse the cached store and run only the incremental rescan path (feature 53). See `25_dataset_virtual_scroll.md`. |
| 55 | Auto-Backfill Threshold Fix | **Done** -- template exposes `summary.by_trigger['experiment']` on `#param-history-root` as `data-experiment-snapshot-count`; `paramHistoryMaybeAutoBackfill()` compares experiment-only counts instead of all-snapshots total so active hand-editors no longer silently suppress auto-incremental backfill (doc 24 had flagged this false-negative). Strictly more eager; chips with no manual editing unaffected. See `24_param_history_ux_polish.md`. |
| 56 | Topbar Import-Status Pill | **Done** -- pill next to the pending tray polls `/param-history/backfill/status` at asymmetric cadence (30 s idle / 1 s running / on-demand wake for ~200 ms first-update latency). Survives page navigation so a long Param-History backfill stays visible from any tab. Shows `Importing X / Y (P%)` with reduced-motion-aware spinner; flashes `Import done (N)` / `Import failed` for 4 s on terminal states, gated by a visibility-guard so a stale finished job in `_backfill_state` doesn't produce a phantom flash on first load. Pure CSS + ~95 lines JS; no new backend. See `24_param_history_ux_polish.md`. |
| 57 | Generate Config Wizard | **Done** -- new "Generate Config" tab: an 8-step wizard (environment → network → chassis → qubits/pairs → wiring → populate → output → review) that builds a QUAM `state.json` + `wiring.json` from scratch. The app runs QM's `qualang_tools.wirer` + `quam_builder` in a user-selected conda env via a subprocess (`generator/run_build.py`); auto-allocates channels with per-line manual override; full populate of resonator / qubit / flux / 1Q-gate-pulse / CZ parameters. See `27_config_generator.md`. |
| 58 | Generate Config UX polish (20+ phases) | **Done** -- multiplex readout feedlines, auto-fill pairs, drag-editable wiring diagram, structural validation, RF/IF unit toggle, LO auto-assignment, shared-LO group viz, docked wiring monitor, keyboard chassis nav, draft persistence, "Set all" bulk-fill, ring LO/band-conflict ports, amp/power/voltage audit, etc. All on `feat/generate-config-ux`. |
| 59 | Gate Operations (CZ + pulse creation) | **Done** -- CZ gate creation from pair detail; Square / DragCosine pulse creation from qubit detail; parametric CZ Phase 3a wired end-to-end. See `29_gate_operations.md`. |
| 60 | Config Viewer | **Done** -- per-pulse waveform plots on detail pages, per-qubit/pair config slice in inspector, top-level `/config` browser. Subprocess to the chosen QM-capable env runs `generator/run_generate_config.py`. See `30_config_viewer.md`. |
| 61 | LF-FEM Delay Auto-set | **Done** -- generator writes `delay` on every LF-FEM analog output from the paired qubit's MW-FEM band (141/161/141 ns for bands 1/2/3); per-port editable post-generation from qubit / pair detail page. See `31_lf_fem_delay.md`. |
| 62 | Red-team Phase 1 (data safety) | **Done** -- thread-safe pointer cache (lock), `.bak` rotation (default 20), two-phase rollback (state restored before log mutated), no silent diff/history truncation (paginate-or-show-all instead of slicing to 200). |
| 63 | Power-User Pack | **Done** -- Ctrl+K command palette, CLI `--version` + `--json` output, package `__version__`. |
| 64 | A11y Pass | **Done** -- Escape cancels inline edits, glyphs on fidelity badges, aria-labels on interactive surfaces. |
| 65 | Hygiene + CI | **Done** -- ruff + pre-commit config, dev setup doc (`33_dev_setup.md`), CI workflow, unused-import cleanup. |
| 66 | Conflict-Safe I/O Hardening (Phase 1 follow-up) | **Done** -- `safe_io.read_state_wiring` does a mtime-bracketed pair read so a writer landing between the two reads can't hand us a torn snapshot; `apply_to_live` narrows the TOCTOU window with a pre-write re-stat; post-write mtime read + meta persist failures are loud (raise `LiveFileError`, in-memory `synced_*` not advanced). See `28_conflict_safe_io.md`. |
| 67 | Red-team Phase 2 (chip isolation + universal safe_io routing) | **Done** -- pointer cache moved off the module and onto each `QuamStore` (per-instance dict + lock; two chips with same-named qubits never share resolutions); `loader.py` + `scanner.py` route their `state.json` / `wiring.json` / `node.json` reads through `safe_io` so workspace navigation can't break a still-active experiment's save; `Saver._atomic_write` unified onto `safe_io.atomic_write_json`; `apply_to_live` writes meta before advancing in-memory mtimes; `_activate_quam` LRU eviction preserves the on-disk working folder so unapplied Saves survive a chip swap. See `32_red_team_phase_2.md`. |
| 68 | Red-team Phase 2.1 (remaining 15 findings) | **Done** -- DatasetStore tag/bookmark/note thread-safety with rollback on disk failure; `_data_json_cache` LRU-bounded at 200; `save_chip_decision` atomic + module-locked + propagates `OSError`; `_canonical_content_hash` / `_ingest_entries_into` / `_parse_run_folder` / `experiment_data` all route through `safe_io`; `Path.is_relative_to` for figure-path containment; `probe_envs` parallelised via `ThreadPoolExecutor` + persistent mtime-keyed cache; `Differ.diff` accepts in-memory `(state, wiring)` tuples (state_review skips the tmp dance); `_save_session_raising` propagates errors to the UI; migration flags + workspace_roots write through `safe_io.atomic_write_json`; `_sanitize_fit` surfaces `./...` refs as `None` with a debug log instead of silently dropping them. 24 new regression tests. See `32_red_team_phase_2.md` Resolution log. |
| 69 | Migration v2 — O(N) fingerprint index | **Done** -- `_resolve_chip_key_by_fingerprint` (per-snap iterdir + first-match `break`) replaced with `_build_fingerprint_index(history_root)` + per-snap O(1) lookup. Two-pass builder uses a *purity ratio* tie-breaker so a clean `ExampleChip_21Q` (1/1=1.0) outranks a polluted `ExampleChip_1Q` (1/2=0.5) for the LabB fingerprint — fixing the routing the old code got backwards. Wall-clock drops from O(N×M×S) ≈ 10⁸ fingerprint reads on a 10 000-snapshot workspace to O(N) ≈ 2N reads (pinned by `test_v2_index_built_once_perf`). The audit doc's last open caveat closes. See `docs/21_multi_chip_support.md`. |
| 70 | Red-team Phase 3 — scaling speed (10⁴ experiments / 50 qubits) | **Done** — 10 of 11 findings shipped on `fix/redteam-phase-3-speed`. SQL-side downsampling in `extract_property_history` (CTE with `ROW_NUMBER OVER PARTITION` caps the row pull at `downsample × 10`); backfill runs through one SQLite connection with `BEGIN`/`COMMIT` every 500 entries; new `_extract_index_rows_from_state` walks the raw qubit dict directly instead of building a `QuamStore` per snap; Workspace + DatasetStore cold scans split into single-threaded discovery + parallel `ThreadPoolExecutor` parse; `_hashes.json` sidecar lets a fresh session skip the meta.json walk; per-entry alignment cache survives a one-experiment workspace mtime bump; `extract_property_history` results cached per `_chip_dir_version`; `RunInfo.key_metric` pre-computed at parse time; backfill progress callbacks throttled to every 100 entries / 200 ms. §3.1 (lazy `/datasets` payload) deferred — desktop deployments don't need the memory savings. 11 new regression tests. See `docs/34_red_team_phase_3.md` Resolution log. |
| 71 | Red-team Phase 4 — security + concurrency | **Done** — 4 of 4 substantive findings shipped on `fix/redteam-phase-4-security`. New `script_json` Jinja filter escapes `<` / `>` / `&` to `\u00XX` JSON-string escapes so a researcher-shared `state.json` with `</script>` in any string can't break out of the script context; 10 templates migrated. Per-folder build lock + cache lock around `_quam_cache` + active-context publication: two threads loading the same folder serialise on the working-folder atomic write but different folders still build in parallel. `before_request` CSRF origin check rejects cross-origin mutations; `after_request` adds CSP / `X-Content-Type-Options` / `Referrer-Policy`. `_H5_WHICH_WHITELIST = {"ds_raw", "ds_fit"}` short-circuits path-traversal-shaped `?which=` queries. 12 new regression tests. §5 Low hygiene deferred (Werkzeug dev server, HDF5 size cap, etc.). See `docs/35_red_team_phase_4.md` Resolution log. |
| 72 | Red-team Phase 5 — web runtime + concurrency | **Done** — 9 of 10 findings shipped on `fix/redteam-phase-5-runtime` (§5.1 CSP nonce deferred). Dataset `pollForNewRuns` rewritten as chained `setTimeout` with `document.visibilityState` gate + exponential backoff + connection-lost toast + `AbortController` 10s timeout — backgrounded tabs stop polling, hung subprocesses don't freeze the UI. `_ensure_workspace_loaded` wrapped in `_startup_lock` so concurrent first-requests don't duplicate workspace roots. `_add_security_headers` extended to set `Cache-Control: no-store` on HTMX partials. `_dataset_lru_lock` makes the LRU dict's first-set race-free. Scanner `os.walk(followlinks=False)` + realpath containment guard rejects symlinks/junctions escaping the workspace root. `safeLSSet` / `safeLSGet` localStorage helpers added for future code. 7 new regression tests. §3.2 (F5 idempotency) verified covered by Phase 4 §2's per-folder build lock. See `docs/37_red_team_phase_5.md` Resolution log. |

**940 tests pass, 96 skip, 0 fail.**

## Quick start for a new developer

```bash
# 1. Clone and enter the project
cd <project-root>

# 2. Run the test suite (no setup needed beyond Python 3.10+)
pip install flask typer rich pytest
python -m pytest tests/ -v

# 3. Try the CLI against real data
python -m quam_state_manager.cli show qA1 -f "path\to\quam_state_folder"
python -m quam_state_manager.cli search "T2" -f "path\to\quam_state_folder"
python -m quam_state_manager.cli scan "path\to\data\project_name"

# 4. Start the web server (dev mode)
python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5000)"
# Then open http://localhost:5000
```

## Reading order for the docs

1. **This file** (`00_overview.md`) -- you're here
2. `01_pointer_resolver.md` -- understand the pointer syntax first; everything depends on it
3. `02_loader.md` -- how JSON files become a `QuamStore`
4. `03_scanner.md` -- how experiment folders are discovered (skip if not working on workspace features)
5. `04_search_index.md` -- how real-time search works (skip if not working on search)
6. `05_query.md` -- how nested JSON becomes flat qubit dicts (important for UI work)
7. `06_modifier.md` -- how edits work (important for edit features)
8. `07_saver.md` -- how saves work (straightforward)
9. `08_differ.md` -- how diffs and trends work
10. `09_cli.md` -- CLI commands reference
11. `10_web.md` -- Flask routes and template architecture
12. `15_web_redesign_progress.md` -- full log of the UI redesign (HTMX layout, split panes, save flow, inspector, etc.)
13. `16_advanced_ui_features.md` -- advanced features (JSON tree viewer, Compare tabs, Trend Tracker, folder browser)
14. `17_dataset_ux_enhancements.md` -- dataset features (multi-run compare, trends, bookmarks, tags, HDF5 plots, sticky state)
15. `18_dark_mode_and_recent_features.md` -- dark mode, fully parameterized color tokens, sidebar sticky state, figures tab, topology grid fix
16. `19_plot_click_to_edit_and_2q_rb.md` -- Plotly click-to-edit + 2Q Randomized Benchmarking panels on the Chip Status dashboard
17. `20_param_history.md` -- Param History sparkline trend dashboard (SQLite property index, content-hash dedup, hover/click drill-down)
18. `21_multi_chip_support.md` -- multi-chip identity (ChipFingerprint, align(), chip-stable keying), chip selector UI, alignment scan banner, live chip-swap auto-routing, backfill ambiguity prompts, v1+v2 migrations
19. `22_session_persistence_and_workspace_auto_populate.md` -- last_session.json + workspace auto-populate, recents dropdown, test fixture isolation
20. `23_param_history_performance.md` -- Param History performance diagnosis (where the 2-4 s lag comes from), all options considered with trade-offs, phased plan to scale to 10 k+ snapshots
21. `24_param_history_ux_polish.md` -- "QUAM STATE MANAGER" slow-route loader, first-visit CTA card, auto-incremental backfill on revisit (the UX layer on top of doc 23's perf work)
22. `25_dataset_virtual_scroll.md` -- Datasets table: compact JSON payload + client-side virtual scroller + delta-poll endpoint + incremental rescan + DatasetStore LRU. The same perf playbook applied to the dataset list.
23. `26_browse_test_isolation_fix.md` -- audit (2026-05-16) fix: `test_browse_parent_of_quam_state` flaked in full-suite runs because it browsed the shared pytest tmp dir past the `/browse` `[:50]` cap; now uses an isolated tmp subtree.
24. `27_config_generator.md` -- the Generate Config wizard: build a fresh QUAM `state.json` + `wiring.json` from a visual hardware/qubit/wiring/populate spec, via a subprocess to a user-selected QM-capable conda env.
25. `28_conflict_safe_io.md` -- conflict-safe live-file access: the State Manager edits a private working copy and touches the live `state.json` / `wiring.json` only on an explicit sync / apply, so it never breaks an experiment program's save. Updated for Phase 1 follow-up (mtime-bracketed pair read, narrowed `apply_to_live` TOCTOU, loud post-write errors) and Phase 2 (universal `safe_io` routing, LRU eviction preserves working folder).
26. `29_gate_operations.md` -- CZ gate creation + parametric CZ Phase 3a + Square / DragCosine pulse creation from detail pages.
27. `30_config_viewer.md` -- offline preview of `machine.generate_config()` + per-pulse waveform plots; subprocess pattern shared with the Generate-Config wizard.
28. `31_lf_fem_delay.md` -- auto-set LF-FEM `delay` from the paired MW-FEM band (141/161/141 ns); editable per-port from detail pages.
29. `32_red_team_phase_2.md` -- Phase 2 audit (16 findings) + Resolution log showing every finding shipped across `fix/redteam-merged-into-lf-fem`, `fix/redteam-phase-2-1`, and `fix/migration-v2-index`.
30. `33_dev_setup.md` -- conda env + editable install + pre-commit + dev-mode `flask run` instructions.
31. `34_red_team_phase_3.md` -- Phase 3 audit (scaling at 10⁴ experiments / 50 qubits) + Resolution log showing 10 of 11 findings shipped on `fix/redteam-phase-3-speed`; §3.1 deferred for memory-vs-speed tradeoff.
32. `35_red_team_phase_4.md` -- Phase 4 audit (security + concurrency) + Resolution log showing all 4 substantive findings shipped on `fix/redteam-phase-4-security` (XSS filter, per-folder build lock, CSRF origin check + CSP, HDF5 whitelist). §5 hygiene deferred.
33. `37_red_team_phase_5.md` -- Phase 5 audit (web runtime + concurrency at the long-running session level) + Resolution log showing 9 of 10 findings shipped on `fix/redteam-phase-5-runtime` (visibility-gated polling, startup lock, no-store on HTMX partials, scanner symlink safety, etc.). §5.1 CSP nonce deferred.
