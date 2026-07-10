# Task 11: Template Polish -- Done

## What was built

A comprehensive visual overhaul of all 15 Jinja2 templates, the CSS stylesheet, and supporting route enhancements. The functional stubs from TODO #10 are now a polished, researcher-friendly interface with pointer-aware editing, color-coded cells, category filtering, and auto-dismissing status toasts.

## What changed

### Route enhancements (`routes.py`)

1. **`_QUBIT_PROPERTY_MAP`**: A new 33-entry mapping that associates each flat property key (e.g. `"readout_amplitude"`) with its section name (e.g. `"Readout"`) and the actual dot_path template in the store (e.g. `"qubits.{name}.resonator.operations.readout.amplitude"`).

2. **`_build_qubit_sections()`**: New helper that builds a list of section dicts for the qubit detail template. For each property it:
   - Looks up the raw value from the store via `store.get_value(dot_path)`
   - Checks if it's a pointer (`#/` or `#../`) or a self-ref (`#./`)
   - Marks non-list/dict fields as editable
   - Passes the dot_path for the inline edit form

3. **Comparison table**: Now computes `col_stats` (min/max per column) and `chains` for color-coding and chain group-by.

4. **Compare**: Now passes `selected_props`, `selected_qubits`, `all_qubit_names`, `trend_props`, and `paths_raw` for interactive selectors.

5. **Search**: Now passes `active_category` for highlighting the active category tab.

### Template changes

| Template | Key changes |
|----------|-------------|
| `base.html` | Sticky topbar, sidebar filter input, active nav highlighting, status toast auto-fade (3s), welcome page with steps |
| `_sidebar_tree.html` | Status color-coding (green=finished, red=failed, gray=standalone), run_id badge, experiment name truncation, time display |
| `_qubit_detail.html` | **Full rewrite**: 4-column table (Property / Value / Pointer / JSON Path), grouped by 7 sections (Identity, Frequencies, Coherence, XY Drive, Readout, Flux, Gate Fidelity), inline `<form>` edit on each scalar field, pointer badge with blue highlight, self-ref shown as gray italic "(runtime)", null as "not set", click-to-copy on JSON paths |
| `_pair_detail.html` | Number formatting, null/list display, control/target shown in header |
| `_qubits.html` | Gate fidelity color-coded (green >=0.99, yellow >=0.95, red <0.95) |
| `_pairs.html` | Bell fidelity color-coded similarly |
| `_table.html` | Chain group-by dropdown, color-coded min/max cells (green=best, yellow=worst), scrollable container |
| `_search_results.html` | Category filter tabs (All / Qubit / Pair / Port / Wiring / Config), color-coded category badges, entity + key columns |
| `_diff.html` | Filter dropdown (All/Modified/Added/Removed), colored type badges, structured form layout, float formatting in value cells |
| `_compare.html` | Property selector checkboxes, qubit selector checkboxes, proper data table under chart, Plotly legend at bottom |
| `_wiring.html` | **Completely rewritten** as a rich Chip Topology dashboard (see below) |
| `_status.html` | Now uses `.toast` class for auto-fade |

### CSS (`style.css`)

Grew from 35 lines to ~250 lines. Key additions:

- **Layout system**: `.app-layout` flexbox with sticky topbar, scrollable sidebar/main/detail panels
- **Sidebar tree**: `.tree-*` classes for the VS Code-style explorer
- **Status badges**: `.status-finished`, `.status-failed`, `.status-standalone` with colored backgrounds
- **Pointer-aware rows**: `.row-pointer` (blue tint), `.row-selfref` (gray tint), `.pointer-badge`, `.selfref-badge`
- **Inline editing**: `.edit-input` with focus ring, `.inline-edit` no-margin form
- **Color-coded cells**: `.cell-good` / `.cell-ok` / `.cell-bad` for fidelity, `.cell-best` / `.cell-worst` for table extremes
- **Category badges**: `.cat-qubit` (blue), `.cat-pair` (pink), `.cat-port` (green), `.cat-wiring` (orange), `.cat-config` (purple)
- **Diff**: `.diff-row-added/removed/modified` background colors, `.diff-type-badge` pill badges
- **Toast**: Auto-fade transition on `.toast` elements

## Files changed

