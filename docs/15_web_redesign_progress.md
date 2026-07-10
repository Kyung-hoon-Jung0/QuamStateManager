# Web UI Redesign -- Progress Log

> Tracks what has been done, what remains, and notes for anyone picking this up.

## Status: 13 of 13 tasks complete -- ALL DONE

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Multi-context backend model | **Done** | `app.py` + `routes.py` refactored |
| 2 | Stable HTMX containers | **Done** | `base.html` rewritten, all templates migrated |
| 3 | Split pane (Split.js) | **Done** | `split.min.js` downloaded, vertical split initialized, user-resizable |
| 4 | Fix global search + per-table filters | **Done** | Global search targets `#inspector-pane`, client-side filters on all 4 data tables |
| 5 | Save flow (Save All + Show Changes) | **Done** | Always-visible Save All, Changes dropdown, `/changes` + `/discard` routes |
| 6 | Edit feedback (modified cells) | **Done** | `.cell-modified` class, hover tooltip, `modified_map` from change_log |
| 7 | Cursor retention after edit | **Done** | Direct render (no redirect), `focus_path` + `focusEditInput()` |
| 8 | Generic inspector panel | **Done** | Shared `_inspector_header.html`, type badges, close button, `closeInspector()` |
| 9 | Typography / CSS overhaul | **Done** | 14px base, `--font-ui`/`--font-mono` vars, monospace values, compact padding, `data-font-size` prep |
| 10 | Sidebar (collapse + polling) | **Done** | ☰ toggle, `localStorage` persistence, 60s HTMX polling on tree |
| 11 | Font size settings | **Done** | ⚙ gear dropdown, S/M/L buttons, `setFontSize()`, `localStorage` persistence |
| 12 | Client-side JS (`app.js`) | **Done** | All functions complete: `filterTable()`, `toggleChanges()`, `focusEditInput()`, `closeInspector()`, `toggleSidebar()`, `toggleSettings()`, `setFontSize()` |
| 13 | Test backfill | **Done** | 36 new tests covering Tasks 2-11; 625 total, 0 regressions |

---

## Task 1: Multi-Context Backend Model -- DONE

### What changed

**Problem:** `app.config` held six flat keys (`active_store`, `active_engine`, `active_index`, `active_modifier`, `active_saver`, `active_path`) hardwired to a single QUAM session. Adding a future data type (HDF5, datasets) would require duplicating all these keys or a messy refactor.

**Solution:** Replaced the flat keys with a two-key context registry:

```python
app.config["contexts"] = {}           # name -> context dict
app.config["active_context"] = None   # name of the currently active context
```

Each context is a dict with at least `{"type": str, "path": str}` plus type-specific objects.

### Files modified

| File | What changed |
|------|-------------|
| `quam_state_manager/web/app.py` | Replaced 6 flat config keys with `contexts` + `active_context`. Updated module docstring. |
| `quam_state_manager/web/routes.py` | New helpers: `_active_ctx()`, `_ctx_obj()`, `_context_type()`, `_active_path()`, `_activate_context()`, `_activate_quam()`. Old `_store()` / `_engine()` / etc. now delegate to `_ctx_obj()`. `_activate_store()` replaced by `_activate_quam()` (two call sites updated). `_ctx()` now includes `context_type` in template context. Updated module docstring. |

### How it works

Routes never touch `app.config` directly. They call helpers:

```python
_store()      # -> _ctx_obj("store")    -> active context's QuamStore
_engine()     # -> _ctx_obj("engine")   -> active context's QueryEngine
_modifier()   # -> _ctx_obj("modifier") -> active context's Modifier
_context_type()  # -> "quam" (or "h5" in the future)
_active_path()   # -> filesystem path of the loaded data
```

QUAM activation:

```python
_activate_quam(folder_path)
# internally calls:
#   _activate_context(name, "quam", path, store=..., engine=..., ...)
```

Future data types plug in the same way:

```python
# Future example -- not built yet
def _activate_h5(file_path):
    h5store = H5Store(file_path)
    _activate_context(
        name=Path(file_path).stem,
        context_type="h5",
        path=str(file_path),
        store=h5store,
        engine=H5QueryEngine(h5store),
    )
```

Routes can branch on `_context_type()`:

```python
if _context_type() == "quam":
    return render_template("_qubit_detail.html", ...)
elif _context_type() == "h5":
    return render_template("_h5_dataset.html", ...)
```

### Test results

All 491 tests pass (1 skipped). Zero regressions -- the refactor is fully backward-compatible because the helper functions (`_store()`, `_engine()`, etc.) have identical signatures and return types.

### Notes for the next developer

- **`_activate_context()` is generic.** It stores arbitrary `**objects` in the context dict. The only required keys are `type` and `path`. Everything else is type-specific.

- **Context names come from the folder's parent name** (e.g. `quam_state_example_17q_chip`). If two folders share a parent name, the second activation overwrites the first. This is fine for single-user desktop use. For multi-user, context names would need to be unique (e.g. include a timestamp or hash).

- **Multiple contexts can coexist** in `app.config["contexts"]`, but only one is active at a time. A future "context switcher" UI could let users toggle between loaded contexts.

- **The old `_activate_store` function no longer exists.** If you see it in older docs or tests, it's now `_activate_quam`.

---

## Task 2: Stable HTMX Containers -- DONE

### What changed

**Problem:** The old layout had three sibling elements inside `.app-layout`: `#sidebar`, `#main`, and `#detail`. All navigation HTMX swaps targeted `#main` directly, and detail views targeted `#detail` (a right-side panel). The global search targeted `#search-results` which lived *inside* `#main` and was routinely destroyed by navigation swaps. This made it impossible to add features without breaking existing targets.

**Solution:** Restructured `#main` to contain stable sub-containers that are never themselves swapped -- only their *contents* are replaced:

```
<main id="main">                        ← never swapped
    <div id="content-area">             ← never swapped; holds the split panes
        <div id="table-pane">           ← navigation target (qubits, pairs, table, etc.)
        </div>
        <div id="inspector-pane">       ← detail/search target (qubit detail, search results)
        </div>
    </div>
    <div id="status-bar">               ← toast messages (save/undo feedback)
    </div>
</main>
```

The old `<aside id="detail">` right panel is removed entirely. Detail content now goes into `#inspector-pane` (below the table pane, not beside it), which sets up the vertical split-pane layout for Task 3.

### Target migration map

| Old target | New target | What uses it |
|------------|-----------|--------------|
| `#main` | `#table-pane` | Nav links, Load/Select forms, pagination, sort headers, diff/compare forms |
| `#detail` | `#inspector-pane` | Qubit/pair row clicks, inline edit forms, Plotly node clicks |
| `#search-results` | `#inspector-pane` | Global search input, search category tabs |
| `#status` | `#status-bar` | Save/Undo buttons, toast auto-fade script |

### Files modified

