# 18: Dark Mode, Color Parameterization, and Recent Features

## What was built

Five features delivered across the v0.2 red-team audit and subsequent commits:

1. **Dark mode with fully parameterized color tokens** -- every hardcoded hex color extracted to CSS custom properties, dark theme overrides, toggle UI, localStorage persistence
2. **Sidebar tree sticky state** -- preserves folder open/closed state and scroll position across auto-refresh polls
3. **Compare/Trend button relocation** -- buttons moved above workspace folder list for easier access
4. **Figures tab enhancement** -- parameters + fit results displayed above figures in 2-column layout
5. **Topology grid Y-axis fix** -- QUAM `grid_location` row axis flipped so row 0 appears at bottom

## Feature 1: Dark Mode + Parameterized Color Tokens

### Problem

The stylesheet (`style.css`) had ~100 hardcoded hex colors scattered across status badges, toast alerts, diff styling, tree value colors, topology indicators, inspector badges, and more. These would look wrong on a dark background and were impossible to theme without search-and-replace.

### Solution

**Phase 1: Extract all colors into CSS custom properties**

Added ~70 semantic color tokens to `:root`, grouped by purpose:

| Group | Example tokens | Count |
|-------|---------------|-------|
| Status/semantic | `--color-success-text`, `--color-error-bg`, `--color-warning-border` | 12 |
| Cell quality | `--cell-good-text`, `--cell-best-bg`, `--cell-worst-bg` | 5 |
| Inspector badges | `--badge-qubit-bg`, `--badge-pair-text`, `--badge-dataset-bg` | 11 |
| JSON tree values | `--tree-val-string`, `--tree-val-number`, `--tree-val-boolean` | 7 |
| Diff colors | `--diff-added-text`, `--diff-removed-bg`, `--diff-type-modified-bg` | 9 |
| Pending tray | `--tray-bg`, `--tray-border`, `--tray-text` | 3 |
| Topology indicators | `--topo-improved-outline`, `--topo-degraded-outline` | 3 |
| Row highlights | `--row-pointer-bg`, `--row-selfref-bg` | 4 |
| Category tags | `--cat-qubit-bg`, `--cat-pair-bg`, etc. | ~10 |

Every CSS rule that previously used a hardcoded color now uses `var(--token-name)`.

**Phase 2: Dark theme overrides**

Added `[data-theme="dark"]` block that overrides all tokens with dark-appropriate values:
- Backgrounds go dark (low lightness), text goes bright
- Pico CSS handles base UI elements (inputs, buttons, table borders) automatically
- Our overrides handle QUAM-specific semantic colors

**Phase 3: Theme toggle**

- `toggleTheme()` function in `app.js` toggles `data-theme` attribute on `<html>`
- Persisted in `localStorage` as `quam_theme`
- Restored on page load in `restorePrefs()` IIFE
- Settings dropdown shows "Dark mode" button with active state indicator

**Phase 4: Plotly chart theming**

- `_applyThemeToPlotly(theme)` updates `UI_CONFIG` colors used by charts
- `_plotlyRender()` uses `paper_bgcolor: 'transparent'` and `plot_bgcolor: 'transparent'` so charts inherit CSS background

**Phase 5: Colorblind + dark compatibility**

Combined overrides for `[data-theme="dark"] .colorblind-mode` ensure colors remain distinguishable in both modes simultaneously.

### Files changed

| File | Changes |
|------|---------|
| `style.css` | +~70 color tokens in `:root`, +`[data-theme="dark"]` overrides, replaced ~100 hardcoded hex values with `var()`, +dark colorblind overrides (~800 new lines) |
| `app.js` | +`toggleTheme()`, +`_applyThemeToPlotly()`, +theme restore on load (~80 lines) |
| `base.html` | +dark mode toggle button in settings dropdown, removed hardcoded `data-theme="light"` |

---

## Feature 2: Sidebar Tree Sticky State

### Problem

The sidebar workspace tree auto-refreshes on a configurable interval (default 60s). Each refresh replaced the entire `#sidebar-tree` DOM, causing all `<details>` elements to reset to their default open/closed state and losing the user's scroll position.

### Solution

Used HTMX `beforeSwap` and `afterSwap` event handlers (same pattern as the inspector sticky state):

