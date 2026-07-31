# 64 — Global-Tab kill fix + Tab navigation + calculator polish (r8 feedback)

Date: 2026-07-31 · Branch: `fix/tab-focus-calc` (stacked on `feat/column-history-undo`)

User feedback batch:

1. Live Edit: Tab should move to the next cell.
2. Calculator (topbar): Tab should move to the next field; rows waste space —
   compact them but make the font BIGGER.
3. "어라? 지금 다시 보니까 SM 전역에서 tab키가 먹지 않는 것 같다. 원인 파악 부탁."
   — Tab seems dead EVERYWHERE in SM. Find the root cause.

## 1. Root cause of the global Tab kill

`trapFocus` (app.js) is a **document-level CAPTURE keydown handler**. Its Tab
branch does `if (!focusables.length) { e.preventDefault(); }` — correct while a
modal is open (keeps Tab from escaping a still-loading dialog), catastrophic if
the handler ever outlives its modal: a hidden container has no visible
focusables, so the orphan swallows **every Tab in the entire app** until reload.

The reachable leak: **Ctrl+K pressed while the palette was already open**.
The shortcut called `openCmdPalette()` unconditionally, and every open did
`pal._releaseTrap = trapFocus(pal)` — overwriting the stored release of the
previous trap *without calling it*. Sequence:

```
Ctrl+K   → trap A registered
Ctrl+K   → trap B registered, A's release LOST (leaked)
Escape   → closeCmdPalette releases B only
…        → palette hidden + orphan A still capturing → Tab dead app-wide
```

The same overwrite hazard existed at all five `trapFocus` call sites (review
overlay, drift overlay, plot-apply popup, palette, Column History card) — the
palette was merely the easiest to hit because a keyboard shortcut re-opens it.

## 2. Fix — leak-proof at the source (no caller discipline required)

`trapFocus` now has two independent defenses:

1. **Re-trap releases the previous trap.** The active release is ALSO stored
   internally as `container.__trapRelease`; a second `trapFocus(container)`
   calls it before registering. Double-open can no longer orphan a handler,
   no matter what the caller does with the returned release.
2. **Self-heal.** On any keydown the handler first checks
   `_trapContainerGone(container)` — detached (`!isConnected`), `[hidden]`,
   or invisible (`checkVisibility()` on Chromium/WebView2; fallback:
   offsetWidth, then a computed-`display:none` ancestor walk for engines
   without layout, incl. jsdom). A gone container ⇒ the handler detaches
   itself and swallows nothing. No focus restore on this path — the opener
   context is stale by then.

Plus the vector itself: **Ctrl+K is now a toggle** (open ⇄ close), which both
matches user expectation and removes the double-open entirely.

`release()` is idempotent; the deferred initial-focus rAF checks `released`
first. Nested traps (two DIFFERENT containers, e.g. popup over overlay) are
untouched — the internal marker is per-container.

## 3. Live Edit: Tab hops between edit cells

Both grids' delegated `keydown` (bulk-edit.js / pair-edit.js — kept in sync)
handle Tab/Shift+Tab via a new `_tabMove(cell, ±1)`:

- next/prev **edit cell** in the row — never the hover-reveal 🕘/apply buttons
  in between (native tab order stops on all of them);
- skips hidden columns (`.bulk-col-hidden`/`.bulk-search-hidden`) and, on the
  row edge, wraps to the adjacent **visible** row's first/last cell (hidden
  rows skipped);
- at the grid's very first/last cell it returns null and native Tab proceeds —
  focus can leave the grid;
- moved-to cells get `.select()` (type-to-replace, same as arrow nav).

Commit semantics are unchanged and intentional: Tab within a row does not
commit (focusout's same-row guard); Tab that leaves the row commits it — the
focusout handler's documented "Tab / click-away commits the row" behavior,
which was unreachable while Tab was globally dead.

## 4. Calculator: field-to-field Tab + compact rows + bigger type

- **Tab / Shift+Tab** inside the popover hop across the calc **inputs only**
  (`.calc-in`/`.calc-expr`), skipping summaries/copy buttons/links and any
  input inside a closed `<details>` (the popover's only hide mechanism —
  checked structurally via `closest('details:not([open])')`, engine-
  independent). Wraps around; Escape still closes; Enter on the expression
  still copies.
- **CSS** (`style.css`): base font 12.5px → **13.5px** (inputs/expression
  inherit), width 320 → 348px so rows don't flex-wrap into ragged gaps;
  vertical rhythm tightened (row/result margins 0.12 → 0.08rem, section
  padding 0.1/0.25 → 0.05/0.18rem, label padding 0.3 → 0.22rem, header/foot
  0.4 → 0.3rem, help 11 → 11.5px with tighter line-height).

## 5. Tests

`tests/tab_focus_selfcheck.cjs` (driven by `tests/test_tab_focus.py`; 28
checks) runs the REAL shipped JS under jsdom:

- **A. trapFocus leak class**: active trap traps / release frees / Escape →
  onEscape; double-trap + single release leaves no orphan; self-heal on own
  `display:none`, `[hidden]`, detach, and **ancestor**-hidden (the Column
  History card case) — each asserting Tab is alive afterwards.
- **B. the exact reported kill, end-to-end**: `Ctrl+K, Ctrl+K, Escape, Tab`
  → Tab alive; toggle semantics pinned.
- **C. grid Tab** on a real `BulkEdit.mount`: in-row hop, hidden-column skip,
  row-edge wrap, hidden-row skip, grid-edge native exit.
- **D. calc Tab**: hop, closed-section skip, wrap, Shift+Tab, opened section
  joining the loop.

`TestSourcePins` (node-free) greps the load-bearing lines: `__trapRelease`,
`_trapContainerGone` consulted in the handler, Ctrl+K's `closeCmdPalette()`
branch, `_tabMove` in BOTH grids, `details:not([open])` in calc.js.

Regression: ctrlz/bulk-search/ui-readability selfchecks + the ndview/dyncols/
search-perf/column-history pytest slices all green.