| File | Lines | What changed |
|------|-------|-------------|
| `web/routes.py` | 690 (was 633) | Added `_QUBIT_PROPERTY_MAP`, `_build_qubit_sections()`, enhanced comparison/compare/search/diff routes |
| `web/templates/base.html` | 115 (was 84) | Complete rewrite: sticky topbar, sidebar filter, active nav, toast JS |
| `web/templates/_qubit_detail.html` | 82 (was 39) | Complete rewrite: sectioned, pointer-aware, editable |
| `web/templates/_sidebar_tree.html` | 46 (was 36) | Status badges, richer entry display |
| `web/templates/_table.html` | 63 (was 45) | Chain dropdown, color-coded cells |
| `web/templates/_search_results.html` | 47 (was 23) | Category tabs, entity/key columns |
| `web/templates/_diff.html` | 68 (was 42) | Filter dropdown, type badges, structured layout |
| `web/templates/_compare.html` | 97 (was 51) | Property/qubit selectors, data table |
| `web/templates/_wiring.html` | 1,046 (was 59) | Full Chip Topology dashboard with live file-change detection |
| `web/templates/_qubits.html` | 55 (was 50) | Fidelity color-coding |
| `web/templates/_pairs.html` | 31 (was 22) | Fidelity color-coding |
| `web/templates/_pair_detail.html` | 26 (was 18) | Number formatting |
| `web/templates/_status.html` | 3 (was 3) | Toast class |
| `web/static/style.css` | ~250 (was 35) | Full UI styling system |
| `tests/test_web.py` | ~380 (was 310) | 19 new tests for polish features |

## Test coverage

| Area | Tests | Verified |
|------|-------|----------|
| Qubit detail sections | 6 | All 7 sections present, pointer/selfref display, inline edit inputs, null values, dot_path column, port info |
| Search category tabs | 3 | Tabs present, category filter works, badges on results |
| Table color-coding | 2 | Chain filter controls, sort headers |
| Diff filter buttons | 1 | Filter type dropdown with all options |
| Status toast | 2 | Toast class on save, toast class on undo |
| Real data: sections | 1 | All sections + dot_path + inline edit in 17-qubit data |
| Real data: pointers | 1 | Pointer or selfref badges present |
| Real data: search tabs | 1 | Category tabs with real search |
| Real data: table | 1 | Color-coding classes present |
| Real data: diff form | 1 | Filter dropdown present |
| **Total new** | **19** | |
| **Full suite** | **455 passed, 1 skipped** | Up from 436 |

## Chip Topology dashboard (`_wiring.html`)

The original Plotly scatter graph was replaced with a full-page scrollable dashboard showing ALL qubit and pair metrics at a glance. The page is an HTMX partial loaded into `#table-pane` with all JS in a single IIFE using `{{ topology_json | safe }}`.

### Dashboard sections (top to bottom)

1. **Summary stat cards** — 6 cards (Qubits/Pairs count, Avg Gate Fidelity, Avg T1, Avg CZ Fidelity, Avg f₀₁, CZ Coverage) with threshold-based color borders
2. **HTML/SVG topology** — qubit cards positioned by `grid_location` with SVG edge lines. Each card shows:
   - **Primary properties** (always visible): T1, T2r, T2e (all in μs), Gate F, RO_fg, RO_fe (all as percentage, e.g. 93.55)
   - **Secondary properties** (popup overlay via "... more ›"): f₀₁, f₁₂, f_ro, anharmonicity, χ, gate fidelities, amplitudes, readout params
   - All values are heatmap-colored (GnBu scale) by comparing across qubits
   - Click → qubit inspector; double-click → JSON panel
3. **Heatmap grid** — colored cells (one per qubit) synced with "Highlight by" dropdown
4. **Distribution histograms** — 4 Plotly histograms (Gate Fidelity, T1, CZ Fidelity, f₀₁)
5. **Pair summary** — enriched pair cells showing:
   - CZ fidelity (best Bell State Fidelity across all gate macros)
   - Per-gate fidelity breakdown (e.g., `bipolar: F=70.9%, P=77.5%`)
   - Confusion matrix off-diagonal summary (NxN CM: max/avg error rates)
   - Horizontal bar chart sorted by CZ fidelity
6. **Per-metric detail panels** — 15 panels (T1, T2r, T2e, Gate F avg/x180/x90, Assignment F, RO_fg, RO_fe, f₀₁, f_ro, anharmonicity, x180/x90/RO amplitude) each with a topology-positioned grid + annotated bar chart in side-by-side layout

### Live file-change detection

The topology page polls `GET /api/topology-mtime` every 3 seconds (configurable via `UI_CONFIG.topoLivePollInterval`). When `state.json` or `wiring.json` mtimes change:
1. An amber banner appears: "State files changed on disk — Reload with diff"
2. On click, the old topology data (stored in `sessionStorage`) is compared against fresh data from `GET /api/topology?refresh=1`
3. Changed values are highlighted: green outline = improved (higher-is-better metrics), red = degraded, amber = neutral change
4. Inline delta badges show the change magnitude (e.g., `+7.2 μs`, `−0.03`)
5. Highlights fade after 5 seconds

The diff applicator lives in `app.js` (not `_wiring.html`) because it must survive HTMX partial swaps of `#table-pane`.

