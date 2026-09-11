# docs/177 — An unapplied edit never vanishes, on the column axis too

2026-09-11. One of the findings from the Live-Edit stress round, fixed.

## The defect

`BulkEdit.applyAll` writes **every dirty cell in the table**:

```js
var rows = _rows().filter(function (tr) { return _cells(tr).some(_isDirty); });
```

`_cells` selects every `.bulk-cell` in the row. Neither the column picker's
`bulk-col-hidden` nor the search layer's `bulk-search-hidden` enters that
filter. So a dirty cell in a hidden column was written by a press whose confirm
read

> Apply 3 edits across 2 qubits to the working state?

while only two of those three were on screen. That is docs/120's rule —
*a press means what the presser could see* — broken on the write path.

## It was already the rule everywhere else

The **row** pickers have said exactly this since docs/141 §4s, in these words:

```js
var off = hid.has(id) && !_rowDirty(r);   // unsaved edits never vanish
…
if (off && _rowDirty(r)) off = false;      // an unsaved edit never vanishes
```

and the comment above them states the reason: *"Apply all" stays "apply what
you see"*. The column axis simply never learned it.

## The case that matters

It is **not** somebody hiding a column they just typed in — they would at least
remember. It is a **mirror write**: the coupled `f_01` ↔ `xy.RF_frequency` twin,
or an FSP+amplitudes bundle (docs/160 §5e), making an **off-screen** column dirty
on its own, with no picker click anywhere near it. That is why the force-show
cannot live only on the picker's change handler — it has to sit on the same
`_refreshGlobal` gate the row rule uses.

## The fix

- `_dirtyColKeys()` — the columns holding an unapplied edit.
- `_effectiveHidden()` = the user's choice **minus** those. `_hiddenSet()` stays
  the choice itself: it is what the picker checkboxes show and what persists, and
  a forced column must not silently un-tick its own box. The choice is
  *overridden*, not forgotten — the column hides again the moment nothing is
  unapplied.
- `_applyColumnVisCore()` — a core pass with **no** `applySearch`, for the same
  reason the row core has none. It also clears a **stale** search verdict on a
  forced column: while the column was checkbox-hidden the search skipped it
  entirely, so whatever class its header carried from before was still there.
- The search layer forces `colSearchHide[k] = false` for a dirty column. A search
  is a question about what to *look at*; it must not decide what a later press
  writes unseen.
- `_recomputeStats` reads the effective set — a forced column is on screen and
  counts.
- The `_refreshGlobal` gate mirrors the row one, and is asymmetric on purpose:
  a key **arriving** in the dirty set only runs the core pass, because re-running
  the search on the first character typed into a cell can hide the very row being
  edited the moment its value stops matching a value token. A key **leaving**
  (applied, reset, undone) also re-runs `applySearch`, since only the search
  knows where a no-longer-forced column belongs.

## Verification

`tests/bulk_dirtycol_selfcheck.cjs` — 16 assertions against the real shipped
`bulk-edit.js` under jsdom, driven by `tests/test_bulk_dirty_columns.py`. The
invariant assertion is the one to keep: *no dirty cell sits in a column the
presser cannot see*, re-checked after each layer.

### A pin whose fixture predated the rule

`bulk_search_selfcheck.cjs` F6 went red — it asserts that a query hides `f_01`,
and section C of the same file had left an **unapplied edit** in that very
column. The rule is right and the assertion is right; the fixture carried an
edit it never meant to. Section C's edit is put back before section F now.
Worth recording rather than quietly patching: the old fixture is evidence the
defect was reachable by accident, in three lines of test setup.
