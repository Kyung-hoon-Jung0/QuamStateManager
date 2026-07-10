# 19: Plot Click-to-Edit and 2Q Randomized Benchmarking Panels

> Covers all work on the `feat/plot-click-to-edit` branch.
> Two major features: (1) clicking Plotly data points to auto-update state.json fields, and (2) dedicated 2Q RB visualization panels on the Chip Status dashboard.

---

## Summary table

| # | Feature | Status | Files changed |
|---|---------|--------|---------------|
| 1 | Plot click-to-edit (auto-update state.json) | **Done** | dataset.py, app.js, style.css, _dataset_detail.html |
| 2 | IF-to-RF frequency axis conversion | **Done** | dataset.py |
| 3 | Pending tray moved to topbar | **Done** | base.html, style.css, test_web.py |
| 4 | Sidebar nav reorganization | **Done** | base.html, style.css |
| 5 | Load-path localStorage persistence | **Done** | base.html |
| 6 | Gate Fidelity -- 2Q RB panels | **Done** | _wiring.html, style.css, app.js |
| 7 | Chip Status section reorder & rename | **Done** | _wiring.html |

---

## Feature 1: Plot Click-to-Edit

### Problem

Researchers view experiment plots (resonator spectroscopy, qubit spectroscopy, etc.) in the Datasets tab and then manually copy frequency/fidelity values into the Explorer tree to update `state.json`. This round-trip is slow and error-prone -- the user must find the right field, navigate the JSON tree, and type the value by hand.

### Solution

Clicking any data point on a Plotly chart now:
1. Copies the `x, y` coordinates to the clipboard
2. Resolves which qubit was clicked (from trace `customdata`)
3. Looks up the experiment type to find the corresponding `state.json` field(s)
4. Auto-updates the field(s) via `POST /field/edit`
5. Navigates to the Explorer tab with the target field highlighted and scrolled into view

### How it works

**Backend changes (`dataset.py`):**

The `_build_plotly_figure()` method now receives `parameters` (from `run.parameters`) and:
- Attaches `customdata` (qubit name string) to every Plotly trace so click events can identify which qubit was clicked
- Returns a `qubit_names` list alongside `traces` and `layout`

**Experiment-to-path mapping (`app.js`):**

A static `EXPERIMENT_PATH_MAP` maps experiment names to arrays of `{axis, path}` objects:

```javascript
var EXPERIMENT_PATH_MAP = {
    'time_of_flight':         [{axis: 'x', path: 'qubits.{name}.resonator.time_of_flight'}],
    'resonator_spectroscopy': [{axis: 'x', path: 'qubits.{name}.resonator.f_01'}],
    'qubit_spectroscopy':     [
        {axis: 'x', path: 'qubits.{name}.f_01'},
        {axis: 'x', path: 'qubits.{name}.xy.RF_frequency'},
    ],
    'qubit_spectroscopy_vs_flux': [
        {axis: 'x', path: 'qubits.{name}.z.joint_offset'},
        {axis: 'y', path: 'qubits.{name}.xy.RF_frequency'},
        {axis: 'y', path: 'qubits.{name}.f_01'},
    ],
};
```

`_resolveExperimentPath(experimentName, qubitName)` substitutes `{name}` with the actual qubit name and returns the resolved paths. Supports fuzzy matching (substring) when exact key is not found.

**Click handler (`app.js`):**

`_attachPlotClickHandler(plotDiv)` binds `plotly_click` events. On click:

1. Extract `x`, `y`, `z` from the clicked point
2. Copy coordinate text to clipboard via `navigator.clipboard`
3. Resolve qubit name: `pt.customdata` > `pt.data.name` > single-qubit fallback
4. Resolve experiment name from `#ds-detail-root[data-experiment]`
5. Call `_autoUpdateFields(mappings, pt)` to POST edits
6. Call `_navigateToExplorerPath(dotPath)` to switch tabs

**Auto-update flow (`app.js`):**

`_autoUpdateFields(mappings, pt)`:
1. Reads the state folder path from `#load-path-input`
2. Ensures the correct state is active via `POST /load`
3. Chains sequential `POST /field/edit` requests (one per mapping)
4. Refreshes the pending tray from the response's `tray_html`
5. Shows a success toast with the updated field names and values

