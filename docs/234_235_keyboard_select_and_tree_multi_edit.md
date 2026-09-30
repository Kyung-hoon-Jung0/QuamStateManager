# docs/234–235: keyboard selection in Live Edit, and multi-edit in the Json tree

2026-09-30, customer question: "Live edit 혹은 Json tree에서 VS Code처럼
다중선택 + 일괄적용?"

## docs/234: Ctrl+Shift+↑/↓ in the Live-Edit Qubits grid

Live Edit already had same-column multi-select (Shift+click / Ctrl+click),
Ctrl+D fill from the anchor, arithmetic over the selection (`*1.1`, `+10%`, …)
and multi-line paste. What was missing was the keyboard: Windows' extend
chord.

- **Ctrl+Shift+↓/↑ extends the selection one row per press, from the
  anchor.** Pressing back toward the anchor shrinks it again, which is the
  Shift+click contract. The caret moves with the edge.
- **A caret outside the current selection starts a new one there**, as in
  Excel, where moving the cursor collapses the selection.
- **The column rule is unchanged.** Rows are walked by `_gridMove`: same
  column, visible (search-filtered) rows, and read-only cells skipped.
- **Discoverability.** The selection hint names the chord, and so does the
  `?` sheet.
- **Scope.** The Pairs and TWPA grids have no multi-select at all (docs/111
  built it for the Qubits grid only), so this is Qubits-only too.

**Verified.**
- Real Chrome: three presses select four cells of qA2–qA5 `f_01` with the
  anchor at qA2; ↑ shrinks the selection to three; Ctrl+D fills; Ctrl+Z
  undoes; Esc clears; the top row is harmless; after a reload the page is
  intact with no JS errors.
- Pins in `grid_editing_selfcheck.cjs`. Both mutations were red: the chord
  removed, and "never restart".

## docs/235: multi-edit in the Json tree (Ctrl+D · Ctrl+Shift+L · Ctrl+H)

The tree is data, not text, so VS Code's features are translated rather than
copied.

- **Ctrl+D on a leaf** selects it. Each further Ctrl+D adds the same field of
  the next entity, in natural order (qA1 → qA2 → … → qA10). The entity level
  is found from the data: it is the outermost level where at least two
  siblings carry the rest of the path as a leaf.
- **Ctrl+Shift+L** selects that field in every entity.
- **Ctrl+H** opens the same panel on a path pattern where `*` matches one
  level, e.g. `qubits.*.xy.operations.x180_DragCosine.amplitude`. There is
  deliberately no text find-and-replace: rewriting digits inside values is
  how a number becomes a string or a pointer stops pointing.
- **One panel for all three.** It shows each field's current value and takes
  one input: an absolute value, or arithmetic on numbers (`*1.1  /2  +5e6
  -1e6  +10%`). Each row previews old → new +Δ before anything is written.
  **Apply** sends ONE atomic `/field/edit-batch` (`group: "new"`), which is one
  Review group and one Ctrl+Z.
- **Left out, each with its reason on its row:**
  - pointers (e.g. the `readout → #./readout_square` alias): edit the target;
  - read-only fields;
  - the FSP field, whose amplitude-compensation offer is per field;
  - for arithmetic, anything that is not a number.
- **Keys.** They act only inside the explorer's editable trees or the panel.
  Everywhere else Ctrl+D, Ctrl+H and Ctrl+Shift+L stay the browser's.
- **Collapsed branches.** Selection works from the tree's data
  (`_treeData`), so fields inside collapsed branches are selected too. Rows on
  screen are tinted, and rows built later (a branch opened) are tinted as
  they appear. The observer watches the selected tree only, and only while a
  selection exists.
- **Commit path.** The tree's own helpers (`_paintTreeLeaf`,
  `_policyReadOnly`, `_isPointer`) are exported and reused, so a batch
  paints and judges leaves exactly as the single-value editor does.

**Verified.**
- Real Chrome, customer chip copy:
  - Ctrl+D ×3 selects qA1, qA2, qA3; Ctrl+Shift+L selects all 21.
  - `*1.1` previews "Apply to 21"; Enter applies. All 21 amplitudes read ×1.1
    in the tree and the tray says "21 unapplied edits".
  - One Ctrl+Z brings the tray back to "In sync" ("Undone: 21 changes").
  - Ctrl+H on `qubits.*.resonator.operations.readout` shows all 21 rows left
    out as pointers.
  - Esc clears; after a reload the page is intact with no JS errors.
  - Screenshots were looked at.
- `tree_multi_selfcheck.cjs` (driven by `tests/test_tree_multi.py`). It covers
  pattern logic, natural order, the arithmetic grammar, the keys (including
  Ctrl+D and Ctrl+H outside a tree being left alone), each exclusion reason,
  the single batch and its body, the model paint, and a refused batch writing
  nothing.
- **Mutation check.** Red: no wildcard, the key guard removed, the pointer,
  FSP and read-only reasons removed, no undo group. The "not a number" reason
  for arithmetic on text is doubly guarded: removing it still leaves the row
  out as "result is not a number", which is redundant, not vacuous.
