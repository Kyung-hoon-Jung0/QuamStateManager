# Web Backend -- `web/app.py` + `web/routes.py`

## What was built

A complete Flask web backend with **60 routes** (HTML pages, HTMX fragment endpoints, JSON API endpoints, dataset/bookmark/trend endpoints), 46 Jinja2 templates, ~3,900 lines of vanilla JS for interactive UX, and a `sys._MEIPASS`-aware app factory for PyInstaller bundling.

## Architecture

```
web/
├── app.py                          # Flask app factory (63 lines)
├── routes.py                       # Blueprint with 60 routes (2,219 lines)
├── __init__.py
├── templates/                      # 46 Jinja2 templates (14 full pages + 32 partials)
│   ├── base.html                   # Main layout: topbar, sidebar, split panes, folder browser dialog
│   ├── _status.html                # Status toast fragment
│   ├── _sidebar_tree.html          # Workspace tree with run entries
│   ├── _inspector_header.html      # Shared inspector header (type badge, close/pin buttons)
│   ├── _pending_tray.html          # Amber pending-changes tray (OOB-swappable)
│   │
│   │  # QUAM State pages (each has a full page + HTMX partial)
│   ├── explorer.html / _explorer.html          # JSON tree viewer (state.json/wiring.json)
│   ├── qubits.html / _qubits.html              # Qubit list table
│   ├── qubit_detail.html / _qubit_detail.html  # Qubit inspector (7 sections, inline edit)
│   ├── pairs.html / _pairs.html                # Pair list table
│   ├── pair_detail.html / _pair_detail.html    # Pair inspector
│   ├── table.html / _table.html                # Flat property comparison table
│   ├── wiring.html / _wiring.html              # Port wiring + Plotly topology graph
│   ├── instrument_wiring.html / _instrument_wiring.html  # Instrument role assignments (SVG)
│   ├── diff.html / _diff.html                  # 2-way diff form + results
│   ├── _search_results.html                    # Live search results
│   │
│   │  # Compare pages
│   ├── compare.html                # Compare wrapper
│   ├── _compare_tabs.html          # Tab bar (State / Diff / Full)
│   ├── _compare_diff.html          # Side-by-side diff
│   ├── _compare_state.html         # Per-state tree viewer
│   ├── _compare_full.html          # Unified tree with Diff Only + Ref Data
│   ├── _trend_picker.html          # Grouped property/qubit selector
│   ├── _trend_chart.html           # Plotly trend chart + experiment legend
│   │
│   │  # Dataset pages
│   ├── datasets.html / _datasets.html          # Run list table (filter, search, bookmarks)
│   ├── dataset_detail.html / _dataset_detail.html  # Run inspector (5 tabs)
│   ├── _dataset_h5.html            # HDF5 variable table + qubit multi-selector + Plotly plots
│   ├── _dataset_compare.html       # Multi-run comparison (figures, fit results, parameters)
│   ├── _bookmarks_panel.html       # Sidebar bookmarks with note icons
│   │
│   │  # Trend pages
│   ├── trends.html / _trends.html  # Trend dashboard
│   ├── _trends_data.html           # Trend charts + parameter diffs + figure strip
│   │
│   │  # Legacy (still in DOM but functionality moved)
│   └── _changes.html               # Old changes panel (replaced by _pending_tray.html)
│
└── static/
    ├── app.js              # 3,887 lines — all client-side JS
    ├── style.css           # 2,677 lines — all custom CSS (~400 CSS custom properties in :root + [data-theme="dark"])
    ├── pico.min.css        # Pico CSS framework (82 KB)
    ├── htmx.min.js         # HTMX 2.0.4 (50 KB)
    ├── split.min.js        # Split.js — vertical pane drag (6.7 KB)
    └── plotly.min.js       # Plotly.js 2.35.2 (4.4 MB) — topology, trends, HDF5 data plots
```

## Routes (53 total)

### Home & Load (2 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/` | GET | full | Welcome page with getting-started steps |
| `/load` | POST | `#table-pane` | Load a quam_state folder, activate QUAM context, redirect to `/explorer` |