**Explorer tree navigation (`app.js`):**

`_navigateToExplorerPath(dotPath)`:
1. Re-activates the user's state folder via `POST /load`
2. Loads the Explorer view via `htmx.ajax('GET', '/explorer', ...)`
3. Waits for the tree to render (polling with `setTimeout`)
4. Calls `_expandTreeToPath(containerId, dotPath)`

`_expandTreeToPath(containerId, dotPath)`:
1. Splits the dot-path into segments (e.g. `qubits.q4.resonator.f_01`)
2. Walks each segment, expanding lazy nodes by clicking their toggle
3. Highlights the target node with a yellow background (`tree-highlight` class)
4. Adds a pulsing orange badge ("Update **fieldName**") next to the target
5. Scrolls the target into view with smooth scrolling
6. Auto-removes highlight and badge after 8 seconds

**Figure card qubit buttons (`_dataset_detail.html`):**

Each figure card now shows per-qubit edit buttons (e.g. `[q0] [q1]`) that navigate directly to the Explorer for that qubit's experiment field. Uses `_resolveExperimentPath()` and `_navigateToExplorerPath()`.

### Key files

| File | Changes |
|------|---------|
| `dataset.py` | Pass `parameters` to `_build_plotly_figure()`; add `customdata` to traces; return `qubit_names` |
| `app.js` | +`EXPERIMENT_PATH_MAP`, +`_resolveExperimentPath()`, +`_attachPlotClickHandler()`, +`_autoUpdateFields()`, +`_showPlotClickToast()`, +`_navigateToExplorerPath()`, +`_expandTreeToPath()` (~300 lines) |
| `style.css` | +`.tree-highlight`, +`.tree-edit-popup`, +`@keyframes popup-pulse`, +`.figure-qubit-actions` |
| `_dataset_detail.html` | +per-qubit edit buttons on figure cards |

---

## Feature 2: IF-to-RF Frequency Axis Conversion

### Problem

HDF5 experiment data often stores frequency coordinates in the intermediate-frequency (IF) range (e.g. 50-400 MHz), but researchers think in terms of the full RF frequency (IF + LO, typically 4-8 GHz). Plot axes showing IF values are confusing.

### Solution

New static method `DatasetStore._apply_full_freq(coords, parameters)`:

1. Searches experiment `parameters` for `LO_frequency`, `lo_frequency`, or `LO_freq`
2. If found and LO >= 1 GHz, scans coordinate arrays for frequency-like names (`freq`, `f_`, `if` in the key name)
3. If all values in the coordinate are < 1 GHz (indicating IF range), adds the LO offset to each value
4. Returns modified `coords` dict (original is not mutated)

Called at the top of `_build_plotly_figure()` before any trace construction.

### Key files

| File | Changes |
|------|---------|
| `dataset.py` | +`_apply_full_freq()` static method (~30 lines); called from `_build_plotly_figure()` |

---

## Feature 3: Pending Tray Moved to Topbar

### Problem

The pending changes tray was docked at the bottom of the sidebar. On screens with many workspace folders, it was often scrolled out of view and easy to miss.

### Solution

Moved the `{% include '_pending_tray.html' %}` from the sidebar `<aside>` to a new `<li id="topbar-tray-slot">` in the topbar `<ul>`. Restyled as a compact floating dropdown:

- Bar: rounded border, no `border-top`, inline with topbar
- Drawer: absolutely positioned below the bar with `z-index: 101`, box shadow, rounded corners
- Removed the standalone "Save All" button from the topbar (now lives inside the tray)

### Key files

| File | Changes |
|------|---------|
| `base.html` | Moved `_pending_tray.html` include from sidebar to `#topbar-tray-slot`; removed standalone Save All button |
| `style.css` | Restyled `.tray-bar` (rounded, inline), `.tray-drawer` (absolute dropdown), `#pending-tray` (relative positioning) |
| `test_web.py` | Updated `TestSaveFlow` -- Save All now requires a pending change to be visible; removed "disabled when no changes" test |

---

## Feature 4: Sidebar Nav Reorganization

### Problem

The sidebar nav had all links in one flat list: Explorer, Table, Instrument, Diff, Datasets, Trends. The Load form was below the nav. This mixed state-editing views (Explorer/Table) with analysis views (Datasets/Diff/Trends).

