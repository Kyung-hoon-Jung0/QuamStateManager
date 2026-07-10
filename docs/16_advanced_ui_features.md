# Advanced UI Features -- Progress Log

> Picks up where `15_web_redesign_progress.md` left off (after Folder Browser and Path Autocomplete).
> Covers all UX improvements, the JSON Tree Viewer, the Explorer view, and the unified Full Compare tab.

---

## Summary table

| # | Feature | Status | Tests |
|---|---------|--------|-------|
| 1 | UX polish (remove `+` button, fix `x` button, sidebar filter) | **Done** | +8 tests |
| 2 | "Diff Only" value readability + Trend Tracker grouped selector | **Done** | +4 tests |
| 3 | Trend Tracker UX overhaul (chart, legend, hover tooltip) | **Done** | +8 tests |
| 4 | Active badge + Load form restore | **Done** | +4 tests |
| 5 | Multi-tag sidebar filter | **Done** | +5 tests |
| 6 | Diff page UX (browse buttons, nav sync, headers, legend, delta column) | **Done** | +6 tests |
| 7 | Recent folders in browser | **Done** | +1 test |
| 8 | JSON Tree Viewer (Explorer + Compare State integration) | **Done** | +8 tests |
| 9 | Bug fixes (tab title, search icon overlap, tree depth) | **Done** | 0 regressions |
| 10 | Full Compare tab (unified tree + Diff Only + Ref Data) | **Done** | +8 new tests |
| 11 | Pending Changes Tray (replaces floating Changes dropdown) | **Done** | +11 tests |

**Total: 195 tests passing, 0 regressions.**

---

## Feature 1: UX Polish (post-Folder Browser)

### What was fixed

**Remove redundant `+` button.** After adding the folder browser "Select" button that auto-submits the form, the explicit `+` submit button became redundant. Replaced with a `hidden-submit` `<input>` so Enter still works but no button is visible.

**Fix `x` button (remove workspace root).** Windows paths like `<data-root>` contain backslashes that broke JSON escaping in HTMX `hx-vals` attributes. The path was being passed as raw string without escaping. Fix: applied `| string | tojson` Jinja2 filter in `_sidebar_tree.html` so every path in `hx-vals` is double-escaped correctly.

**Browse dialog UX.**
- "Select" button and path display moved to the **top** of the dialog (above the breadcrumbs) so it's always visible without scrolling.
- Fixed `..` (up) navigation from drive roots on Windows: `Path("D:\\").parent == Path("D:\\")` (no change), so the backend now returns `parent: ""` for drive roots. The JS was updated to navigate to `""` (computer root) in that case.
- Fixed ".." disappearing: condition changed from `if (data.parent)` to `if (data.path)` so the entry always shows when you're inside any folder, even if `parent` is an empty string.

**Deep experiment folder detection.** The "Contains experiment subfolders" badge in the browser was only checking the immediate children. Replaced with `_has_experiment_descendant()` using `os.walk` up to `max_depth=4`, so deeply nested `quam_state/` folders are detected correctly.

**Sidebar filter.** Typing in the filter box (e.g. "2025") had no effect. Implemented `_filter_tree()` in `routes.py` that AND-matches all whitespace-separated tokens against experiment name, date string, and status. Applied server-side before rendering `_sidebar_tree.html`.

### Key files

| File | What changed |
|------|-------------|
| `web/templates/base.html` | `+` → `hidden-submit`; Load form always visible; Browse dialog "Select" moved to top |
| `web/templates/_sidebar_tree.html` | `hx-vals` JSON escaping with `tojson` filter |
| `web/routes.py` | `_has_experiment_descendant()` using `os.walk`; `_filter_tree()` for sidebar |
| `web/static/app.js` | Fixed `..` navigation logic; `selectBrowserFolder()` auto-submits |

---

## Feature 2: Diff Value Readability + Trend Tracker Grouped Selector

### What was done

**Diff delta readability.** The `(+0.013e9)` numeric delta shown in comparison tables was too small and blended with the value. Updated `.diff-delta` CSS to render as a distinct block-level element with larger font, bold weight, and color-coding (green for positive, red for negative).