### Explorer (1 route)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/explorer` | GET | `#table-pane` | Interactive JSON tree viewer for state.json / wiring.json with search, depth controls |

### Qubits (3 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/qubits` | GET | `#table-pane` | Qubit list with grid location, f_01, T1, x180 amplitude |
| `/qubit/<name>` | GET | `#inspector-pane` | Qubit detail: 7 sections (Identity, Frequencies, Coherence, XY Drive, Readout, Flux, Gate Fidelity) with inline edit, pointer resolution markers |
| `/qubit/<name>/edit` | POST | `#inspector-pane` | Inline edit — type-coerced, returns updated panel + OOB pending tray |

### Field Editing (1 route)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/field/edit` | POST | varies | Generic dot-path field edit (any property in any context) |

### Pairs (2 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/pairs` | GET | `#table-pane` | Pair list with CZ gate summary, Bell fidelity color-coding |
| `/pair/<name>` | GET | `#inspector-pane` | Pair detail inspector |

### Table / Chip Topology / Instrument (4 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/table` | GET | `#table-pane` | Flat property comparison table with column selector, chain group-by, min/max color-coding |
| `/wiring` | GET | `#table-pane` | **Chip Topology dashboard** -- scrollable page with: summary stat cards, HTML/SVG topology with always-visible qubit property cards (primary: T1/T2r/T2e/GateF/RO_fg/RO_fe, secondary collapsible), heatmap grid, distribution histograms, enriched pair summary (gate fidelities + confusion matrix off-diagonals), per-metric detail panels (13 metrics). Includes live file-change detection with diff overlay. |
| `/topology` | GET | `#table-pane` | Alias for `/wiring` |
| `/instrument` | GET | `#table-pane` | SVG instrument wiring diagram with role-colored port circles, hover popups |

### Search (1 route)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/search` | GET | `#inspector-pane` | Live search results (150ms debounce via HTMX `hx-trigger`) |

### State Modification (4 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/save` | POST | `#status-bar` | Atomic save with backup, returns toast + OOB pending tray |
| `/undo` | POST | `#status-bar` | Undo last change |
| `/changes` | GET | fragment | Show pending changes panel (legacy) |
| `/discard` | POST | `#pending-tray` | Discard single change by index or all changes |

### Export (1 route)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/export` | GET | download | CSV file download of qubit summary |

### Diff & Compare (5 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/diff` | GET/POST | `#table-pane` | 2-way diff form + results (with Browse buttons, delta column) |
| `/compare` | POST | `#table-pane` | Load 2-4 experiment states, show tabbed compare view |
| `/compare/diff` | GET | fragment | Differences tab (side-by-side, color-coded) |
| `/compare/state` | GET | fragment | Per-state tree viewer tab |
| `/compare/full` | GET | fragment | Unified tree with Diff Only toggle + Ref Data dropdown |

### Trend (2 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/trend` | POST | fragment | Trend property/qubit picker modal |
| `/trend/chart` | POST | fragment | Render Plotly trend chart with experiment legend |

### Workspace (5 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/workspace/add` | POST | `#sidebar-tree` | Add a root folder to workspace |
| `/workspace/remove` | POST | `#sidebar-tree` | Remove a root folder |
| `/workspace/tree` | GET | `#sidebar-tree` | Render workspace tree (polled every 60s, rescans if stale) |
| `/workspace/refresh` | POST | `#sidebar-tree` | Force-rescan all workspace roots, clear dataset cache |
| `/workspace/select` | POST | `#table-pane` | Load experiment's quam_state, redirect to `/qubits` |

### Browse (1 route)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/browse` | GET | JSON | Folder browser — lists directories + files for the folder browser dialog |

### JSON API (4 routes)

