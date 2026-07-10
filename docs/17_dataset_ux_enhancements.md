# Dataset UX Enhancements — Progress Log

> Picks up where `16_advanced_ui_features.md` left off.
> Covers cross-run comparison tools, bookmark/tag improvements, sticky navigation, and compact UI density.

---

## Summary table

| # | Feature | Status | Files changed |
|---|---------|--------|---------------|
| 1 | Multi-Select Compare | **Done** | routes.py, app.js, style.css, _datasets.html, _dataset_compare.html |
| 2 | Trend Dashboard | **Done** | routes.py, app.js, style.css, base.html, trends.html, _trends.html, _trends_data.html |
| 3 | Pin & Browse | **Done** | routes.py, app.js, style.css, _inspector_header.html |
| 4 | Bookmarks Sidebar Panel | **Done** | routes.py, app.js, style.css, base.html, _bookmarks_panel.html |
| 5 | Notion-Style Tag Picker | **Done** | routes.py, app.js, style.css, _datasets.html |
| 6 | Dataset Note Input | **Done** | app.js, style.css, _dataset_detail.html |
| 7 | Sticky Tab/Figure/Plot State | **Done** | app.js |
| 8 | Compact UI Density | **Done** | style.css, _bookmarks_panel.html |
| 9 | Auto-Refresh Datasets | **Done** | routes.py, app.js, _datasets.html, scanner.py |
| 10 | Multi-Select HDF5 Plots | **Done** | app.js, style.css |
| 11 | Graceful Sticky Fallback | **Done** | app.js, style.css |
| 12 | Sidebar → Dataset Detail | **Done** | app.js, _sidebar_tree.html |
| 13 | Bookmark Note Popover | **Done** | app.js, style.css, _bookmarks_panel.html |

---

## Feature 1: Multi-Select Compare

### What it does

Checkboxes on each dataset table row allow users to select 2–5 runs and compare them side-by-side. A sticky "Compare Selected" bar appears at the bottom of the table when items are checked.

### How it works

- Checkbox column added to `_datasets.html` (header + per-row)
- `compareSelectedDatasets()` in `app.js` collects checked run IDs, sends `GET /datasets/compare?ids=...`
- `routes.py` → `datasets_compare()` loads run details, uses `Differ.compare_parameters()` and `compare_fit_results()` for diff
- `_dataset_compare.html` renders 3 tabs: Figures (side-by-side grid), Fit Results (diff table), Parameters (diff table)

### Key files

- `routes.py`: `GET /datasets/compare`
- `_dataset_compare.html`: comparison view template (new)
- `_datasets.html`: checkbox column + compare bar
- `app.js`: `updateCompareButton()`, `toggleAllDatasetCheckboxes()`, `compareSelectedDatasets()`

---

## Feature 2: Trend Dashboard

### What it does

A dedicated "Trends" page showing Plotly charts of key metrics (T1, T2, frequency, fidelity, etc.) over time, filterable by experiment type and qubit.

### How it works

- Sidebar nav link "Trends" added to `base.html`
- `GET /trends` renders `trends.html` with experiment/qubit dropdowns
- `GET /trends/data?experiment=...&qubit=...` returns `_trends_data.html` with:
  - Plotly scatter charts for each metric found in fit results
  - Figure timeline strip showing saved PNG figures
  - Parameter diff table across selected runs
- `loadTrendData()` in `app.js` triggers the HTMX data fetch

### Key files

- `routes.py`: `GET /trends`, `GET /trends/data`
- `trends.html`, `_trends.html`, `_trends_data.html` (all new)

---

## Feature 3: Pin & Browse

### What it does

Pin one dataset run in the inspector, then click other runs to view them side-by-side in a split layout. Tabs sync between pinned and current columns.

### How it works

- Pin button (📌) added to `_inspector_header.html` for dataset-type inspectors
- `togglePinDataset()` captures the current inspector HTML as "pinned"
- `htmx:beforeSwap` interceptor detects pinned state:
  - Prevents default swap
  - Builds two-column layout with `_wrapPinnedLayout()`
  - Syncs tabs between columns via `_syncPinnedTabs()`
- Clicking pin again unpins and restores normal single-column layout

### Key files

- `app.js`: `togglePinDataset()`, `_wrapPinnedLayout()`, `_syncPinnedTabs()`, `_switchBothColumns()`
- `_inspector_header.html`: pin button
- `style.css`: `.inspector-split`, `.pinned-label`, `.inspector-pin`

---

## Feature 4: Bookmarks Sidebar Panel

### What it does