| File | What changed |
|------|-------------|
| `templates/base.html` | **Full rewrite.** Removed `<div id="search-results">`, `<div id="status">`, `<aside id="detail">`. Added `#content-area` > `#table-pane` + `#inspector-pane`, `#status-bar`. Search input targets `#inspector-pane`. Save/Undo target `#status-bar`. Toast script checks `status-bar`. Two new blocks: `{% block table_content %}` and `{% block inspector_content %}`. |
| `templates/_qubits.html` | `#main` -> `#table-pane` (chain tabs, pagination). `#detail` -> `#inspector-pane` (row clicks). |
| `templates/_qubit_detail.html` | `#detail` -> `#inspector-pane` (edit form). |
| `templates/_pairs.html` | `#detail` -> `#inspector-pane` (row clicks). |
| `templates/_search_results.html` | `#search-results` -> `#inspector-pane` (category tabs). `#detail` -> `#inspector-pane` (row clicks). |
| `templates/_wiring.html` | `#detail` -> `#inspector-pane` (row clicks, Plotly `htmx.ajax` call). |
| `templates/_table.html` | `#main` -> `#table-pane` (form, sort headers). `#detail` -> `#inspector-pane` (row clicks). |
| `templates/_diff.html` | `#main` -> `#table-pane` (diff form). |
| `templates/_compare.html` | `#main` -> `#table-pane` (compare form). |
| `templates/_sidebar_tree.html` | `#main` -> `#table-pane` (workspace select, compare button). |
| `templates/qubits.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/pairs.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/table.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/wiring.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/diff.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/compare.html` | `{% block content %}` -> `{% block table_content %}` |
| `templates/qubit_detail.html` | `{% block content %}` -> `{% block inspector_content %}` |
| `templates/pair_detail.html` | `{% block content %}` -> `{% block inspector_content %}` |
| `static/style.css` | Replaced `#main` (single flex child) and `#detail` (side panel) styles with `#content-area` (vertical flex), `#table-pane` (scrollable, 50%), `#inspector-pane` (scrollable, 50%, hides when empty), `#status-bar` (fixed bottom). |

### Design decisions

1. **`#inspector-pane` hides when empty.** CSS rule `#inspector-pane:empty { display: none; }` means the inspector only appears after the user clicks a row or performs a search. Before that, `#table-pane` takes the full height.

2. **Two Jinja2 blocks instead of one.** Full-page templates now use either `{% block table_content %}` (nav pages) or `{% block inspector_content %}` (detail pages). This allows a direct URL like `/qubit/qA1` to render the detail in the correct pane.

3. **`#status-bar` is separate from `#content-area`.** Toasts appear at the very bottom of `#main`, below both panes. They don't interfere with content scrolling.

4. **No Python route changes required.** All targeting is done in the HTML templates via `hx-target` attributes. Routes return the same HTML fragments as before -- they just land in different containers now.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The test client doesn't validate HTMX targeting, but the route responses and template rendering are fully verified.

### Notes for the next developer

- **Never swap `#main`, `#content-area`, or `#status-bar` themselves.** Always swap their *children* (`#table-pane`, `#inspector-pane`). This is the core architectural rule that prevents feature interference.

- **Task 4 (Search fix) is partially done.** The global search already targets `#inspector-pane` instead of the old `#search-results`. What remains is adding per-table client-side filter inputs.

- **Task 8 (Generic inspector) is partially done.** All `hx-target="#detail"` references have been migrated to `#inspector-pane`. What remains is making the inspector panel render different content types (qubit detail, pair detail, search result detail) with a unified header/close mechanism.

- **The old `<aside id="detail">` no longer exists.** Any code (tests, scripts) that references `#detail` must be updated.

---

## Task 3: Split Pane (Split.js) -- DONE

### What changed

**Problem:** After Task 2, `#table-pane` and `#inspector-pane` were stacked vertically inside `#content-area` using CSS flexbox. Each took 50% height. Users couldn't resize them -- a critical requirement for usability (the user explicitly asked for Cursor IDE-like resizable panes).

**Solution:** Integrated Split.js v1.6.5 (6.7KB minified) to make the two panes resizable by dragging a gutter between them.

### Files added

| File | What it is |
|------|-----------|
| `static/split.min.js` | Split.js v1.6.5 minified (6,769 bytes). Lightweight, zero-dependency library for resizable split panes. |

### Files modified

| File | What changed |
|------|-------------|
| `templates/base.html` | Added `<script src="split.min.js">` tag. Replaced the simple toast-fade script with a full Split.js lifecycle manager (IIFE) that handles initialization, HTMX-aware re-initialization, and `localStorage` persistence. |
| `static/style.css` | Updated `#content-area` to `overflow: hidden`. Changed `#table-pane` from `flex: 1 1 50%` to `flex: 1 1 auto`. Replaced the `#inspector-pane:empty { display: none }` rule with a `.split-hidden` class approach (JS-controlled). Added `.gutter` and `.gutter-vertical` styles for the drag handle. |

### How it works

The Split.js lifecycle is managed by an IIFE at the bottom of `base.html`:

```
Page load → initSplit()
    ├─ inspector-pane empty?  → add .split-hidden, table-pane fills 100%
    └─ inspector-pane has content? → initialize Split.js with saved or default sizes

HTMX swap into #inspector-pane → initSplit()
    └─ Content just appeared → destroy old split, create new one, inspector becomes visible

HTMX swap into #table-pane → initSplit()
    └─ Recalculate split (inspector may or may not have content)

User drags gutter → onDragEnd saves sizes to localStorage

Next page load → loadSizes() reads from localStorage, restores user's preference
```

**Key behaviors:**

1. **Inspector hidden until needed.** On initial load, `#inspector-pane` is empty, so it gets the `split-hidden` CSS class (`display: none`). `#table-pane` takes full height. No gutter is visible.