| Route | Method | Description |
|-------|--------|-------------|
| `/api/qubit/<name>` | GET | Qubit properties as JSON |
| `/api/pair/<name>` | GET | Pair properties as JSON |
| `/api/search?q=...` | GET | Search results as JSON array |
| `/api/topology` | GET | Graph nodes + edges as JSON (pass `?refresh=1` to force server-side cache refresh when files changed on disk) |
| `/api/topology-mtime` | GET | Returns `{state_mtime, wiring_mtime, folder}` — lightweight endpoint polled every 3s by the topology page for live file-change detection |

### Dataset Management (12 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/datasets` | GET | `#table-pane` | Run list table with experiment chip filter, date tabs, search, checkboxes |
| `/datasets/rescan` | POST | `#table-pane` | Rescan workspace for new runs |
| `/dataset/<id>` | GET | `#inspector-pane` | Run detail inspector (5 tabs: Overview, Results, Figures, Data, State) |
| `/dataset/<id>/fig/<name>` | GET | binary | Serve figure image from run folder |
| `/dataset/<id>/h5` | GET | fragment | HDF5 variable summary + qubit selector |
| `/dataset/<id>/h5/plot` | GET | JSON | Plotly-ready data for one HDF5 variable slice |
| `/dataset/<id>/json` | GET | fragment | JSON tree viewer for node.json, data.json, state.json, wiring.json |
| `/dataset/<id>/load-state` | POST | `#table-pane` | Load this run's quam_state into the QUAM editor |
| `/dataset/<id>/bookmark` | POST | JSON | Toggle bookmark on a run |
| `/dataset/<id>/tag` | POST | JSON | Add tag to a run |
| `/dataset/<id>/tag` | DELETE | JSON | Remove tag from a run |
| `/dataset/<id>/note` | POST | JSON | Set/update note on a run |

### Bookmarks & Tags (2 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/bookmarks/panel` | GET | fragment | Sidebar bookmarks list (HTMX trigger: `load, bookmark-changed from:body`) |
| `/datasets/tags` | GET | JSON | List all unique tags across all runs (for tag picker autocomplete) |

### Dataset Compare & Trends (3 routes)

| Route | Method | Target | Description |
|-------|--------|--------|-------------|
| `/datasets/compare` | GET | `#table-pane` | Multi-run compare view (Figures, Fit Results, Parameters tabs) |
| `/trends` | GET | `#table-pane` | Trend dashboard — experiment/qubit selectors |
| `/trends/data` | GET | fragment | Trend charts + parameter diffs + figure timeline |

## HTMX Patterns

### Dual-render

Every page route checks for `HX-Request` header:
- **HTMX request**: returns `_fragment.html` (partial swap into target pane)
- **Full page request**: returns `page.html` (extends `base.html`, includes the fragment)

This means every page works as a standalone URL and as an HTMX partial swap.

### Sidebar layout (top to bottom)

1. **Bookmarks** — collapsible panel loaded via HTMX (`/bookmarks/panel`), refreshes on `bookmark-changed` event
2. **Generate Config** — the "creation" entry, pinned at the top above a divider (builds a new chip rather than viewing one)
3. **Chip navigation** — Chip Status, Explorer, Qubits, Pairs, Table, Instrument Wiring, Param History
4. **QUAM state folder path + Load button** — manually load a `quam_state` folder
5. **Cross-cutting tools** — Datasets, Diff, Trends
6. **Workspace** — collapsible `<details>` with: add-folder form, filter input with tag rendering, workspace tree (polled every 60s via `hx-trigger`)
7. **Pending changes tray** — amber bar showing unsaved edit count, OOB-swapped

### Target panes

| Pane | Purpose |
|------|---------|
| `#table-pane` | Main content area (qubit list, dataset table, diff, trends) |
| `#inspector-pane` | Detail panel below table (qubit detail, dataset detail, search results) |
| `#status-bar` | Auto-fading toast messages (save confirmation, errors) |
| `#sidebar-tree` | Workspace folder tree in the sidebar |
| `#pending-tray` | Pending changes tray (OOB-swapped via `hx-swap-oob`) |

### Out-of-band swaps