**Trend Tracker property selector.** Previously showed all properties in a flat list. Redesigned to use the same grouped layout as the Comparison Table (Identity, Frequencies, Coherence, XY Drive, Readout, Flux, Gate Fidelity sections), each with a group-level toggle checkbox. Functions `trendToggleAll`, `trendToggleGroup`, `trendSyncGroup` added to `app.js`.

### Key files

| File | What changed |
|------|-------------|
| `web/static/style.css` | `.diff-delta` block display, larger font, color-coded |
| `web/templates/_trend_picker.html` | Grouped selector replacing flat list |
| `web/static/app.js` | `trendToggleAll`, `trendToggleGroup`, `trendSyncGroup` |

---

## Feature 3: Trend Tracker UX Overhaul

### What was fixed

1. **"Show Chart" required two clicks.** The first click would toggle the button label to "Update Chart" but not show the chart. Root cause: the HTMX form wasn't triggering on the first click when the chart area was empty. Fixed by initializing the chart area container on page load and using `hx-trigger="load, submit"`.

2. **Selector disappeared after showing chart.** The property/qubit selector was being replaced by the chart HTML. Fixed by making the chart render into a separate `#trend-chart-area` div below the selector, preserving the form.

3. **X-axis readability.** Long experiment names (e.g. `#10693_08_qubit_spectroscopy_000845`) made the x-axis unreadable. Solution: assigned short symbols (E1, E2, E3...) on the x-axis; added a collapsible "Experiment Legend" table below the chart mapping each symbol to its full label and date.

4. **Chronological ordering.** Experiments are now sorted by `run_id` (the leading `#NNNNN` in the folder name) in `trend_chart()` route so the trend reads left-to-right chronologically.

5. **Hover tooltip artifact.** Hovering on a Plotly data point showed a "blue line with an arrow" instead of a clean tooltip. Root cause: default Plotly spike lines. Fixed by setting `showspikes: false` on the x-axis and `hovermode: 'closest'` with a custom `hovertemplate` in `_trend_chart.html`.

### Key files

| File | What changed |
|------|-------------|
| `web/routes.py` | `trend_chart()`: sort by `run_id`, pass legend data |
| `web/templates/_trend_picker.html` | Separate chart area div; `hx-trigger="load, submit"` |
| `web/templates/_trend_chart.html` | Collapsible legend table; Plotly config (hovermode, showspikes, hovertemplate) |

---

## Feature 4: Active Badge + Load Form Restore

### What was fixed

**Active badge location.** The folder name badge (e.g. `superconducting`) was being shown at the very top of the page. Moved it below the main content area header for better visual hierarchy.

**Missing Load form.** The Load input for a single quam state file disappeared after a previous refactor. Restored it as always-visible in the sidebar with its own "Browse" button and autocomplete, functioning independently of the workspace selection.

### Key files

| File | What changed |
|------|-------------|
| `web/templates/base.html` | Active badge moved below page title; Load form restored as always-visible |

---

## Feature 5: Multi-Tag Sidebar Filter

### What was added

The sidebar filter previously supported only a single text value. Updated to support **multiple filter terms** with AND logic, displayed as visual tag pills.

**Behavior:**
- Typing `2025-03-18 iq_blob` filters experiments matching BOTH terms simultaneously.
- Each space-separated token becomes a visual pill below the input with an `×` to remove it.
- Tag pills are rendered by `renderFilterTags()` in `app.js`.
- The underlying filter logic (server-side `_filter_tree()`) already supported AND matching -- only the UI needed updating.

### Key files

| File | What changed |
|------|-------------|
| `web/templates/base.html` | Multi-tag filter input; `.filter-tags` container below input |
| `web/static/app.js` | `renderFilterTags()` function: parses tokens, renders pills with × buttons |
| `web/static/style.css` | `.filter-tags`, `.filter-tag` pill styles |

---

## Feature 6: Diff Page UX Improvements

### What was improved

1. **Browse buttons on State A / State B inputs.** Both path inputs on the 2-Way Diff page now have folder browse buttons and autocomplete, identical to the workspace and load inputs.