### Solution

Split into two groups with a visual divider:

```
[Explorer] [Table] [Instrument Wiring]
                                        <-- Load form
------- sidebar-section-divider -------
[Datasets] [Diff] [Trends]
                                        <-- Workspace folder list
```

The Load form sits between the two nav groups, making it more prominent for state-editing workflows.

### Key files

| File | Changes |
|------|---------|
| `base.html` | Split nav into two `<nav>` blocks with `<hr class="sidebar-section-divider">` between; moved Load form between them |
| `style.css` | +`hr.sidebar-section-divider` rule |

---

## Feature 5: Load-Path localStorage Persistence

### Problem

Users had to re-type or re-paste the `quam_state` folder path every time they opened the app. The workspace path was persisted but the load path was not.

### Solution

On page load, `#load-path-input` restores its value from `localStorage.getItem("quam_load_path")`. On form submit, the current value is saved via `localStorage.setItem("quam_load_path", ...)`.

Also added `hx-include="#sidebar-filter-input"` to the `#sidebar-tree` element so filter state is preserved across HTMX tree reloads.

### Key files

| File | Changes |
|------|---------|
| `base.html` | +localStorage save/restore for `#load-path-input` (~15 lines); +`hx-include` on `#sidebar-tree` |

---

## Feature 6: Gate Fidelity -- 2Q RB Panels

### Problem

The Chip Status dashboard showed 1Q gate fidelity metrics (RB avg, x180, x90) as per-qubit heatmap panels, but 2Q Randomized Benchmarking data (StandardRB, InterleavedRB) -- which already existed in `state.json` under `qubit_pairs.{pair}.macros.{gate}.fidelity` -- was only visible as small inline text in pair summary cells. Researchers needed dedicated visualization panels to compare 2Q RB fidelity across all pairs at a glance.

### Solution

Added a new **"Gate Fidelity -- 2Q RB"** section to the Chip Status tab (Section 5, right after the Distribution Histograms). This replaced the old "Pair CZ Fidelity" section which showed less structured data.

**Section structure:**

```
Gate Fidelity -- 2Q RB
  Standard RB
    [flattop]   topology pair grid + bar chart
    [unipolar]  topology pair grid + bar chart
  Interleaved RB
    [flattop]   topology pair grid + bar chart
    [unipolar]  topology pair grid + bar chart
```

**No backend changes needed.** The existing `_extract_pair_gate_fidelities()` in `query.py` already extracts StandardRB/InterleavedRB entries into each edge's `gate_fidelities` array as `{"gate": "cz_flattop", "metric": "StandardRB", "value": 0.55}`.

### How it works

**Data collection (`_wiring.html`):**

`build2QRBPanels()` filters `topo.edges[].gate_fidelities` for entries where `metric === "StandardRB"` or `metric === "InterleavedRB"`, grouping into:

```javascript
rbData = {
    "StandardRB":    { "cz_flattop": [{pair_id, source, target, value}, ...], ... },
    "InterleavedRB": { "cz_flattop": [...], ... }
}
```

Returns early if no RB data exists (section stays hidden).

**Pair grid positioning (doubled-coordinate scheme):**

Pairs are positioned at the midpoint of their two constituent qubits on the chip topology grid. Since qubit `gridPositions` are 0-based integers (already row-flipped), the pair midpoint in doubled coordinates is simply:

```javascript
var mc = sp.col + tp.col;  // source.col + target.col
var mr = sp.row + tp.row;  // source.row + target.row
```

This works because:
- Horizontal pair between col 0 and col 1: doubled col = 1 (odd = between qubits)
- Vertical pair between row 0 and row 1: doubled row = 1 (odd = between qubits)
- Qubit positions would be at even indices (0, 2, 4...) in the doubled space

CSS grid template uses these doubled coordinates directly: `grid-column: mc + 1; grid-row: mr + 1`.

**Panel rendering:**

Iterates `["StandardRB", "InterleavedRB"]`, then each gate name within. For each combination:
- Group heading when RB type changes (e.g. `<h4>Standard RB</h4>`)
- Panel title: gate name with `cz_` prefix stripped + stats line (avg/min/max/count)
- Topology pair grid: heatmap cells with GnBu color scale, positioned by doubled-coordinates
- Horizontal bar chart via `_plotlyRender()` -- sorted descending, percentage annotations, height matched to grid

