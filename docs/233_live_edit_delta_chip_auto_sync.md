# docs/233: the before→after chip stayed on screen after an Auto-Sync apply

2026-09-30, customer report from Live State Edit with Auto-Sync on. A cell's
before→after chip ("→ 300 −2,700 (−90%)") stayed on screen and never went
away. Its "before" half was empty.

## Cause

The chip shows while the pointer is over a *modified* cell
(`td.bulk-ba-show`). Two paths could leave it shown.

1. **The hover handler hid only a cell that was still modified.**
   `_hoverBA(e, false)` began with
   `if (!cell.classList.contains('bulk-cell-modified')) return;`. That applied
   to hiding too, not only to showing. With Auto-Sync the edit is written to
   live, and unmarked, within a few hundred ms of Enter, while the pointer is
   still on the cell. The mouse-leave that followed therefore returned early.
   The same code exists in `bulk-edit.js` and `pair-edit.js`.
2. **Clearing the pending marker left the chip up.**
   `PendingMarkers.clearAll` and `clearPaths` run when the tray reports no
   pending change. They removed the mark and emptied the chip's "before" text,
   but not `bulk-ba-show`. That is why the stuck chip read "→ new Δ" with no
   "before".

## Fix

- **Leaving always hides.** The modified check now gates only *showing*.
- **Clearing a mark also hides that cell's chip.** This covers both
  `clearAll` and `clearPaths`.
- **CSS guard.** The show rule is
  `.bulk-td.bulk-ba-show:has(.bulk-cell-modified) .bulk-ba`. Whatever path
  unmarks a cell (Apply, Auto-Sync, Undo, a sync), its chip cannot outlive the
  mark.

## Verified

- **Real Chrome with Auto-Sync armed** (push + pull). Edit a qB3 cell with the
  pointer on it, press Enter, wait for the flush, move away.
  - Before the fix: the chip " → 564,810,877.0 −5,083,297,892.97 (−90%)"
    stayed, with an empty "before". This reproduces the customer's report
    exactly.
  - After the fix: no chip, even while the pointer is still on the cell.
- **Real Chrome, manual mode.** Hover shows "6,605,879,074.49 →
  6,671,937,865.0 +66,058,790.51 (+1%)". Leaving hides it and hovering shows it
  again. After pressing ↑ Apply the chip is gone even while hovering. After a
  reload the page is intact with no JS errors.
- **Pins.**
  - `bulk_markup_selfcheck.cjs`: a cell unmarked under the pointer loses its
    chip on mouse-leave, for both the qubit grid and the pair grid.
  - `pending_markers_selfcheck.cjs`: a shown chip is hidden with the mark.
  - `test_cust_0930_ui.py::test_the_chip_only_shows_on_a_modified_cell` pins
    the CSS guard; `test_chip_selfchecks` drives both selfchecks.
  - All three JS fixes were mutation-checked red.