2. **Nav link "Diff" stuck active.** The "Diff" sidebar link was always shown as active regardless of the current page. Root cause: HTMX history pushes don't re-run the page's JS. Fixed by listening to `htmx:pushedIntoHistory` and syncing the active class based on `window.location.pathname`.

3. **Renamed "Old Value" / "New Value" headers** to "State A" / "State B" for clarity.

4. **Full path legend.** Added a `.diff-path-legend` block below the form that displays the full filesystem paths of State A and State B, so the user can always tell which is which.

5. **Removed redundant "Compare" button.** The diff form already has a submit button; the extra "Compare" button at the top-right was redundant and removed.

6. **Delta column.** Added a "Delta" column to the diff table showing numeric differences with the same `render_delta` macro used in the Compare view (color-coded: green/red/gray).

### Key files

| File | What changed |
|------|-------------|
| `web/templates/_diff.html` | Browse buttons; renamed headers; `.diff-path-legend`; Delta column; removed Compare button |
| `web/static/app.js` | `htmx:pushedIntoHistory` listener for nav sync |
| `web/static/style.css` | `.diff-path-legend` styles |

---

## Feature 7: Recent Folders in Browser

### What was added

The folder browser dialog now shows a **"Recent"** collapsible section listing up to 10 previously selected folder paths. Clicking a recent path navigates the browser directly to that folder.

**Implementation:** Paths are stored in `localStorage` under the key `recentFolders` (array, max 10, most recent first). When `selectBrowserFolder()` is called, the selected path is prepended to the list. The recent list is rendered by `_renderRecentFolders()` on dialog open.

### Key files

| File | What changed |
|------|-------------|
| `web/templates/base.html` | `<details id="browser-recent">` section in dialog |
| `web/static/app.js` | `_getRecentFolders()`, `_addRecentFolder()`, `_renderRecentFolders()` |
| `web/static/style.css` | `.browser-recent`, `.browser-recent-list`, `.browser-recent-item` styles |

---

## Feature 8: JSON Tree Viewer

### What was added

A completely new **JSON Tree Viewer** component replaces all previous curated property views. It renders every single key and value from `state.json` and `wiring.json` in an interactive collapsible tree -- nothing omitted.

### Design

**UI elements:**
- File tabs (`state.json` / `wiring.json`) to switch between files
- Toolbar: magnifying glass icon + search input, depth control buttons (0, 1, 2, 3, All)
- Interactive tree: expandable nodes with `▶`/`▼` toggles, colored values by type
- Click any key to copy its full dot-path to clipboard (with brief "copied" flash)

**Value coloring:**

| Type | Color | Example |
|------|-------|---------|
| String | Green | `"finished"` |
| Number | Blue | `6.25e9` |
| Boolean | Orange | `true` |
| Null | Muted/italic | `null` |
| Pointer | Purple | `#/qubits/qA1/anharmonicity` |

**Search:** Typing in the search box instantly highlights matching nodes (yellow background), hides non-matching leaf nodes, and auto-expands all ancestor nodes of matches.

**Diff mode:** When `refData` is passed, nodes where the value differs from the reference get a yellow row background (`.tree-diff`), and numeric differences show a delta annotation.

**Tree indentation fix:** Indentation was originally done with inline `paddingLeft = depth * 18px`. Since nodes are nested in the DOM, this compounded quadratically -- at depth 5, the actual indent was 270px making deep nodes unreachable. Fixed by removing the inline padding and using CSS `margin-left: 18px` on `.tree-node[data-depth]:not([data-depth="0"])`, which indents linearly (18px per level, not compounded).

### Explorer route (`/explorer`)

`GET /explorer` serves a full-page view with both `state.json` and `wiring.json` trees. `POST /load` now redirects to `/explorer` instead of the old qubit list. The "Explorer" link was added to the sidebar nav.

### Compare State integration

Each per-state tab in the "Compare Selected" view now shows a "Full State & Wiring" `<details>` section at the bottom, rendering the JSON tree in **diff mode** with `refData` set to the reference state's JSON. Nodes that differ from the reference are highlighted in yellow with delta annotations.

### Key files