**Click handlers:** `.heatmap-cell[data-pair]` opens the pair detail inspector via `htmx.ajax('GET', '/pair/' + pid, ...)`.

**Shared grid positions:** The `gridPositions` / `gridCols` / `gridRows` / `hasGrid` computation was hoisted from `buildMetricPanels()` into the parent scope so both the 2Q RB panels and the per-metric qubit panels can share it.

**Live-reload diff:** Added `gate_fidelities` comparison to `computeTopoDiff()` so the "State files changed on disk" banner fires when 2Q RB values change. Uses `JSON.stringify()` comparison of the arrays.

**Diff highlighting:** Updated `app.js` diff-highlight code to target `#topo-2q-rb-panels .heatmap-cell[data-pair]` instead of the removed `.topo-pair-cell`.

### What was removed

The old **Pair CZ Fidelity** section (Section 5) was fully removed:
- HTML container (`#topo-pair-section`, `#topo-pair-grid`, `#topo-pair-chart`)
- JS `buildPairSummary()` IIFE (~80 lines)
- CSS classes: `.topo-pair-grid`, `.topo-pair-cell`, `.topo-pair-cell-name`, `.topo-pair-cell-value`, `.topo-pair-gate-info`, `.topo-pair-gate-row`, `.topo-pair-gate-label`, `.topo-pair-confusion`, `.topo-pair-chart`
- CSS variables: `--topo-pair-name-size`, `--topo-pair-value-size`, `--topo-pair-gate-info-size`, `--topo-pair-confusion-size`

### Key files

| File | Changes |
|------|---------|
| `_wiring.html` | +`#topo-2q-rb-panels` container; hoisted grid positions; +`build2QRBPanels()` IIFE (~120 lines); removed `buildPairSummary()` (~80 lines); +`gate_fidelities` diff detection |
| `style.css` | +`.topo-2q-pair-grid` and child styles (~15 lines); removed old pair summary CSS (~20 lines) |
| `app.js` | Updated diff-highlight to target 2Q RB pair cells |

---

## Feature 7: Chip Status Section Reorder & Rename

### Problem

The Chip Status per-metric detail panels started with Coherence (T1, T2), then Gate & Readout Fidelity. Since 2Q RB panels were added right above, having 1Q fidelity panels separated from them by Coherence broke the logical grouping. Also, the group title "Gate & Readout Fidelity" didn't clearly indicate these were single-qubit metrics.

### Solution

**Reordered `PANEL_DEFS`** so fidelity comes first (right after 2Q RB), then coherence:

```
Section 5: Gate Fidelity -- 2Q RB
  Standard RB (per macro)
  Interleaved RB (per macro)

Section 6: Per-Metric Detail Panels
  1Q RB & Readout Fidelity    <-- was "Gate & Readout Fidelity", moved up
    Gate Fidelity -- RB avg
    Gate Fidelity x180
    Gate Fidelity x90
    IQ Blob                    <-- was "Assignment Fidelity -- IQ Blob"
    Readout Fidelity |g>
    Readout Fidelity |e>
  Coherence                    <-- was first, now second
    T1, T2 Ramsey, T2 Echo
  Frequencies
  Calibration
```

**Renamed:**
- Group label: `"Gate & Readout Fidelity"` --> `"1Q RB & Readout Fidelity"`
- Panel title: `"Assignment Fidelity -- IQ Blob (%)"` --> `"IQ Blob (%)"`

### Key files

| File | Changes |
|------|---------|
| `_wiring.html` | Reordered `PANEL_DEFS` array (fidelity first); renamed group label and IQ Blob title |

---

## Current stats (post-changes)

| Metric | Value |
|--------|-------|
| Tests | 679 passing, 1 skipped |
| Test files | 14 |
| Application code | ~7,500 lines |
| Test code | ~7,200 lines |
| Routes | 60 |
| Templates | 46 (14 full pages + 32 partials) |
| `app.js` | 4,188 lines |
| `style.css` | 2,714 lines |
| `routes.py` | 2,219 lines |
| `_wiring.html` | 1,288 lines |
| `dataset.py` | 1,132 lines |