A persistent "Bookmarks" section in the sidebar showing all bookmarked dataset runs. Survives app restarts (stored in `quashboard_tags.json`). Each item shows run ID, experiment name, qubits, and date in a single compact line.

### How it works

- `_bookmarks_panel.html` renders bookmarked runs from `DatasetStore.list_runs(bookmarked_only=True)`
- Inserted into `base.html` sidebar between workspace tree and nav links
- Auto-refreshes via HTMX custom event: `hx-trigger="load, bookmark-changed from:body"`
- When user toggles a bookmark (star click), `toggleDatasetBookmark()` dispatches `bookmark-changed` event
- Clicking a bookmark item loads that run in the inspector

### Persistence

Already handled by existing `quashboard_tags.json` persistence layer — no backend changes needed.

### Bug fix

`/bookmarks/panel` returned 500 when no workspace was configured. Root cause: `_dataset_store()` called `_ws()` which did `config["workspace"]` (KeyError). Fixed to use `config.get("workspace")` (returns None).

### Key files

- `_bookmarks_panel.html` (new)
- `routes.py`: `GET /bookmarks/panel`
- `base.html`: bookmarks panel container
- `app.js`: `bookmark-changed` event dispatch

---

## Feature 5: Notion-Style Tag Picker

### What it does

Replaces the browser `prompt()` dialog for adding tags with an inline dropdown. Shows all existing tags with checkmark toggles, plus a "New:" input for creating new tags. Tags are visually smaller.

### How it works

- Click "+" on a dataset row → `openTagPicker(runId, btnEl)` fires
- Fetches all tags via `GET /datasets/tags`
- Builds a positioned dropdown listing existing tags with ✓/blank prefix
- Click a tag → instant toggle (POST to add, DELETE to remove)
- Type new tag + Enter → creates and applies immediately
- Click-outside or Escape closes the picker

### Key files

- `app.js`: `openTagPicker()` (~80 lines), replaces `promptAddTag()`
- `routes.py`: `GET /datasets/tags`
- `style.css`: `.tag-picker`, `.tag-picker-item`, `.tag-picker-new`
- `_datasets.html`: changed onclick to `openTagPicker`

---

## Feature 6: Dataset Note Input

### What it does

Moved the note field from a hidden `<details>` collapse inside the Overview tab to a compact always-visible text input above the tabs. Auto-saves on blur with green border flash confirmation.

### Key files

- `_dataset_detail.html`: `<div class="ds-note-row">` above `<nav class="dataset-tabs">`
- `app.js`: `saveDatasetNote()` with visual feedback + `bookmark-changed` dispatch
- `style.css`: `.ds-note-row`, `.ds-note-input`

---

## Feature 7: Sticky Tab/Figure/Plot State

### What it does

When navigating between dataset runs (via bookmarks, table rows, parent links, or any method), the inspector stays on the same tab, same figure position, and replays the same HDF5 plot. Works across different experiment types.

### Architecture (rewrite)

Replaced fragile click-based `_dsViewState` capture with always-on `_dsSticky` ambient state:

```
var _dsSticky = {
    tab: 'overview',       // Updated by switchDatasetTab()
    figIdx: null,          // Captured in htmx:beforeSwap (DOM still intact)
    scrollTop: 0,          // Captured in htmx:beforeSwap
    plot: null,            // Captured in htmx:beforeSwap
    currentRunId: null,    // Tracks what's currently shown
};
```

**Why this is better than click-based:**

| | Click-based (old) | Always-on (new) |
|--|--|--|
| Same experiment bookmark | ✓ | ✓ |
| Different experiment bookmark | ✗ reset | ✓ |
| Parent/child link in detail | ✗ miss | ✓ |
| Any navigation method | ✗ click-dependent | ✓ |

### Key flow

1. `switchDatasetTab()` always updates `_dsSticky.tab`
2. `htmx:beforeSwap` (inspector target) captures figure index + scroll position + plot state
3. `htmx:afterSwap` detects new run ID, restores tab → figure scroll → plot replay

### Key file

- `app.js`: `_dsSticky` object, `htmx:beforeSwap` capture, `htmx:afterSwap` restore

---

## Feature 8: Compact UI Density

### Problem

Three UI areas were too spacious: workspace tree date rows, bookmark items, and h5 data table rows.

### Root cause

PicoCSS applies fixed calculated `height` + generous `padding` to `summary:not([role])` and `button` elements:
```css
summary:not([role]) { height: calc(1rem * var(--pico-line-height) + ...); padding: var(--pico-form-element-spacing-vertical) ...; }
td, th { padding: calc(var(--pico-spacing) / 2) var(--pico-spacing); }
```