| File | What changed |
|------|-------------|
| `web/routes.py` | `GET /explorer` route; `load()` redirect to `/explorer`; `compare_state()` passes `state_json`, `wiring_json`, `ref_state_json`, `ref_wiring_json` |
| `web/templates/explorer.html` | New full-page wrapper |
| `web/templates/_explorer.html` | HTMX partial: tabs, toolbar, tree containers, inline script |
| `web/templates/_compare_state.html` | Section 4: Full State & Wiring with JSON tree in diff mode |
| `web/static/app.js` | `renderJsonTree()`, `_buildNode()`, `_toggleNode()`, `_searchTree()`, `_expandToDepth()`, `jsonTreeExpandToDepth()`, `jsonTreeCollapseAll()`, `jsonTreeExpandAll()`, `jsonTreeSearch()` |
| `web/static/style.css` | `.json-tree`, `.tree-node`, `.tree-row`, `.tree-toggle`, `.tree-key`, `.tree-val-*`, `.tree-diff`, `.tree-delta`, `.tree-highlight`, `.tree-search-hidden`, `.tree-toolbar`, `.tree-file-tabs`, `.tree-file-tab` |

### Tab title bug + search icon overlap

Two rendering bugs were found and fixed:

- **Tab title disappearing:** `<button>` elements for the file tabs were being overridden by Pico CSS button styles even with `all: unset`. Fix: switched from `<button>` to `<span>` elements (which Pico CSS doesn't target with button rules).
- **Search icon overlapping with text:** `input[type="search"]` has a browser-native magnifying glass icon rendered inside the input box, overlapping typed text. Fix: replaced with `type="text"` + a separate `<span class="tree-search-icon">` before the input, and used `appearance: textfield` to suppress the native icon.

---

## Feature 9: Full Compare Tab (Unified Tree Viewer)

### What was added

A new **"Full Compare"** tab in the Multi-State Comparison view (after "Differences", before per-state tabs) that renders a single unified JSON tree merging all selected states side-by-side.

### Visual design

For **identical values** across all states (renders once, no highlighting):
```
  frequency: 6.25e9
```

For **differing values** (inline colored badges per state):
```
  frequency: [A] 6.25e9  [B] 6.30e9
```

For **keys missing in some states**:
```
  new_param: [A] 0.5  [B] --
```

State badges (A, B, C...) use distinct colors: blue, green, orange, purple, teal, etc.

### Toolbar controls

| Control | Function |
|---------|----------|
| Search | Highlights matching keys/values, auto-expands ancestors |
| Depth 0/1/2/3/All | Collapse/expand the entire tree to a depth level |
| **Diff Only** | Hides identical nodes; auto-expands ancestors of diff nodes; re-applies on toggle |
| **Ref Data** | Opens a dropdown listing all states with full paths; selecting one adds delta annotations |

### Ref Data (reference selector with deltas)

With a reference selected (e.g. "Ref: A"), every differing numeric value shows:
```
  frequency: [A] 6.25e9 (REF)  [B] 6.30e9 (+0.05e9 ↑)
```

| Annotation | Meaning | Color |
|------------|---------|-------|
| `(REF)` | This is the reference value | Blue badge |
| `(+delta ↑)` | Higher than reference | Green |
| `(-delta ↓)` | Lower than reference | Red |
| `(= ↔)` | Same value, non-numeric | Gray |

Re-rendering is **purely client-side**: the datasets array is cached on the DOM element, so switching reference states is instant without a server round-trip.

### Architecture

```
User clicks "Full Compare" tab
  → HTMX GET /compare/full?paths=...
  → Server loads all stores, serializes state + wiring as JSON
  → Template renders datasets_json, legend, toolbar, tree containers
  → renderUnifiedTree() called for state.json and wiring.json trees
  → _buildUnifiedNode() walks union of all keys recursively
  → Identical values: single value display
  → Differing values: .tree-multi-val with per-state badges
  → .tree-diff + .tree-has-diff classes set for Diff Only mode
  
User clicks "Diff Only"
  → toggleUnifiedDiffOnly(containerId) adds diff-only-active class
  → Non-diff leaf nodes hidden, diff ancestors auto-expanded

User clicks "Ref Data" → selects state B
  → setUnifiedRef(1) re-renders both trees with refIndex=1
  → Reference badge + delta annotations added to all differing numeric values
```

### Key files

| File | What changed |
|------|-------------|
| `web/routes.py` | `GET /compare/full`: loads stores, serializes all state+wiring dicts, returns `_compare_full.html` |
| `web/templates/_compare_tabs.html` | "Full Compare" tab button added between "Differences" and per-state tabs |
| `web/templates/_compare_full.html` | New template: legend, file tabs, toolbar with Diff Only + Ref Data, tree containers |
| `web/static/app.js` | `renderUnifiedTree()`, `_buildUnifiedNode()`, `_mergeKeys()`, `_allEqual()`, `toggleUnifiedDiffOnly()` (all in new IIFE) |
| `web/static/style.css` | `.compare-legend`, `.tree-state-badge` (8 colors), `.tree-multi-val`, `.tree-val-missing`, `.tree-ref-tag`, `.tree-delta-up/down/same`, `.diff-only-hidden`, `.ref-dropdown`, `.ref-dropdown-item` |
| `tests/test_web.py` | 8 new tests in `TestCompare`: tab present, route, legend, diff-only toggle, ref-data button/dropdown, file tabs, error handling, embedded data |

---

## Feature 11: Pending Changes Tray

### What was changed

Replaced the floating `#changes-panel` dropdown (z-index 99, absolute-positioned below topbar) with a **persistent amber tray** docked between `#content-area` and `#status-bar` inside `#main`.

**Before:** A "Changes (N)" button in the topbar opened a floating overlay. Easy to miss, overlaid content, disconnected from the Save action.

**After:** When edits exist, an amber bar appears at the bottom of the main area showing "● N unsaved change(s)" plus a "▼ Review" toggle and a "Save All" button. Clicking Review slides open a drawer with the full changes table and per-row Discard buttons.

### Architecture

```
#main
  #content-area     ← unchanged (Split.js panes)
  #pending-tray     ← NEW: docked bottom bar (hidden when empty)
    .tray-bar       ← amber bar with count + actions
    #tray-drawer    ← expands upward (max-height transition)
  #status-bar       ← unchanged (toast messages)
```

The tray is a stable DOM element (included in `base.html`, never swapped away by navigation). It updates via HTMX out-of-band (OOB) swaps: responses from `qubit_edit` and `/save` append a `hx-swap-oob="outerHTML:#pending-tray"` fragment. The `/discard` route returns the full tray directly (buttons target `#pending-tray` with `hx-swap="outerHTML"`).

### Key changes

| File | What changed |
|------|-------------|
| `web/templates/_pending_tray.html` | **New** — tray fragment; `oob` flag injects `hx-swap-oob` attribute |
| `web/templates/base.html` | Removed `#changes-panel` div + "Changes" `<li>` button; added `{% include '_pending_tray.html' %}`; added `htmx:afterSettle` listener for `_restoreTrayState` |
| `web/routes.py` | Added `_tray_oob()` helper; `qubit_edit` appends OOB tray on success; `save` returns toast + OOB tray; `discard` returns full `_pending_tray.html` |
| `web/static/style.css` | Removed `.changes-panel`/`.changes-hidden`/`.btn-show-changes`; added `.tray-bar`, `.tray-drawer`, `.tray-review-btn`, `.tray-indicator`, `.tray-actions`, `.tray-expanded` |
| `web/static/app.js` | Removed `toggleChanges`; added `togglePendingTray` (expand/collapse + sessionStorage) and `_restoreTrayState` (called on `htmx:afterSettle`) |
| `tests/test_web.py` | Added `synth_client`/`synth_qubit` fixtures; `TestPendingChangesTray` (11 tests); updated 2 stale tests in `TestSaveFlow` |

### Drawer open state persistence

The tray open/closed state is stored in `sessionStorage` under `quam_tray_open`. The `htmx:afterSettle` event fires after every OOB swap, calling `_restoreTrayState()` to re-apply the expanded class so the drawer stays open across edits.

---

## Current test count

```
195 tests passing, 0 regressions
```

Tests run in ~5s against synthetic fixtures. The `TestRealData` class also runs against actual ExampleChip data when available.

---

## Running the web server

```bash
cd <project-root>
python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5000)"
```

Then open `http://localhost:5000`.

---

## Sidebar layout reorder (Task 30)

The sidebar was reordered based on researcher workflow priorities:

1. **Bookmarks** (top) — most-accessed items visible first
2. **Navigation tabs** — Explorer through Trends
3. **QUAM state folder path + Load** — occasionally used
4. **Workspace folder list** (bottom, collapsible) — large tree that can be collapsed when not needed

Previously: Workspace was at top, pushing bookmarks and navigation below the fold on smaller screens.

## Chip Status dashboard (Tasks 28-31)

The `/topology` page (renamed "Chip Status", first item in sidebar nav) was redesigned from a sparse Plotly scatter graph into a rich, scrollable dashboard. For full details see [`11_templates.md`](11_templates.md) (Chip Topology dashboard section).

Key features relevant to the advanced UI:
- All qubit metrics visible at a glance (never behind hover/tooltips)
- T1/T2 displayed in μs, fidelities as percentages (e.g. 99.09 not 0.9909)
- Enriched pair data: searches all gate macros for fidelity values, extracts NxN confusion matrix off-diagonals
- Live file-change detection: polls `state.json`/`wiring.json` mtime every 3s, shows diff overlay on reload
- Popup overlays for "... more" qubit details and pair edge labels (not inline expand)
- Unified GnBu heatmap color scale across all views

### Fully parameterized styling

All topology fonts, dimensions, and spacing are controlled by:
- **CSS**: `--topo-*` custom properties in `:root` (style.css) — ~35 variables with descriptive comments
- **JS**: `UI_CONFIG.plotly.topology.layout` in app.js — card width, row height, spacing, padding

Change one variable to resize the entire dashboard. To scale everything up/down uniformly, multiply all `--topo-*` values and `layout.*` values by the same factor (e.g. 1.25 for 25% larger).

## Inspector-panel search (qubit / pair detail)

The qubit (`/qubit/<name>`) and pair (`/pair/<name>`) inspector panes —
loaded into `#inspector-pane` by clicking ports on `/instrument`, qubits in `/qubits`,
or via the command palette — show ~40–50 rows across 7+ collapsible
`<details>` sections each. To save scrolling, a sticky in-panel search bar
filters those rows live.

Markup lives in `_inspector_header.html`, gated by
`{% if inspector_type in ('qubit', 'pair') %}` so it never shows for the
dataset inspector. The JS function `filterDetailPanel(inputEl)` in `app.js`
walks `#inspector-pane .qubit-detail, #inspector-pane .pair-detail` and:

- builds a per-row haystack from `tr.textContent` + the parent
  `<details>`'s `<summary>` text + every nested `<input>.value` (editable
  cells render as `<input>` so their typed value isn't in textContent);
- splits the query on whitespace and applies AND-semantics across tokens
  (same convention as `filterTable`);
- toggles `tr.style.display`, auto-opens any section that has at least one
  visible row, and hides sections with no matches;
- updates an "X of Y shown" counter and a clear-search × button.

The non-table auxiliary sections at the bottom of `_qubit_detail.html`
("Generated Config", "Wiring Ports") hide while a query is active so the
filtered view doesn't show unrelated panels.

`<details>` open/closed state is preserved on clear (the function only
forces `details.open = true` when there's an active query with matches).

## Known limitations / future ideas

| Area | Note |
|------|------|
| Full Compare: non-JSON fields | `data.json` and `node.json` not yet merged into the unified tree (only `state.json` + `wiring.json`) |
| Full Compare: array diffs | Arrays with different lengths show `[A]/[B] items` but don't walk mixed-length arrays well |
| Trend Tracker: multiple properties on one chart | Currently one chart per property; could overlay on one graph |
| Explorer: bookmark paths | User can't save/name frequently visited states beyond recent history |
| Tree viewer: copy subtree | Could add right-click / button to copy a subtree as JSON |
