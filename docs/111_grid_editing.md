# 111 — The 21-qubit-retune toolkit: fill-down · paste · multi-select · pinning (docs/110 #11)

*2026-08-10. docs/104 #11, user-approved in the docs/110 campaign: bulk
retune across 21 qubits meant typing the same value 21 times; Excel muscle
memory (fill-down, paste a column) didn't work; enabling a dyn column
mid-edit destroyed unsaved edits behind a threatening confirm.*

Everything is **client-side in bulk-edit.js** — `/bulk` HTML is
byte-identical (every server pin holds); even the pin glyphs are
JS-injected at mount.

- **Multi-select** (same column only — a range across properties is
  meaningless): click anchors, shift-click extends within the column,
  Ctrl-click toggles, Escape clears. Cross-column ranges are refused.
- **Fill-down**: Ctrl+D fills the anchor's current value into the
  selection. ONE `LiveEditUndo` action ("fill-down (N cells)") — one
  Ctrl+Z takes it all back. Commit stays user-explicit (Apply).
- **Paste a column**: a multi-line clipboard (Excel column copy) fills
  downward from the focused cell through the VISIBLE rows; first tab-field
  per line (a 2-D Excel block pastes its first column); overflow beyond
  the last row is REPORTED in the toast, never silent. Single-value paste
  stays native. ONE undo action.
- **Pinning**: 📌 on every column header (sticky-left, cumulative offsets
  after the row-header column; a pinned COLD column is hydrated first —
  docs/105 virtualization) and on every qubit row (floats to the top,
  marked, re-floated after a sort). Persisted: columns globally
  (`quam_bulk_pinned_cols`), rows per chip
  (`quam_bulk_pinned_rows::<chipToken>`).
- **The dyn-column reload no longer destroys edits**: the toggle captures
  dirty cells before `_reloadPane()` and re-applies them into the fresh
  grid (announced: "N unsaved edits preserved"); the leave-confirm gets a
  carve-out (`_dynReloadAt`) because a discard warning over carried edits
  would be a lie — the same pattern as the UndoNav stash carve-out.

Deliberately not in scope: the pair grid (its Tab contract is its own
docs/64 work; the qubit grid is the retune surface) and drag-rectangle
selection (shift-click covers the workflow without drag-vs-text-select
ambiguity).

Pinned by `tests/grid_editing_selfcheck.cjs` (20, real bulk-edit.js under
jsdom) + `tests/test_grid_editing.py`; guard suites green
(`test_bulk_edit`, `test_bulk_virt`, `test_tab_focus`,
`bulk_virt/bulk_dyncols/tab_focus` selfchecks).