```
beforeSwap:
  1. Walk all <details> in #sidebar-tree, record which are open (keyed by date label text)
  2. Save scroll position
  3. Store in window._sidebarSticky

afterSwap:
  1. Restore <details> open/closed state from _sidebarSticky
  2. Restore scroll position
```

### Files changed

| File | Changes |
|------|---------|
| `app.js` | +`_sidebarSticky` object with beforeSwap/afterSwap handlers (~40 lines) |

---

## Feature 3: Compare/Trend Button Relocation

### Problem

"Compare Selected" and "Trend Tracker" buttons were inside `_sidebar_tree.html`, which meant they were destroyed and re-created on every auto-refresh. They also appeared below the folder list, requiring scrolling.

### Solution

Moved the `<form id="compare-form">` into `base.html`, positioned right after the Workspace `<summary>` header and before the folder selection input. Checkboxes in `_sidebar_tree.html` still work via the HTML `form="compare-form"` attribute.

### Files changed

| File | Changes |
|------|---------|
| `base.html` | Moved compare form here, placed above workspace folder input |
| `_sidebar_tree.html` | Removed the compare form (checkboxes use `form` attribute to associate) |

---

## Feature 4: Figures Tab Enhancement

### Problem

The Figures tab in dataset detail only showed figure images. Users wanted parameters (from `node.json`) and fit results (from `data.json`) visible alongside figures for context.

### Solution

**Layout:** 2-column info row above 2-column figure grid:
```
┌─ Fit Results (left) ──┬─ Parameters (right) ─┐
│  qubit tables          │  renderJsonTree()     │
└────────────────────────┴───────────────────────┘
┌─ Figure 1 ─┬─ Figure 2 ─┐
│             │             │
└─────────────┴─────────────┘
```

**Data fix:** `dataset.py` was only extracting `data.parameters.model` from `node.json`. Fixed to fall back to the full `data.parameters` dict when `model` is empty.

**Value display fix:** Jinja template had `[{{ val | length }} items]` for list/array values. Changed to:
- Lists: displayed as comma-separated values in `<code>` tags
- Nested dicts: rendered via `renderJsonTree()` with depth 2

### Files changed

| File | Changes |
|------|---------|
| `dataset.py` | Fixed parameter extraction to fall back from `data.parameters.model` to `data.parameters` |
| `_dataset_detail.html` | Added 2-column info row (fit results + parameters) above figure grid; fixed list value display |
| `style.css` | Added `.fig-info-row`, `.fig-info-col`, `.ds-inline-tree` rules; changed `.figure-grid` to `repeat(2, 1fr)` |

---

## Feature 5: Topology Grid Y-Axis Fix

### Problem

QUAM `grid_location` convention has row 0 at the **bottom** of the physical chip (math-style Y-axis). The topology rendering used row values directly for screen Y position, causing qubits to display upside-down relative to the physical layout.

### Customer's chip layout (3x3):
```
Screen top:    q0 — q0-q1 — q1 — q1-q2 — q2     (grid row 2)
               |            |            |
               q3 — q3-q4 — q4 — q4-q5 — q5     (grid row 1)
               |            |            |
Screen bottom: q6 — q6-q7 — q7 — q7-q8 — q8     (grid row 0)
```

### Solution

Flipped the Y-axis in both rendering locations:

1. **`buildTopology()`** -- main chip SVG:
   ```javascript
   // Before: p.y = (p.row - minRow) * SPACING_Y + PAD;
   // After:
   p.y = (maxRow - p.row) * SPACING_Y + PAD;
   ```

2. **`buildMetricPanels()`** -- per-metric detail grids:
   ```javascript
   // After normalization, flip rows:
   gridPositions[qid].row = maxGR - gridPositions[qid].row;
   ```

### Files changed

| File | Changes |
|------|---------|
| `_wiring.html` | Flipped row axis in `buildTopology()` (line ~300) and `buildMetricPanels()` (line ~835) |

---

## Current stats (post-changes)

| Metric | Value |
|--------|-------|
| Tests | 681 passing, 1 skipped |
| Test files | 14 |
| Application code | ~7,400 lines |
| Test code | ~7,200 lines |
| Routes | 60 |
| Templates | 46 (14 full pages + 32 partials) |
| `app.js` | 3,887 lines |
| `style.css` | 2,677 lines |
| CSS custom properties | ~400 |
| `routes.py` | 2,219 lines |
| `_wiring.html` | 1,211 lines |
| `dataset.py` | 1,077 lines |