The pending changes tray uses `hx-swap-oob="outerHTML:#pending-tray"` to update the change count from any route that modifies state (edit, undo, save, discard).

### Event-driven refresh

- `bookmark-changed` custom event: dispatched by `toggleDatasetBookmark()` and `saveDatasetNote()` — triggers HTMX to refetch `/bookmarks/panel`
- `inspector-closed` custom event: triggers split pane re-initialization
- `htmx:afterSwap`: restores experiment chip filters, sticky tab state, and dataset detail state
- `htmx:afterSettle`: restores pending tray collapse state
- `htmx:pushedIntoHistory`: syncs active nav link highlighting

## App Factory

`create_app(*, testing=False)` builds the Flask app with:
- `sys._MEIPASS`-aware template/static paths (PyInstaller compatibility)
- Random `SECRET_KEY` per launch
- Multi-context registry in `app.config`:

| Config key | Type | Purpose |
|------------|------|---------|
| `workspace` | `Workspace` | Folder scanner instance |
| `contexts` | `dict[str, dict]` | Named context registry (each has `type`, `path`, and type-specific objects) |
| `active_context` | `str \| None` | Name of the currently active context |
| `dataset_store` | `DatasetStore \| None` | Cached dataset store (cleared on workspace rescan) |

### Context activation

`_activate_quam(folder_path)` loads a quam_state folder and registers it as the active context:
```python
context = {
    "type": "quam",
    "path": str(folder_path),
    "store": QuamStore(folder_path),
    "engine": QueryEngine(store),
    "index": SearchIndex.build(store.merged, wiring_keys),
    "modifier": Modifier(store),
    "saver": Saver(store),
}
app.config["contexts"][name] = context
app.config["active_context"] = name
```

Route helpers `_store()`, `_engine()`, `_modifier()`, `_saver()`, `_index()` pull from the active context transparently.

## Client-Side JS (`app.js` — 3,054 lines)

### Key global functions (38 `window.*` exports)

**Layout & Settings:** `toggleSidebar()`, `toggleSettings()`, `setFontSize()`, `closeInspector()`

**Editing:** `focusEditInput()`, `togglePendingTray()`, `_restoreTrayState()`

**Navigation:** `initPathAutocomplete()`, `renderFilterTags()`, `filterTable()`, `switchCompareTab()`

**Instrument Wiring:** `renderInstrumentWiring()`, `openInspectorFromPopup()`

**Dataset Inspection:** `switchDatasetTab()`, `toggleFigureZoom()`, `loadDatasetH5()`, `plotOrSelectQubit()`, `_removeSelection()`

**Dataset Management:** `toggleDatasetBookmark()`, `openTagPicker()`, `removeDatasetTag()`, `saveDatasetNote()`, `openBookmarkNote()`

**Multi-Select & Compare:** `updateCompareButton()`, `toggleAllDatasetCheckboxes()`, `compareSelectedDatasets()`, `switchCompareDatasetTab()`

**Trends:** `loadTrendData()`

**Pin & Browse:** `togglePinDataset()`

**Dataset Filter:** `toggleExpFilter()`, `filterDatasetTable()`

### Key configuration object

```js
var UI_CONFIG = {
    autoRefreshInterval: 60,       // seconds — workspace tree + dataset poll
    topoLivePollInterval: 3,       // seconds — topology page polls /api/topology-mtime (0 = disabled)
    split: { gutterSize: 6, defaultSizes: [55, 45], minSizes: [100, 50] },
    plotly: {
        topology: {
            dashboard: {
                colorScale: ['#e0f3db','#a8ddb5','#7bccc4','#43a2ca','#0868ac'],  // GnBu 5-stop
                nullCellColor: '#f0f0f0',
                pairBarColor: '#43a2ca',
            }
        },
        h5Plot: { height: 340, margin: {l:50,r:20,t:30,b:40} }
    }
};
```

### Sticky state system