### Key JS functions (inside IIFE)
- `buildTopology()` — renders HTML/SVG cards with primary/secondary split
- `buildSummaryCards()` — summary stat row
- `buildHeatmapGrid()` / `updateHeatmapGrid(metricKey)` — metric-synced colored cells
- `renderHistograms()` — 4 Plotly histograms
- `buildPairSummary()` — enriched pair grid + bar chart
- `buildMetricPanels()` — 15 topology-positioned grid + bar chart panels
- `propBgColor(prop, value)` — unified GnBu heatmap coloring
- `highlightMetric(key)` — highlights property rows across all qubit cards

### CSS design tokens (fully parameterized)

All topology fonts and dimensions are controlled by `--topo-*` CSS custom properties in `:root` (style.css). Card layout dimensions (width, row height, spacing) live in `UI_CONFIG.plotly.topology.layout` in app.js. Change one place to resize the entire dashboard.

Key variables (see style.css `:root` for full list with comments):
```css
/* Qubit cards */
--topo-node-header-size:  1.44em;   /* card header (qubit name) */
--topo-prop-row-size:     1.25em;   /* property row font */
--topo-prop-row-height:   35px;     /* row min-height — sync with UI_CONFIG layout.rowHeight */
--topo-prop-label-width:  88px;     /* label column width */

/* Per-metric panels */
--topo-panel-cell-size:   132px;
--topo-panel-title-size:  1.56em;
--topo-panel-bar-height:  390px;

/* Heatmap / pair grids */
--topo-heatmap-cell-min:  140px;
--topo-pair-name-size:    1.25em;
```

JS layout (app.js `UI_CONFIG.plotly.topology.layout`):
```js
cardWidth: 278, rowHeight: 35, headerHeight: 48,
gapX: 94, gapY: 62, padding: 48
```

### Unit conventions

| Metric type | Display unit | Conversion |
|-------------|-------------|------------|
| T1, T2ramsey, T2echo | μs | raw seconds × 10⁶ |
| Gate fidelity, RO fidelity, assignment fidelity | percentage (e.g. 99.09) | raw fraction × 100 |
| f₀₁, f_ro | GHz | raw Hz ÷ 10⁹ |
| Anharmonicity, χ | MHz | raw Hz ÷ 10⁶ |

The `%` symbol appears only in panel/section titles (e.g. "Gate Fidelity — RB avg (%)"), not on individual grid values.

## How the pointer-aware UI works

The qubit detail template renders each property with 4 columns:

```
| Property | Value | Pointer | JSON Path |
```

For each property, the route builds this data structure:

```python
{
    "key": "readout_amplitude",          # flat name for display
    "value": 0.042,                      # resolved value
    "raw": 0.042,                        # raw value from store
    "dot_path": "qubits.qA1.resonator.operations.readout.amplitude",
    "is_pointer": False,                 # True if raw is #/ or #../
    "is_self_ref": False,                # True if raw is #./
    "editable": True,                    # False for id, lists, dicts
}
```

Template rendering rules:
1. **Normal scalar**: shows an `<input>` with `hx-post` that submits on Enter
2. **Pointer (`#/` or `#../`)**: shows resolved value as editable input, plus a blue `pointer-badge` in the Pointer column showing the raw pointer string
3. **Self-ref (`#./`)**: shows raw string in gray italic with "(runtime)" label, no edit, gray `selfref-badge`
4. **Null**: shows "not set" in italic gray, with edit input (empty placeholder)
5. **List/dict**: shows "[N items]", not editable

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The `_QUBIT_PROPERTY_MAP` in `routes.py` must be kept in sync with `QueryEngine.get_qubit()` in `query.py`. If a new property is added to `get_qubit()`, add it to the map too, otherwise it won't appear in the sectioned detail view.

- The inline edit submits on Enter (native form submit). There's no debounce -- each edit is a full HTTP round-trip. This is fine for single edits but would need batching for bulk operations (handled by the modifier's `batch_set` on the backend side).

- The toast auto-fade uses vanilla JS on the `htmx:afterSwap` event. It fades after 3 seconds and removes the DOM element after 3.5 seconds. If you need longer-lived messages, adjust the timeouts in `base.html`.

- Plotly is loaded from CDN (`cdn.plot.ly/plotly-2.35.2.min.js`). For the PyInstaller bundle (TODO #13), this needs to be replaced with a local copy in `static/` or bundled via the spec file.

- HTMX is loaded from `unpkg.com`. Same note -- bundle locally for offline `.exe` use.

- The category tab links use `hx-get="/search?q={{query}}&category=..."` which re-fires the search with a category filter. This means changing category re-executes the search. The search itself is <5ms so this is instant.

- The `col_stats` calculation for table color-coding is O(rows * columns) per request. For 500 qubits * 7 columns this is ~3500 comparisons -- negligible.

- The click-to-copy on JSON paths uses `navigator.clipboard.writeText()`. This only works in secure contexts (HTTPS or localhost). Since we serve on `127.0.0.1`, this is fine for the desktop app but wouldn't work on a non-HTTPS deployment.
