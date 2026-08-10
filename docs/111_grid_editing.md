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

## The audit (user-mandated, adversarial)

A 3-lens workflow (feature interactions · undo/events · test honesty) with
per-finding verification raised **22 findings**; the verification pass was
cut short by a provider limit, so every finding was triaged against the real
code by hand. **14 were real and are fixed**, each with its own pin:

- **F1 (major)** ctrl-click had no same-column guard — Ctrl+D could fill a T1
  value into an amplitude column. Both selection paths refuse a foreign
  column now. (A plain click only ANCHORS — it never paints a selection, so
  ordinary cell editing is visually unchanged.)
- **F2 (major)** the carry dispatched `input` BEFORE the fresh table's own
  listener was bound, so carried cells were never marked dirty and their row
  Apply stayed disabled. The carry now marks/refreshes directly — no
  ordering dependency.
- **F3 (major)** a carried edit whose column was COLD (docs/105 detached its
  inputs; the fresh pane starts at scrollLeft 0) was silently dropped. The
  carry hydrates when any path is missing, and anything still unplaceable is
  REPORTED ("N could not be restored"), never silently lost.
- **F4 (major)** pinning inline-styled every `[data-col-key]` — including the
  resize-handle span inside the header — killing drag-resize and auto-fit on
  pinned columns. Scoped to headers + cells.
- **F6** the qubit-name corner (`th.bulk-corner`) is a sort trigger too; the
  re-float listener missed it, so name-sorting scattered pinned rows.
- **F7** a read-only row INSIDE the paste range was counted as "beyond the
  last row" — the toast now names each reason separately.
- **F8** `window._dynReloadAt` was never cleared, leaving 4 s of unguarded
  pane swaps; cleared when the carry is consumed.
- **F9** three other `_reloadPane()` callers (the docs/85 "N hidden columns
  match — Show" chip, Show-all, Reset columns) still destroyed unsaved
  edits — all four paths carry now.
- **F10** the carve-out (4 s) and the carry TTL (15 s) disagreed, so a slow
  `/bulk` re-raised the exact discard confirm the carve-out exists to
  remove; ONE `CARRY_TTL_MS`.
- **F11** sticky insets were a one-shot px snapshot — font scale / bold /
  letter-spacing now re-derive them.
- **F12** pins stacked in CLICK order; sticky cannot reorder columns, so an
  out-of-order pair overlapped at rest. Sorted by DOM order.
- **F13** paste read each prev as it went, so a linked (shared-port) column
  mirrored the previous value into the next prev and Ctrl+Z converged on an
  intermediate. All prevs are snapshotted BEFORE any write.
- **F14** pasting into a cell the user had typed in was recorded twice (once
  by paste, once by the blur `change` listener). `LiveEditUndo.resync()`
  moves the focus snapshot forward.

**Accepted, not fixed** (recorded so no future audit re-raises them):
type-then-shift-click commits the anchor row through the pre-existing
row-exit commit (docs/64/75 pinned contract — the fill still works, the
anchor's own edit is simply staged as its own change); and the pre-existing
mixed FSP + stored-as-text 409 ack loop, which this feature makes more
reachable but does not introduce — it belongs to the `_applyCells` gate
chain and is tracked separately.