The `_dsSticky` object preserves dataset inspector state across HTMX navigations:
- `tab` — current tab name (overview/results/figures/data/state)
- `figIdx` — current figure index
- `scrollTop` — inspector scroll position
- `plot` — HDF5 multi-plot selections (`{which, experimentType, selections: [{varName, dims, qubitIdx}]}`)

Captured in `htmx:beforeSwap`, restored in `htmx:afterSwap`.

## CSS (`style.css` — 1,871 lines)

130 CSS custom properties in `:root` controlling:
- Typography: `--font-ui`, `--font-mono`, `--font-size-base`
- Layout: `--topbar-height`, `--sidebar-width`, `--split-gutter-size`
- Table density: `--data-table-td-pad-v`, `--prop-table-td-pad-v`
- Tree: `--tree-entry-pad-v`, `--tree-date-label-font`
- Bookmarks: `--bm-star-font`, `--bm-id-font`, `--bm-exp-font`

User-selectable font size via `data-font-size` attribute on `<html>`: S (13px), M (14px), L (16px).

## UI State Persistence (localStorage)

| Key | Storage | Purpose |
|-----|---------|---------|
| `quam_font_size` | localStorage | Font size preference (empty/small/large) |
| `quam_sidebar_collapsed` | localStorage | Sidebar collapse state ("1" or absent) |
| `quam_tray_open` | sessionStorage | Pending changes tray expanded |
| `quam_split_sizes` | localStorage | Split pane size percentages [table%, inspector%] |
| `quam_topo_highlight` | localStorage | Last selected topology highlight metric |
| `recentFolders` | localStorage | Recently used folder paths (array, max 10) |

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `web/app.py` | 63 | App factory, PyInstaller-aware paths |
| `web/routes.py` | 1,786 | 53 routes + ~30 helper functions |
| `web/templates/` | 42 files | 13 full pages + 29 HTMX partial fragments |
| `web/static/app.js` | 3,054 | All client-side JS (38 public functions) |
| `web/static/style.css` | 1,871 | All custom CSS (130 CSS custom properties) |
| `web/static/pico.min.css` | (vendor) | Pico CSS framework |
| `web/static/htmx.min.js` | (vendor) | HTMX 2.0.4 |
| `web/static/split.min.js` | (vendor) | Split.js resizable panes |
| `web/static/plotly.min.js` | (vendor) | Plotly.js 2.35.2 |
| `tests/test_web.py` | ~4,700 | 195 tests (all passing) |

## Test coverage (195 tests)

| Area | Tests |
|------|-------|
| Home + Load | 4 |
| Explorer | 12 |
| Qubits + Edit | 26 |
| Pairs | 8 |
| Table | 4 |
| Wiring + Instrument | 4 |
| Search | 6 |
| Save/Undo/Discard | 8 |
| Export | 2 |
| Diff | 6 |
| Compare (multi-state) | 32 |
| Trend | 11 |
| Workspace | 10 |
| JSON API | 10 |
| Datasets + Bookmarks + Tags | 20 |
| Pending Changes Tray | 12 |
| Field Edit | 8 |
| Real data (17-qubit) | 12 |

## Notes for developers

- **HTMX dual-render pattern**: each route checks `_is_htmx()` and renders either `_foo.html` (fragment) or `foo.html` (full page extending `base.html`). Adding new pages means creating two template files.

- **Shared state** lives in `app.config` (not `g` or sessions) because it must persist across requests. Designed for single-user desktop use. Multi-user would need per-session stores.

- **Multi-context registry**: `app.config["contexts"]` maps context names to dicts with a `type` field. Currently only `"quam"` type exists, but the design supports adding HDF5/dataset context types without restructuring.

- **OOB swap pattern**: the pending changes tray is updated via `hx-swap-oob="outerHTML:#pending-tray"` appended to responses from edit/save/discard routes. This keeps the tray in sync without separate polling.

- **Dataset auto-refresh**: the datasets table has a hidden `#ds-autopoll` div with `hx-trigger: every Ns` (configured via `UI_CONFIG.autoRefreshInterval`). It includes `#ds-active-date` to preserve the date filter across polls.