Custom CSS rules with class selectors (`.h5-vars-table td`) couldn't override Pico's type selectors (`td`) due to equal or lower specificity.

### Fix

1. **Pico summary/button override**: `height: auto; padding: .15rem .25rem` on all sidebar/inspector summaries
2. **Table specificity boost**: `table.h5-vars-table td` (specificity 0,1,2) beats Pico's `td` (specificity 0,0,1)
3. **Button height override**: `.btn-sm { height: auto }` removes Pico's fixed height calc
4. **Workspace tree**: colored date labels, VSCode-style indent guide (`border-left` on `.tree-entries`)
5. **Bookmark items**: 2-line stacked layout → single-line flex layout

### Results

| Element | Before | After |
|---------|--------|-------|
| Sidebar date-group row | ~40px | ~20px |
| Bookmark item | ~46px (2 lines) | ~20px (1 line) |
| H5 variable row | ~36px | ~18px |
| Prop-table row | ~32px | ~18px |

### Key files

- `style.css`: specificity overrides, `height:auto`, `line-height:1.3`
- `_bookmarks_panel.html`: single-line item layout

---

## Feature 9: Auto-Refresh Datasets

### What it does

The Datasets tab automatically polls for new experiment runs without requiring a manual page reload. Configurable poll interval via `UI_CONFIG.autoRefreshInterval` (default 60s). Date filter and experiment chip selections are preserved across auto-refresh swaps.

### How it works

- `_datasets.html` contains a hidden `#ds-autopoll` anchor with `hx-get="/datasets"` and `hx-target="#table-pane"`
- An inline `<script>` reads `UI_CONFIG.autoRefreshInterval` and sets `hx-trigger="every Ns"` dynamically, then calls `htmx.process()` to activate
- Hidden `<input id="ds-active-date">` carries the current date filter so the poll re-fetches with the same filter
- `htmx:afterSwap` listener on `#table-pane` restores `_selectedExps` chip active states after each swap
- Server-side: `scanner.py` `rescan_if_stale()` uses filesystem mtime checks — cheap enough for short poll intervals. `POST /workspace/refresh` triggers `rescan_all()`.

### Key files

- `_datasets.html`: `#ds-autopoll` anchor, `#ds-active-date` hidden input, inline script
- `app.js`: `htmx:afterSwap` experiment chip restoration, `UI_CONFIG.autoRefreshInterval`
- `scanner.py`: `rescan_if_stale()`, `_scan_times` dict, `_is_root_stale()`
- `routes.py`: `POST /workspace/refresh`

---

## Feature 10: Multi-Select HDF5 Plots

### What it does

Users can select multiple variables AND multiple qubits simultaneously and see all plots rendered in a stacked layout. Clicking "Plot" toggles a variable into/out of the selection (button label changes to "Remove" when selected). For 3D+ data with qubit dimensions, a multi-select qubit button row appears.

### Architecture

Replaced old single-plot `plotDatasetVar` with a multi-selection system. State stored in `_dsLastPlot`:

```
{
    which: 'ds_raw' | 'ds_fit',
    experimentType: 'ramsey' | 'T1' | ...,
    selections: [
        { varName: 'I', dims: ['x'], qubitIdx: null },
        { varName: 'state', dims: ['x', 'qubit'], qubitIdx: 2 },
        ...
    ]
}
```

### How it works

- `plotOrSelectQubit(triggerEl, runId, which, varName, dims)` is the main entry point
- For 1D/2D data: `_toggleSelection()` adds/removes from selections, `_renderAllSelections()` rebuilds the plot stack
- For 3D+ data: `_showQubitMultiSelector()` renders a button per qubit label; each button toggles that qubit into the selection
- `_fetchAndRenderPlot()` fetches `/dataset/<runId>/h5plot?var=...&qubit_idx=...` and renders with Plotly
- `_renderAllSelections()` fully rebuilds the DOM each time (avoids stale `data-idx` references)
- `_removeSelection(idx, runId)` removes a specific selection entry and re-renders
- `_updateVarRowStates()` updates row highlights (`.h5-var-selected`) and button labels across the table
- Switching `ds_raw` ↔ `ds_fit` clears all selections and removes plot UI elements
- Per-run coordinate map `_h5CoordsById[runId]` avoids cross-contamination in split views

### Key files

- `app.js`: `_toggleSelection()`, `_renderAllSelections()`, `_fetchAndRenderPlot()`, `_showQubitMultiSelector()`, `_removeSelection()`, `_updateVarRowStates()`, `_findQubitDim()`, `_getQubitCount()`
- `style.css`: `.h5-var-selected`, `.h5-plot-entry`, `.h5-qubit-selector`