2. **Split appears on first row click / search.** When HTMX swaps content into `#inspector-pane`, the `htmx:afterSwap` handler calls `initSplit()`. The inspector has content now, so Split.js initializes with a 55/45 default split (or the user's saved preference).

3. **Gutter is draggable.** The 6px gutter between the panes uses a `row-resize` cursor. A small visual "handle" (32px wide, 2px tall line) appears centered in the gutter. Hovering highlights it.

4. **Sizes persist across sessions.** `localStorage` key `quam_split_sizes` stores `[topPercent, bottomPercent]`. Saved on every drag end, loaded on every `initSplit()`.

5. **Destroy-before-recreate pattern.** Every `initSplit()` call first destroys the previous Split instance (cleaning up gutters and inline styles), then decides whether to create a new one. This prevents gutter duplication when HTMX swaps trigger re-initialization.

### CSS details

```css
.gutter.gutter-vertical {
    cursor: row-resize;
    height: 6px;
    background: var(--pico-muted-border-color);
}
.gutter:hover, .gutter-dragging {
    background: var(--pico-primary-background);
}
```

The gutter has a centered pseudo-element "pill" as a visual drag affordance:

```css
.gutter.gutter-vertical::after {
    content: "";
    width: 32px; height: 2px;
    background: var(--pico-muted-color);
    /* centered with absolute + transform */
}
```

### Test results

All 491 tests pass (1 skipped). Zero regressions. Split.js is a client-side-only library -- it doesn't affect server responses or template rendering, so all existing tests remain valid.

### Notes for the next developer

- **Split.js uses inline `height` styles.** When active, it sets `height: calc(55% - 3px)` (or similar) directly on the pane elements. This overrides CSS `flex` rules. When `destroy()` is called, these inline styles are removed and CSS flex takes over again.

- **The `split-hidden` class is JS-controlled, not CSS `:empty`.** We switched from `:empty` to a class because Split.js inserts a `.gutter` div as a sibling between the two panes. The `:empty` pseudo-selector wouldn't work correctly after Split.js modifies the DOM. The JS code explicitly checks `innerHTML.trim().length > 0` to determine if the inspector has real content.

- **`localStorage` key is `quam_split_sizes`.** If you need to reset a user's split preference, clear this key. The default is `[55, 45]` (55% table, 45% inspector).

- **`minSize: [80, 60]`** ensures the table pane never shrinks below 80px and the inspector never below 60px. This prevents the user from accidentally collapsing either pane to zero.

- **Split.js re-initializes on every HTMX swap to `#table-pane` or `#inspector-pane`.** This is intentional -- it ensures the split correctly recalculates after content changes (which may affect the container's available height). The destroy-before-recreate pattern keeps this clean.

---

## Task 4: Fix Global Search + Per-Table Client-Side Filters -- DONE

### What changed

**Problem:** The global search was broken (see Task 2 -- its target `#search-results` was being destroyed by navigation swaps). Additionally, researchers need instant filtering within each data table to quickly find specific qubits, pairs, or wiring rows without a server round-trip.

**Solution:** Two parts:

1. **Global search fix** -- completed in Task 2: the `hx-target` was changed from `#search-results` to `#inspector-pane`. The search category tabs were also updated. No further work needed here.

2. **Per-table client-side filters** -- added a `filterTable()` JS function and filter `<input>` elements above each of the four data tables: Qubits, Pairs, Comparison Table, and Wiring Map.

### Files added

| File | What it is |
|------|-----------|
| `static/app.js` | Client-side application logic. Contains `filterTable()` (attached to `window`). Future tasks will add `toggleSidebar()` and `setFontSize()` here. |

### Files modified

| File | What changed |
|------|-------------|
| `templates/base.html` | Added `<script src="app.js">` tag. |
| `templates/_qubits.html` | Wrapped heading in `.table-header-row` flex container. Added filter `<input>` with `oninput="filterTable(this, 'qubits-table')"`. Added `id="qubits-table"` to the `<table>`. Added filter counter `<small>`. |
| `templates/_pairs.html` | Same pattern: `.table-header-row`, filter input targeting `pairs-table`, `id="pairs-table"` on table, counter element. |
| `templates/_table.html` | Same pattern: `.table-header-row`, filter input targeting `comparison-table`, `id="comparison-table"` on table, counter element. |
| `templates/_wiring.html` | Added `.table-header-row` with `<h3>Wiring Map</h3>` and filter input targeting `wiring-table`. Added `id="wiring-table"` to the table. Counter element. |
| `static/style.css` | Added `.table-header-row` (flex row with space-between), `.table-filter` (input + counter), `.table-filter input` (styled to match the existing search box). |

### How `filterTable()` works

```javascript
filterTable(inputElement, tableId)
```

1. Reads the input value, lowercases it, splits on whitespace into terms
2. Finds the `<table>` by `tableId`, iterates `<tbody>` rows
3. For each row, concatenates all `<td>` text content (lowercased)
4. All terms must appear somewhere in the row text (AND matching)
5. Non-matching rows get `display: none`
6. Updates a counter element (`{tableId}-filter-count`) showing "X of Y shown"

**Key characteristics:**
- **Zero latency** -- pure DOM manipulation, no server calls
- **AND matching** -- typing "qA1 0.99" shows only rows containing both "qA1" and "0.99"
- **Case insensitive** -- "qa1" matches "qA1"
- **Works on any column** -- ID, frequency, fidelity, grid location, etc.
- **Counter feedback** -- shows "12 of 50 shown" so the user knows how many rows match

### Layout pattern

Each filterable table now has this structure:

```html
<div class="table-header-row">
    <h2>Qubits <small class="muted">(50)</small></h2>
    <div class="table-filter">
        <input type="search" placeholder="Filter qubits..."
               oninput="filterTable(this, 'qubits-table')">
        <small id="qubits-table-filter-count" class="muted"></small>
    </div>
</div>

<table id="qubits-table" class="data-table" role="grid">
    ...
</table>
```

The `.table-header-row` is a flex container that puts the heading on the left and the filter on the right, on the same line. It wraps on narrow screens.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The filter is entirely client-side JS -- server responses are unchanged.

### Notes for the next developer

- **`app.js` is the home for all future client-side functions.** Tasks 10 (sidebar collapse) and 11 (font size settings) will add `toggleSidebar()` and `setFontSize()` to this file. The file uses `window.functionName` assignment so functions are globally accessible from inline event handlers.

- **Filter inputs use `type="search"`.** This gives browsers a built-in clear button (the "x") for free. No extra JS needed.

- **The filter counter element ID convention is `{tableId}-filter-count`.** The JS looks for it automatically. If the element doesn't exist, the counter just doesn't show -- no error.

- **The qubit detail inspector panel has no filter.** Its property table is already sectioned by category (`<details>` sections), so filtering would be redundant. If needed later, the same `filterTable()` function can be reused -- just add an `id` to the table and a filter input.

- **Diff table and Compare table don't have filters.** The Diff table has its own server-side filter dropdown (added/removed/modified). The Compare table has property/qubit checkboxes. Client-side row filtering isn't as useful there because the tables are already heavily filtered server-side.

---

## Task 5: Save Flow (Save All + Show Changes) -- DONE

### What changed

**Problem:** The old save/undo buttons were conditionally rendered -- they only appeared when `change_count > 0`. This violated the plan's requirement that Save All be always visible (greyed when inactive). The old Undo button only undid the *last* change; there was no way to review all pending changes or discard a specific one.

**Solution:** Four changes:

1. **Save All button** -- always visible in the topbar. `disabled` when no changes, active with a red badge when changes exist.
2. **Changes button** -- next to Save All, toggles a floating dropdown panel listing all pending changes.
3. **`GET /changes` route** -- returns an HTML fragment listing each pending `ChangeEntry` with path, old value, new value, and a Discard button.
4. **`POST /discard` route** -- discards a specific change by its index, restoring the old value. The panel re-renders itself after discard.

### Files added

| File | What it is |
|------|-----------|
| `templates/_changes.html` | HTML fragment for the changes dropdown. Renders a table of ChangeEntry objects with Discard buttons. HTMX-powered: each Discard button posts to `/discard` and re-renders the panel. |

### Files modified

| File | What changed |
|------|-------------|
| `core/modifier.py` | Added `discard(index)` method: restores old value, removes entry from change_log, clears cache, updates search index. Thread-safe (acquires store lock). |
| `web/routes.py` | Added `GET /changes` route (returns `_changes.html` with `modifier.get_change_log()`). Added `POST /discard` route (calls `modifier.discard(index)`, returns updated `_changes.html`). |
| `web/templates/base.html` | Topbar rewritten: Save All always visible (`disabled` attr when no changes), old conditional `{% if change_count > 0 %}` removed. New "Changes" button with `onclick="toggleChanges()"`. New `#changes-panel` div positioned below topbar with `hx-get="/changes"` triggered on custom `toggle-changes` event. Old "Undo" button removed (replaced by per-change Discard). |
| `web/static/app.js` | Added `toggleChanges()` function: toggles `.changes-hidden` class on `#changes-panel`, dispatches `toggle-changes` event to trigger HTMX load. |
| `web/static/style.css` | Added `.changes-panel` (absolute positioned dropdown, shadow, rounded bottom corners), `.changes-table` (compact table), `.change-old` (red, strikethrough), `.change-new` (green, bold), `.btn-discard` (red outline), `.btn-show-changes`, `.changes-hidden`. |

### How it works

```
User clicks "Changes" button
  → toggleChanges() removes .changes-hidden, dispatches "toggle-changes" event
  → #changes-panel has hx-trigger="toggle-changes from:body"
  → HTMX fires GET /changes → returns _changes.html
  → Panel shows: [path] [old_value] → [new_value] [Discard]

User clicks "Discard" on a specific change
  → POST /discard with index=N → modifier.discard(N) restores old value
  → Response is updated _changes.html → re-renders panel in place

User clicks "Save All"
  → POST /save → saver.save() → toast in #status-bar
  → (Page reload will show Save All as disabled, Changes as disabled)

User clicks "Changes" again
  → toggleChanges() adds .changes-hidden → panel hides
```

### `Modifier.discard(index)` details

```python
def discard(self, index: int) -> ChangeEntry | None:
    # 1. Validate index bounds
    # 2. Get entry from change_log[index]
    # 3. Restore old_value in merged dict + source dict (state/wiring)
    # 4. Pop entry from change_log
    # 5. Clear pointer cache, update search index
    # Returns the discarded ChangeEntry (or None if invalid index)
```

This differs from `undo()` which always pops the *last* entry. `discard()` can remove *any* entry by position. The `_rollback()` method uses `list.remove()` by reference; `discard()` uses `list.pop(index)` which is safer for indexed access.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The new `discard()` method doesn't affect existing undo/batch tests because it operates on a different code path.

### Notes for the next developer

- **The old "Undo" button is removed.** If you want Undo back, you can add it alongside Discard. But per-change Discard is strictly more powerful (undo-last is just discard-last-index).

- **The changes panel re-renders itself after each discard.** `POST /discard` returns the full updated `_changes.html`, which HTMX swaps into `#changes-panel`. No page reload needed.

- **The "Save All" and "Changes" buttons are always in the DOM** (not conditionally rendered). Their `disabled` attribute is set by Jinja2 based on `change_count`. After an HTMX operation that changes the count (save, discard, edit), the topbar doesn't auto-update. A full page nav or reload refreshes the button states. This is acceptable for V1; a future enhancement could use OOB swaps to update the topbar.

- **The changes panel is positioned absolute, right-aligned.** It floats over the content like a dropdown menu. It doesn't push down the layout.

- **`toggleChanges()` uses a custom DOM event** (`toggle-changes`) to trigger the HTMX load. This avoids manual `htmx.ajax()` calls and keeps the triggering declarative.

---

## Task 6: Edit Feedback (Modified Cells) -- DONE

### What changed

**Problem:** After a user edits a value in the qubit detail inspector, there is no visual indication that the value has been modified. The user has to open the Changes panel (Task 5) to see what's been changed. This makes it easy to lose track of edits, especially when modifying many values across different qubits.

**Solution:** Added three visual feedback mechanisms for modified cells in the qubit detail table:

1. **Light-red cell background** -- the value column (`<td class="col-val">`) gets a `.cell-modified` class with a soft red background and a 3px red left border.
2. **Hover tooltip on the cell** -- hovering the modified cell shows `"Previous: {original_value}"` via the HTML `title` attribute.
3. **Red-tinted input border** -- the `<input>` inside a modified cell gets an `.edit-input-modified` class with a red border and red focus glow (instead of the default blue).

### Files modified

| File | What changed |
|------|-------------|
| `web/routes.py` | Added `_modified_map()` helper function that builds a `{dot_path: old_value}` dict from the change_log. Uses `setdefault()` so that when a field is edited multiple times, only the *original* (first) old_value is recorded. Passed as `modified_map` to the qubit detail template. |
| `web/templates/_qubit_detail.html` | Added `{% set modified_map = modified_map \| default({}) %}` safe default at top. For each property row: checks `p.dot_path in modified_map`, conditionally adds `.cell-modified` class to the value `<td>`, adds `title="Previous: ..."` tooltip, and adds `.edit-input-modified` class to the edit input. The input's own `title` changes from "Press Enter to save" to "Modified — was: {old_value}" when the cell is modified. |
| `web/static/style.css` | Added `.cell-modified` (light red background `rgba(220,53,69,0.08)`, 3px red left border). Added `.edit-input-modified` (red border, subtle red background tint). Added `.edit-input-modified:focus` (red focus ring instead of blue). |

### How `_modified_map()` works

```python
def _modified_map() -> dict[str, Any]:
    store = _store()
    if not store:
        return {}
    m: dict[str, Any] = {}
    for entry in store.change_log:
        m.setdefault(entry.dot_path, entry.old_value)
    return m
```

The `setdefault()` call is important: if a user edits `qubits.qA1.f_01` from `5.0` to `6.0`, then from `6.0` to `7.0`, the change_log has two entries:

```
[0] dot_path="qubits.qA1.f_01", old_value=5.0, new_value=6.0
[1] dot_path="qubits.qA1.f_01", old_value=6.0, new_value=7.0
```

`setdefault()` keeps only `5.0` (the *original* value before any edits). The tooltip shows "Previous: 5.0", which is the value the user would get back if they discarded all edits.

### Template logic

```jinja2
{% set is_modified = p.dot_path and p.dot_path in modified_map %}
<td class="col-val{% if is_modified %} cell-modified{% endif %}"
    {% if is_modified %}title="Previous: {{ modified_map[p.dot_path] }}"{% endif %}>
    ...
    <input ... class="edit-input{% if is_modified %} edit-input-modified{% endif %}"
           title="{% if is_modified %}Modified — was: {{ modified_map[p.dot_path] }}{% else %}Press Enter to save. Current: {{ p.value }}{% endif %}">
```

The `p.dot_path and` guard handles properties without a `dot_path` (like computed or grouped values), preventing a `None in dict` check error.

### Visual design

| State | Cell background | Input border | Input focus | Tooltip |
|-------|----------------|-------------|-------------|---------|
| Unmodified | transparent | grey | blue glow | "Press Enter to save. Current: {value}" |
| Modified | light red (8% opacity) + 3px red left border | red | red glow | "Modified — was: {original_value}" |

The red is intentionally subtle (8% opacity background) to avoid being distracting while still providing clear visual feedback. The 3px left border acts as a quick scannable "gutter" indicator -- the user can scroll through the detail panel and instantly spot which rows have been changed.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The `modified_map` is a simple dict computed per-request; it doesn't alter any backend state. The existing `test_qubit_detail` and `test_qubit_edit` tests exercise the route with and without changes in the change_log.

### Notes for the next developer

- **`modified_map` is rebuilt on every request.** This is O(n) where n is the number of entries in the change_log. For typical usage (< 100 edits per session), this is negligible. If editing thousands of values, consider caching the map.

- **The safe default `{% set modified_map = modified_map | default({}) %}` is defensive.** Currently only `qubit_detail()` passes `modified_map`, but the template might be included from other contexts in the future. The `| default({})` ensures it never raises an `UndefinedError`.

- **Pair detail does not have edit feedback.** The `_pair_detail.html` template doesn't have editable fields (no inline edit forms), so there's no `modified_map` logic there. If pair editing is added later, the same pattern can be applied.

- **The visual indicators disappear after Save.** When the user saves (Task 5), `saver.save()` calls `change_log.clear()`, which means the next `_modified_map()` call returns `{}`. All cells return to their normal unmodified appearance.

- **The visual indicators also disappear after Discard.** When a specific change is discarded via Task 5's `/discard` route, the entry is removed from the change_log. On the next page render, that cell will no longer show as modified.

---

## Task 7: Cursor Retention After Edit -- DONE

### What changed

**Problem:** When a user edits a value in the qubit detail panel and presses Enter, `qubit_edit` issued a 302 redirect back to `qubit_detail`. HTMX followed the redirect, fetched the full detail, and swapped it into `#inspector-pane`. This replaced the entire DOM subtree, including the input the user was typing in. The result: focus was lost, the user had to scroll back down and click the next field. Extremely frustrating when editing many values in sequence.

**Solution:** Three changes:

1. **Eliminate the redirect** -- `qubit_edit` now renders the qubit detail HTML directly (200 response) instead of issuing a 302. This also saves a network round-trip.
2. **Pass `focus_path`** -- the `dot_path` of the just-edited field is passed to the template as `focus_path`.
3. **Auto-focus script** -- a `<script>` tag at the bottom of `_qubit_detail.html` calls `focusEditInput(dotPath)` when `focus_path` is non-empty. This finds the matching input and focuses it, placing the cursor at the end of the value.

### Files modified

| File | What changed |
|------|-------------|
| `web/routes.py` | Extracted `_render_qubit_detail(name, *, focus_path=None)` shared helper. `qubit_detail()` delegates to it (no `focus_path`). `qubit_edit()` calls it with `focus_path=dot_path` instead of `redirect()`. |
| `web/templates/_qubit_detail.html` | Added `{% set focus_path = focus_path \| default("") %}` safe default. Added `{% if focus_path %}<script>window.focusEditInput(...);</script>{% endif %}` at the bottom. |
| `web/static/app.js` | Added `focusEditInput(dotPath)` function. |

### How `focusEditInput` works

```javascript
window.focusEditInput = function(dotPath) {
    requestAnimationFrame(function() {
        // Find the hidden input whose value matches the edited dot_path
        var hidden = document.querySelector(
            'input[type="hidden"][name="dot_path"][value="' + dotPath + '"]'
        );
        if (!hidden) return;
        // Find the visible text input in the same <form>
        var input = hidden.parentElement.querySelector('input[name="value"]');
        if (!input) return;
        input.focus();
        // Place cursor at end of value
        var len = input.value.length;
        input.setSelectionRange(len, len);
    });
};
```

**Key details:**

- **`requestAnimationFrame`** ensures the HTMX swap is fully committed to the DOM before we try to focus. Without this, the element might not yet be in the document.
- **Selector strategy:** Each editable property has a `<form>` containing `<input type="hidden" name="dot_path" value="qubits.qA1.f_01">` and `<input type="text" name="value">`. We find the hidden input by its exact value, then go to its parent `<form>` to find the text input.
- **`setSelectionRange(len, len)`** places the cursor at the end of the value, which is the natural position for continuing to edit.

### Refactoring: `_render_qubit_detail`

Before this task, the rendering logic was duplicated between `qubit_detail` (view) and `qubit_edit` (post-edit). Now both delegate to a shared helper:

```python
def _render_qubit_detail(name: str, *, focus_path: str | None = None):
    # Fetch qubit data, build sections, collect port_info
    # Render _qubit_detail.html (HTMX) or qubit_detail.html (full page)
    # Pass focus_path to template context

@bp.route("/qubit/<name>")
def qubit_detail(name: str):
    return _render_qubit_detail(name)

@bp.route("/qubit/<name>/edit", methods=["POST"])
def qubit_edit(name: str):
    # ... validate, parse, set_value ...
    return _render_qubit_detail(name, focus_path=dot_path)
```

This eliminates the 302 redirect entirely. The response to an edit POST is a 200 with the updated detail HTML, which HTMX swaps directly into `#inspector-pane`.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The existing `test_qubit_edit` test used `follow_redirects=True` and asserted `status_code == 200`. With the direct render, the test still gets a 200 (no redirect to follow). The test continues to pass without modification.

### Notes for the next developer

- **No more redirect from `qubit_edit`.** The old pattern was `POST /qubit/qA1/edit → 302 → GET /qubit/qA1 → 200`. The new pattern is `POST /qubit/qA1/edit → 200` (one round-trip eliminated). This is both faster and required for focus retention.

- **The `<script>` tag is inside `#inspector-pane`.** When HTMX swaps the response HTML into the inspector, the inline `<script>` executes immediately as part of the swap. HTMX evaluates `<script>` tags in swapped content by default.

- **`focus_path` uses Jinja2's `| tojson` filter.** This safely JSON-encodes the dot_path string (handles quotes, special characters) so it can be passed as a JS string argument: `focusEditInput("qubits.qA1.f_01")`.

- **The focus script is harmless on a normal (non-edit) detail view.** When `focus_path` is empty/None, the `{% if focus_path %}` guard prevents the `<script>` tag from being emitted at all.

- **If pair editing is added later**, the same `focus_path` + `focusEditInput` pattern can be reused in `_pair_detail.html` with zero extra JS.

---

## Task 8: Generic Inspector Panel -- DONE

### What changed

**Problem:** The inspector panel (`#inspector-pane`) hosted three different content types -- qubit detail, pair detail, and search results -- but each had its own header structure (or none at all for search). There was no way to close/dismiss the inspector once opened, and no visual indication of *what type* of content was being displayed. The panel lacked a unified identity.

**Solution:** Four changes:

1. **Shared header partial** -- `_inspector_header.html` provides a consistent header bar for all inspector content.
2. **Type badges** -- colored labels ("Qubit", "Pair", "Search") using the same color scheme as the existing category badges in search results.
3. **Close button** -- an `×` button in the header that clears the inspector and collapses it back to zero height.
4. **`closeInspector()` JS function** -- clears the pane's innerHTML and dispatches a custom `inspector-closed` event that the Split.js lifecycle manager listens for.

### Files added

| File | What it is |
|------|-----------|
| `templates/_inspector_header.html` | Shared partial: type badge + title + optional subtitle + close button. |

### Files modified

| File | What changed |
|------|-------------|
| `templates/_qubit_detail.html` | Removed inline `<header>` with `<h3>`. Added `{% set inspector_type/label/sub %}` + `{% include '_inspector_header.html' %}` before the `<article>`. |
| `templates/_pair_detail.html` | Same pattern: removed inline `<header>`, added inspector header include. |
| `templates/_search_results.html` | Added inspector header include at the top (search had no header before). |
| `static/app.js` | Added `closeInspector()` function: clears `#inspector-pane` innerHTML, dispatches `inspector-closed` event. |
| `templates/base.html` | Added listener for `inspector-closed` event that calls `initSplit()` (recalculates layout, hides empty inspector). |
| `static/style.css` | Added `.inspector-header` (sticky, flex, border-bottom), `.inspector-title`, `.inspector-badge` (with color variants for qubit/pair/search/wiring/h5), `.inspector-close` (hover highlight). Removed old `.qubit-detail header` and `.pair-detail header` rules. |

### How the inspector header works

Each detail template sets three variables before including the shared partial:

```jinja2
{% set inspector_type = "qubit" %}
{% set inspector_label = qubit_name %}
{% set inspector_sub = "Grid: " ~ qubit.grid_location if qubit.grid_location else "" %}
{% include '_inspector_header.html' %}
```

The partial renders:

```
[Qubit] qA1  Grid: (3, 1)                                    [×]
──────────────────────────────────────────────────────────────────
```

- **Badge** -- color-coded by type (blue for qubit, red for pair, orange for search)
- **Title** -- entity name (bold)
- **Subtitle** -- optional secondary info (muted, small)
- **Close button** -- right-aligned `×`

The header is `position: sticky; top: 0` within the inspector pane, so it stays visible as the user scrolls through the detail content.

### How `closeInspector()` works

```javascript
window.closeInspector = function() {
    var pane = document.getElementById("inspector-pane");
    if (!pane) return;
    pane.innerHTML = "";
    document.body.dispatchEvent(new Event("inspector-closed"));
};
```

The `inspector-closed` custom event is caught by the Split.js IIFE in `base.html`:

```javascript
document.body.addEventListener("inspector-closed", function() {
    initSplit();
});
```

`initSplit()` sees that `#inspector-pane` is now empty, adds the `.split-hidden` class, destroys the Split.js instance, and the table pane returns to full height. The gutter disappears.

### Badge color scheme

| Type | Background | Text | Usage |
|------|-----------|------|-------|
| `qubit` | `#e3f2fd` (light blue) | `#1565c0` | Qubit detail |
| `pair` | `#fce4ec` (light pink) | `#c62828` | Pair detail |
| `search` | `#fff3e0` (light orange) | `#ef6c00` | Search results |
| `wiring` | `#e8f5e9` (light green) | `#2e7d32` | Future: wiring detail |
| `h5` | `#f3e5f5` (light purple) | `#7b1fa2` | Future: H5 dataset detail |

These match the `cat-qubit`, `cat-pair`, etc. badge colors already used in search results, keeping the visual language consistent.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The tests check for section names ("Frequencies", "Coherence"), dot paths, and CSS class presence ("inline-edit", "pointer-badge") -- all of which are in the article body, not the header. The header change is transparent to existing test assertions.

### Notes for the next developer

- **Adding a new inspector content type is a 3-line addition.** Set `inspector_type`, `inspector_label`, `inspector_sub`, then `{% include '_inspector_header.html' %}`. The badge color is automatic if you add a `.inspector-badge-{type}` CSS rule.

- **The close button only clears the DOM.** It does not notify the server. The inspector is purely client-side UI state. Reopening is done by clicking a table row or searching again.

- **The inspector header is sticky within `#inspector-pane`.** This means it stays visible as the user scrolls through long qubit detail sections. The `z-index: 5` ensures it sits above table content but below the topbar (`z-index: 100`) and changes panel (`z-index: 99`).

- **Search results get the header *before* the category tabs.** So the visual stack is: inspector header → category tabs → results table. The close button is always accessible even when scrolled deep into results.

- **Old `<header>` CSS rules removed.** `.qubit-detail header, .pair-detail header` and `.qubit-detail h3, .pair-detail h3` are gone. If you see tests or docs referencing them, they're stale.

---

## Task 9: Typography / CSS Overhaul -- DONE

### What changed

**Problem:** The UI used Pico CSS's defaults (16px body, generic system fonts, `rem`-based sizes throughout). Text was too large for a data-dense tool, values were indistinguishable from labels (both used the same proportional font), and padding was generous rather than compact. The result felt more like a blog than an IDE.

**Solution:** A comprehensive typography overhaul touching every section of `style.css`:

1. **CSS custom properties** -- three design tokens at `:root` level
2. **14px base font** -- more compact without being cramped
3. **Dual font stacks** -- UI font for labels, monospace font for values
4. **Compact padding** -- tighter cells, slimmer topbar, tighter pane padding
5. **`data-font-size` prep** -- HTML attribute hooks for Task 11's S/M/L settings

### Files modified

| File | What changed |
|------|-------------|
| `static/style.css` | Complete pass over every section. ~50 individual property changes. See details below. |

### Design tokens

```css
:root {
    --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Oxygen, Ubuntu, "Helvetica Neue", Arial, sans-serif;
    --font-mono: "JetBrains Mono", "Fira Code", "Cascadia Code",
                 "SF Mono", Menlo, Consolas, "DejaVu Sans Mono", monospace;
    --font-size-base: 14px;
}
html[data-font-size="small"]  { --font-size-base: 13px; }
html[data-font-size="large"]  { --font-size-base: 16px; }
```

**Why custom properties?**
- `--font-ui` and `--font-mono` ensure a consistent dual-font system across all components. Any future element just uses `font-family: var(--font-mono)` instead of repeating the stack.
- `--font-size-base` is the single source of truth for base size. Task 11 just sets `data-font-size="small|large"` on `<html>` and the entire UI scales.
- The font stacks prioritize modern coding fonts (JetBrains Mono, Fira Code, Cascadia Code) for value display, falling back to platform-specific monospace.

### What gets monospace

| Element | Why |
|---------|-----|
| `.data-table td` (except first column) | Numeric qubit data, frequencies, fidelities |
| `.prop-table .col-val` | Qubit detail value cells |
| `.edit-input` | Inline edit inputs |
| `.comparison-table td` (except first column) | Comparison values |
| `.dot-path` | JSON paths like `qubits.qA1.f_01` |
| `.pointer-badge`, `.selfref-badge` | Pointer references like `#/qubits/qA1/f_01` |
| `.changes-table td` | Old/new values in the changes panel |
| `.diff-val` | Diff values |
| `.search-value` | Search result values |
| `.col-score` | Search relevance scores |
| `.port-kv` | Wiring port key-value pairs |
| `code` | All inline code |
| `.run-id`, `.entry-time` | Sidebar tree timestamps |
| `.trend-label` | Compare/trend chart labels |
| `.active-state-badge kbd` | Active state name in topbar |

First-column cells in data tables use `var(--font-ui)` with `font-weight: 500` since they contain entity names (qubit IDs, pair names), not values.

### Sizing strategy

All element-level `font-size` values were converted from absolute `rem` to relative `em`. This means:
- Every element's font size is relative to its parent, not the root
- Changing `--font-size-base` on `body` cascades proportionally through the entire UI
- The sidebar, tables, detail panels, and topbar all scale together

**Examples of conversions:**
- `0.85rem` → `0.92em` (data table body)
- `0.8rem` → `0.85em` (table headers)
- `0.82rem` → `0.88em` (filter inputs, tabs)
- `0.72rem` → `0.78em` (timestamps, small badges)

### Padding reductions

| Area | Before | After |
|------|--------|-------|
| Topbar | `0.25rem 1rem` | `0.2rem 0.75rem` |
| Table pane | `1rem 1.5rem` | `0.6rem 1rem` |
| Inspector pane | `0.75rem 1.5rem` | `0.5rem 1rem` |
| Data table cells | `0.3rem 0.5rem` | `0.2rem 0.4rem` |
| Prop table cells | `0.2rem 0.4rem` | `0.15rem 0.35rem` |
| Sidebar | `0.5rem 0.75rem` | `0.4rem 0.6rem` |
| Sidebar width | `280px` | `260px` |

### Table header treatment

All table headers (`th`) now have:
```css
text-transform: uppercase;
letter-spacing: 0.03em;
color: var(--pico-muted-color);
font-weight: 600;
```

This creates clear visual separation between column headers and data rows, following the convention used by Cursor, VS Code, and other IDE table views.

### Test results

All 491 tests pass (1 skipped). Zero regressions. Typography changes are purely CSS -- they don't affect server responses, route logic, or template structure. Existing test assertions check for text content and CSS class names, not font sizes or padding.

### Notes for the next developer

- **The `data-font-size` attribute on `<html>` is not yet set.** It's only defined in CSS. Task 11 will add the JS (`setFontSize()`) and UI (gear dropdown) to toggle it. The attribute is optional -- if absent, the default 14px applies.

- **`em` vs `rem` strategy.** We intentionally use `em` (relative to parent) instead of `rem` (relative to root) for component-level sizes. This is because Pico CSS sets `html { font-size: 100% }` (browser default, typically 16px) and many of its internal rules use `rem`. By keeping our sizes in `em` relative to `body`'s 14px, we don't fight with Pico's `rem`-based layout.

- **The first column exception.** `.data-table td:first-child` and `.comparison-table td:first-child` override `font-family` back to `var(--font-ui)`. This is because the first column typically contains entity names (qubit IDs), which read better in a proportional font. If a table doesn't follow this pattern, add a class override.

- **Font availability.** The monospace stack starts with JetBrains Mono, Fira Code, and Cascadia Code -- popular developer fonts. If none are installed, it falls back to the platform's default monospace (SF Mono on macOS, Consolas on Windows, DejaVu on Linux). Values will always be monospace; they just might not be the *prettiest* monospace.

- **`-webkit-font-smoothing: antialiased`** improves rendering on macOS/Chrome. It's a no-op on Windows. The matching `-moz-osx-font-smoothing: grayscale` covers Firefox.

---

## Task 10: Sidebar Collapse + Polling -- DONE

### What changed

**Problem:** Two issues: (1) The sidebar took a fixed ~260px of horizontal space with no way to hide it, reducing the available width for data tables and the inspector panel. Researchers editing many values want maximum horizontal space. (2) When running experiments, new `quam_state` folders appear in the workspace every few minutes, but the sidebar tree only updated on manual page refresh or explicit re-scan. Users had to repeatedly click to see new data.

**Solution:** Three changes:

1. **Collapsible sidebar** -- a `☰` toggle button in the topbar that hides/shows the sidebar with a CSS transition.
2. **`localStorage` persistence** -- the collapsed/expanded state survives page reloads.
3. **60-second HTMX polling** -- the workspace tree auto-refreshes every 60 seconds to pick up new experiment folders.

### Files modified

| File | What changed |
|------|-------------|
| `templates/base.html` | Added `☰` toggle button as the first topbar item. Added `hx-get="/workspace/tree" hx-trigger="every 60s"` to `#sidebar-tree`. Added `restoreSidebar()` function in the IIFE to read `localStorage` on page load. |
| `static/app.js` | Added `toggleSidebar()` function: toggles `.sidebar-collapsed` on `.app-layout`, persists to `localStorage`. |
| `static/style.css` | Added `.sidebar-toggle` button styling. Added `transition` on `#sidebar` for smooth collapse animation. Added `.sidebar-collapsed #sidebar` rule (width: 0, overflow: hidden, opacity: 0, pointer-events: none). |

### How the sidebar toggle works

```
User clicks ☰ button
  → toggleSidebar() toggles .sidebar-collapsed on .app-layout
  → CSS transition: sidebar width 260px → 0, opacity 1 → 0 (150ms ease)
  → localStorage.setItem("quam_sidebar_collapsed", "1" or "0")

Page load
  → restoreSidebar() reads localStorage
  → If "1", adds .sidebar-collapsed to .app-layout before first paint
  → Sidebar starts collapsed (no flash of expanded state)
```

The collapsed state uses `pointer-events: none` to prevent interaction with the hidden sidebar content, and `overflow: hidden` to ensure nothing leaks out during the transition.

### How 60s polling works

```html
<div id="sidebar-tree"
     hx-get="/workspace/tree" hx-trigger="every 60s"
     hx-swap="innerHTML">
```

HTMX fires `GET /workspace/tree` every 60 seconds. The server re-scans the workspace roots and returns the updated tree HTML. New experiment folders appear automatically. The polling is lightweight -- the tree endpoint returns a small HTML fragment, and HTMX only swaps if the response differs.

### CSS details

```css
#sidebar {
    transition: width 0.15s ease, padding 0.15s ease, opacity 0.15s ease;
}
.sidebar-collapsed #sidebar {
    width: 0; min-width: 0; padding: 0;
    overflow: hidden; border-right: none;
    opacity: 0; pointer-events: none;
}
```

The sidebar toggle button uses the hamburger character `☰` (U+2630), styled as a minimal icon button:

```css
.sidebar-toggle {
    background: none; border: none; cursor: pointer;
    font-size: 1.15rem; padding: 0.15rem 0.35rem;
    color: var(--pico-muted-color); border-radius: 4px;
}
```

### Test results

All 491 tests pass (1 skipped). Zero regressions. The sidebar toggle is purely client-side CSS/JS. The 60s polling uses existing server-side routes -- no new endpoints needed.

### Notes for the next developer

- **`localStorage` key is `quam_sidebar_collapsed`.** Value is `"1"` (collapsed) or `"0"` (expanded). Default (key missing) is expanded.

- **The toggle class is on `.app-layout`, not on `#sidebar`.** This allows the CSS descendant selector `.sidebar-collapsed #sidebar` to work, and also lets other elements (like `#main`) react to the collapsed state if needed in the future.

- **Polling doesn't run when the sidebar is collapsed.** HTMX still fires the request (the div is in the DOM, just hidden), but the response is tiny and the swap is invisible. This is acceptable overhead for a 60s interval. If you want to stop polling when collapsed, add `hx-trigger="every 60s [!document.querySelector('.sidebar-collapsed')]"` (HTMX extended syntax).

- **The transition is intentionally fast (150ms).** Longer transitions feel sluggish for a collapse/expand toggle. 150ms is perceptible enough to be smooth but fast enough to feel instant.

- **`restoreSidebar()` runs before `initSplit()`.** This ensures the layout is correct before Split.js measures container sizes. If the sidebar is collapsed, `#main` gets the full width from the start.

---

## Task 11: Font Size Settings -- DONE

### What changed

**Problem:** Task 9 set the default font size to 14px and prepared CSS hooks (`data-font-size` attribute, `--font-size-base` variable), but there was no UI for users to change the font size. Different researchers may have different preferences depending on screen size and visual acuity.

**Solution:** A gear icon dropdown in the topbar with three font size options:

| Button | Attribute | Base size |
|--------|-----------|-----------|
| **S** | `data-font-size="small"` | 13px |
| **M** | _(attribute removed)_ | 14px (default) |
| **L** | `data-font-size="large"` | 16px |

### Files modified

| File | What changed |
|------|-------------|
| `templates/base.html` | Added `⚙` gear button + `#settings-dropdown` in the topbar (after "Changes" button). Added font size restore logic to the IIFE's `restorePrefs()` (renamed from `restoreSidebar()`). Active button highlighting on page load. |
| `static/app.js` | Added `toggleSettings()` (open/close dropdown, click-outside-to-close) and `setFontSize(size)` (sets `data-font-size` on `<html>`, persists to `localStorage`, highlights active button). |
| `static/style.css` | Added `.settings-wrap` (relative positioning anchor), `.settings-btn` (gear icon styling), `.settings-dropdown` (absolute dropdown with shadow), `.settings-label`, `.settings-options` (flex row), `.settings-opt` (individual size buttons), `.settings-opt-active` (highlighted state). |

### How it works

```
User clicks ⚙ gear button
  → toggleSettings() removes .settings-hidden on #settings-dropdown
  → Dropdown appears with S / M / L buttons
  → One-time document click listener auto-closes on click outside

User clicks "L" button
  → setFontSize("large")
  → document.documentElement.setAttribute("data-font-size", "large")
  → CSS rule: html[data-font-size="large"] { --font-size-base: 16px; }
  → body { font-size: var(--font-size-base) } → entire UI scales to 16px
  → localStorage.setItem("quam_font_size", "large")
  → .settings-opt-active class toggled to highlight the "L" button

Page load
  → restorePrefs() reads localStorage("quam_font_size")
  → Sets data-font-size attribute on <html>
  → Highlights the correct button in the dropdown
```

### Design decisions

1. **Gear icon in the topbar** -- not competing with Save/Changes buttons. It's at the far right, visually separated.

2. **Three discrete sizes, not a slider** -- per the plan's recommendation. Simple, no complexity, covers the practical range (13-16px).

3. **"M" removes the attribute** -- the default 14px is defined in `:root` without any `data-font-size` attribute. Setting Medium just removes the attribute, returning to the CSS default. This keeps the default state clean.

4. **Click-outside-to-close** -- the dropdown registers a one-time document click listener when opened. Clicking anywhere outside the dropdown closes it. This is the standard dropdown UX pattern.

5. **Active button highlighting** -- the selected size button gets `.settings-opt-active` (primary color background, inverse text). This is restored on page load from `localStorage`.

### Test results

All 491 tests pass (1 skipped). Zero regressions. The settings dropdown is purely client-side HTML/CSS/JS -- no server-side changes.

### Notes for the next developer

- **`localStorage` key is `quam_font_size`.** Values: `""` (empty = 14px default), `"small"` (13px), `"large"` (16px). If the key is missing or empty, 14px applies.

- **`restorePrefs()` replaced `restoreSidebar()`.** The IIFE function now restores both sidebar collapsed state and font size. It runs before `initSplit()` so layout measurements are correct.

- **Adding more font sizes** is trivial: add a CSS rule `html[data-font-size="xlarge"] { --font-size-base: 18px; }` and a button with `data-size="xlarge"` in the dropdown.

- **The settings dropdown can host more settings in the future.** The structure uses `.settings-group` with a label and options. Add another group (e.g., theme light/dark) by appending to the dropdown HTML.

- **Task 12 (Client-side JS) is now complete** as a side effect. All planned `app.js` functions are implemented: `filterTable()`, `toggleChanges()`, `focusEditInput()`, `closeInspector()`, `toggleSidebar()`, `toggleSettings()`, `setFontSize()`.

---

## Task 13 -- Test backfill

### What changed

Added **36 new tests** across **9 new test classes** to `tests/test_web.py`, covering every UI feature introduced in Tasks 2-11. Total test count: **527 passed**, 1 skipped (0 regressions).

### Files modified

| File | Change |
|------|--------|
| `tests/test_web.py` | +9 test classes, +36 test methods |

### New test classes

| Class | Covers | # Tests |
|-------|--------|---------|
| `TestLayoutContainers` | Stable HTMX containers (`#table-pane`, `#inspector-pane`, `#status-bar`), Split.js, `app.js` script tags | 8 |
| `TestTableFilters` | Client-side `filterTable()` inputs on qubits, pairs, comparison table, wiring | 4 |
| `TestSaveFlow` | Save All button (always visible, disabled when clean), Changes dropdown, `/changes` + `/discard` routes | 7 |
| `TestEditFeedback` | `.cell-modified` class, `.edit-input-modified`, previous-value tooltip | 4 |
| `TestCursorRetention` | Edit returns 200 (not redirect), response contains detail + `focusEditInput` script | 3 |
| `TestGenericInspector` | Inspector header in qubit/pair/search detail, type badges, close button | 4 |
| `TestSidebarFeatures` | Sidebar toggle button, 60s HTMX polling, workspace tree route | 3 |
| `TestFontSizeSettings` | Settings gear button, dropdown, S/M/L options with `data-size` attrs | 3 |

### Design decisions

1. **HTML-content assertions** -- Tests verify the presence of CSS classes, element IDs, and JS function names in the rendered HTML. This ensures the templates actually emit the expected structure, not just that the routes return 200.

2. **Behavioral flow tests** -- `TestSaveFlow` and `TestEditFeedback` perform a sequence (edit → check) to verify that state mutations produce the expected UI changes (e.g., `cell-modified` appears only after an edit).

3. **No JS execution** -- Flask's test client doesn't run JavaScript. Client-side behavior (Split.js, `filterTable`, `setFontSize`) is verified by checking that the correct scripts, IDs, and function calls are present in the HTML. Full E2E JS testing would require a browser driver (Playwright/Selenium) -- deferred to V2.

### Notes for the next developer

- **Tests are fast** -- the full 527-test suite runs in ~8s. The synthetic fixture (`synth_folder`) avoids I/O to real data.

- **Real-data tests** (`TestRealData`) are skipped when the ExampleChip data folder isn't available. They still work on the dev machine.

- **Adding tests for new features**: follow the pattern -- check `resp.status_code`, decode `resp.data`, and assert on CSS classes, element IDs, and JS function names. This catches template regressions without needing a browser.

---

## REDESIGN COMPLETE

All 13 of 13 tasks are done. The web UI has been fully redesigned with:

- Multi-context backend model (extensible to HDF5/plotting)
- Stable HTMX architecture with `#table-pane`, `#inspector-pane`, `#status-bar`
- Resizable split-pane layout (Split.js)
- Global search + per-table client-side filters
- Save All / Show Changes / Discard workflow
- Edit feedback (modified cells, tooltips)
- Cursor retention after edits
- Generic inspector panel (qubit, pair, search)
- Typography overhaul (14px default, monospace values, Cursor-like fonts)
- Collapsible sidebar with 60s auto-polling
- Font size settings (S/M/L)
- Full client-side JS in `app.js`
- 527 tests, 0 regressions

### Future work (V2)

| Feature | Notes |
|---------|-------|
| Full file explorer | Tree-based file browser with expand/collapse (deferred from V1) |
| HDF5 context | Second context type for `.h5` files -- backend model already supports it |
| Plotting | Plot data from HDF5 datasets; display saved images |
| Browser E2E tests | Playwright/Selenium for JS-dependent features (Split.js, filters, settings) |
| Theme switching | Light/dark mode toggle in the settings dropdown |

## Full plan reference

See the plan file at `.cursor/plans/web_ui_redesign_final_346372ba.plan.md` for the complete specification of all 13 tasks.

---

# Post-Redesign Features

## Compare / Trend Tracker -- DONE

### What was added

Three major features layered on top of the base redesign:

1. **Compare Selected (tabbed diff view)** -- Select multiple experiment states via sidebar checkboxes, click "Compare Selected" to see a tabbed comparison. "Differences" tab highlights what changed; per-state tabs show full qubit properties with delta values vs a user-selectable reference state.

2. **Trend Tracker** -- Separate button that charts selected properties across multiple states over time using Plotly. Grouped property picker with category toggles.

3. **Experiment data integration (data.json + node.json)** -- Comparison views include metadata, parameters (`node.json > data.parameters.model`), and fit results (`data.json > fit_results`) in collapsible sections. Handles mismatched keys across different experiment types gracefully.

### Key files

| File | Role |
|------|------|
| `core/experiment_data.py` | `ExperimentContext` dataclass + `load_experiment_context()` |
| `core/differ.py` | `multi_diff()`, `compare_parameters()`, `compare_fit_results()` |
| `web/routes.py` | `/compare`, `/compare/diff`, `/compare/state`, `/trend`, `/trend/chart` routes |
| `web/templates/_compare_tabs.html` | Tab bar container |
| `web/templates/_compare_diff.html` | Differences tab with ref selector + 4 collapsible sections |
| `web/templates/_compare_state.html` | Per-state tab with metadata/params/fits/properties |
| `web/templates/_trend_picker.html` | Property/qubit selection for trend charts |
| `web/templates/_trend_chart.html` | Plotly chart rendering |

---

## Comparison Table UX Fixes -- DONE

### What was fixed

1. **Chain filter dropdown bug** -- Selecting a chain (e.g. "Chain B") caused other chains to disappear from the dropdown. Root cause: chains were extracted from already-filtered rows. Fix: extract chains from all rows before applying the filter.

2. **Property selection redesign** -- Replaced flat row of 18 checkboxes with a grouped layout matching the qubit detail view's sections (Identity, Frequencies, Coherence, XY Drive, Readout, Flux, Gate Fidelity). All 31 properties selected by default. Per-group toggle checkboxes with indeterminate state. "Select All" / "Deselect All" links.

### Key changes

| File | What changed |
|------|-------------|
| `web/routes.py` | `_TABLE_PROP_GROUPS` + `_ALL_TABLE_PROPS` built from `_QUBIT_PROPERTY_MAP`. Chain extraction moved before filter. Default props = all 31. |
| `web/templates/_table.html` | Grouped property selector with `toggleAllProps()`, `toggleGroup()`, `syncGroupCheckbox()` JS functions. |
| `web/static/style.css` | `.prop-selector`, `.prop-group`, `.prop-group-toggle`, `.prop-group-label`, `.prop-group-items` styles. |
| `tests/test_web.py` | `TestTableChainFilter` (multi-chain fixture, 3 tests), plus grouped selector and default-props tests in `TestTable`. |

---

## Folder Browser and Path Autocomplete -- DONE

### What was added

Both the workspace "Add folder path" input and the "Load" input now support:

1. **Path autocomplete** -- As the user types, the server suggests matching directories (debounced 250ms). Dropdown appears below the input with keyboard navigation (arrow keys, Enter, Escape).

2. **Folder browser modal** -- A "Browse" button (folder icon) opens a `<dialog>` modal with breadcrumb navigation, clickable folder list, and "Select" button. Highlights folders containing `quam_state` data. "Go up" navigation via `.. (up)` entry.

### Key changes

| File | What changed |
|------|-------------|
| `web/routes.py` | `GET /browse` route -- lists directory children, handles partial paths for autocomplete, returns `has_quam_state` and `has_experiment_children` flags. Cross-platform (Windows drives / Unix roots). |
| `web/templates/base.html` | Browse buttons on both inputs, `<dialog id="folder-browser">` modal, autocomplete init script. |
| `web/static/app.js` | `initPathAutocomplete()`, `openFolderBrowser()`, `navigateBrowser()`, `selectBrowserFolder()` functions. |
| `web/static/style.css` | `.path-input-group`, `.path-suggestions`, `.folder-browser-dialog`, `.browser-breadcrumbs`, `.browser-list`, `.browser-folder` styles. |
| `tests/test_web.py` | `TestBrowse` class (7 tests: roots, valid dir, partial path, invalid path, button rendering). |

### Test results

582 tests passed, 1 skipped, 0 regressions.