---

## Feature 11: Graceful Sticky Fallback

### What it does

When navigating between dataset runs of different experiment types, instead of showing an error or losing state, the system auto-falls back to the first available variable with a yellow caution banner: "⚠ Different experiment type — showing default variable".

### How it works

- `_currentExperimentType()` reads `data-experiment` attribute from `#ds-detail-root`
- On `htmx:afterSwap` sticky restore, compares saved `plot.experimentType` with new `_currentExperimentType()`
- **Same experiment type**: validates each saved selection still exists in the new run's variable list. Validates qubit indices are in range using `_getQubitCount()`.
- **Different experiment type**: sets `usedFallback = true`, picks the first available variable with `qubitIdx: 0` (if 3D+) or `null`
- If same-experiment but all selections are now invalid, also falls back
- Shows `.h5-caution-banner` div when fallback was used
- Deep-copies `_dsLastPlot` in `htmx:beforeSwap` via `JSON.parse(JSON.stringify())` to avoid mutation during async restore
- Uses `MutationObserver` to detect when the h5-vars-table appears in the DOM before attempting restore

### Bug fix

`innerHTML` does NOT execute embedded `<script>` tags. When `loadDatasetH5` fetches HDF5 data via JavaScript `fetch()`, the response HTML contains `<script>` tags that populate `window._h5CoordsById[runId]`. Fix: after setting innerHTML, clone each script and append to `document.head`:

```javascript
container.querySelectorAll('script').forEach(function(s) {
    var ns = document.createElement('script');
    ns.textContent = s.textContent;
    document.head.appendChild(ns);
    document.head.removeChild(ns);
});
```

### Key files

- `app.js`: `_currentExperimentType()`, `_getQubitCount()`, `htmx:afterSwap` restore logic, `loadDatasetH5` script execution fix
- `style.css`: `.h5-caution-banner`

---

## Feature 12: Sidebar → Dataset Detail

### What it does

Clicking a run entry in the sidebar workspace tree now loads the dataset detail view (HDF5 data, figures, fit results) into the inspector pane alongside the QUAM state that goes into the table pane.

### How it works

- `_sidebar_tree.html` adds `data-run-id="{{ entry.run_id }}"` attribute to `.tree-entry-click` spans
- A delegated click handler on `document` catches clicks on `.tree-entry-click[data-run-id]`
- Fires `htmx.ajax('GET', '/dataset/' + runId, {target: '#inspector-pane', swap: 'innerHTML'})` in parallel with the existing HTMX `hx-get="/workspace/select"` on the same element
- Result: one click loads both the QUAM qubit table (table pane) and the dataset detail (inspector pane)

### Key files

- `app.js`: delegated click handler (lines 239–246)
- `_sidebar_tree.html`: `data-run-id` attribute on tree entries

---

## Feature 13: Bookmark Note Popover

### What it does

A pencil icon (✎) next to each bookmark in the sidebar panel. Hovering shows the note as a tooltip. Clicking opens an inline textarea popover for editing. Saves on blur or Enter, closes on Escape.

### How it works

- `_bookmarks_panel.html` renders a `.bm-note-btn` button with `data-note`, `title` (for tooltip), and `onclick="openBookmarkNote(runId, this)"`
- Button is dimmed by default, highlighted (`.has-note`) when a note exists
- `openBookmarkNote(runId, btnEl)` creates a `.bm-note-popover` div with a `.bm-note-textarea`
- Positioned relative to the button's parent element
- On save: calls `saveDatasetNote(runId, val)`, updates `data-note` and `title` attributes, toggles `.has-note` class
- Blur handler uses `setTimeout(save, 120)` to avoid premature save when clicking inside the popover

### Key files

- `app.js`: `openBookmarkNote()` (~40 lines)
- `style.css`: `.bm-note-btn`, `.bm-note-popover`, `.bm-note-textarea`
- `_bookmarks_panel.html`: pencil button markup

---

## Commits

| Hash | Message |
|------|---------|
| `37cda30` | feat: add bookmarks panel, tag picker, multi-select compare, trends, and sticky tab state |
| `20da2d3` | fix: compact UI density — override Pico CSS spacing on tables, summaries, buttons |
| `394bb54` | feat: auto-refresh Datasets tab when new experiment runs appear |
| `6929aa1` | feat: multi-select HDF5 plots and graceful sticky fallback |
| `0f05b7b` | fix: execute embedded scripts after loadDatasetH5 innerHTML injection |
| `2952aa4` | feat: load dataset detail in inspector when clicking sidebar run entry |
| `b951fd2` | feat: add inline note editor to bookmark panel entries |
