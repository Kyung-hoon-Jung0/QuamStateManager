# 57 — ⚏ Qubits row picker (Live State Edit)

**Ask:** users want to choose which *qubits* (rows) the Live State Edit grid
shows — the panel previously only offered column selection (`≡ Properties`)
plus the typed search, whose row filtering is an ephemeral side-effect of
id tokens, not a deliberate scope.

## Design (why it looks the way it does)

- **Mirror of the Properties picker.** A `⚏ Qubits` `<details>` popover sits
  right next to `≡ Properties` and works identically (checkboxes, All/None/
  Invert, persisted hidden-set). Zero new interaction grammar to learn.
- **Chip map first.** Researchers think in chip positions and feedline rows.
  When ≥2 qubits carry a parseable `grid_location` (`"col,row"`, **row 0 =
  bottom** — the JS flips Y via `grid-row`), the popover renders a clickable
  mini chip map above the list; otherwise it degrades to list-only. The
  grouped list keys on the row letter (`qA1 → A`), giving per-feedline
  show/hide shortcuts on lettered chips, with a hover-revealed **only** button
  per qubit.
- **Default = everything visible; narrowed = loud.** The selection is a
  per-chip persisted **hidden-set** (`localStorage
  quam_bulk_qhidden:<active folder>`) — new qubits on a changed chip default
  to visible, stale ids are ignored (the column picker's exact semantics).
  Whenever rows are hidden, an amber **“N of M qubits — Show all”** pill sits
  in the toolbar; one click restores everything.
- **Unsaved edits can never vanish.** A row with a dirty cell is force-shown
  regardless of the selection; its picker entry is disabled with an “unsaved
  edit” note. “Apply all” therefore stays *apply what you see* — no invisible
  edit can ever be applied or lost.
- **The pair grid follows.** A pair row hides when either resolved member is
  hidden. Pair ids occur as both `qA1-qA2` and `qA2-A1`; each dash segment is
  resolved against the chip's real qubit ids (`A1 → qA1`) and anything
  unresolved **fails open** (the pair stays visible — never wrongly hidden).
- **Layered, like columns.** Rows now have the same two-layer visibility
  architecture columns already had: `bulk-qubit-off` (selection) composes
  independently with `bulk-row-hidden` (search). The search count shows what
  is actually on screen (both layers).
- **Stats follow the scope.** Per-column min/max header stats and the
  best/worst extreme colouring are computed over *visible* rows only —
  chip-wide extremes on hidden rows would mislead (or colour nothing
  visible).

## Sharp edge (deliberate)

`_refreshGlobal` (fires on every cell keystroke) re-runs only the **core**
visibility pass (`_applyQubitVisCore` + menu rebuild), never `applySearch`:
re-evaluating the search mid-edit could hide the very row being typed in the
moment its cell value stops matching a value token. Search re-evaluation
happens only on search input / picker / column-menu interactions, as before.

## Files

| File | Change |
|------|--------|
| `web/routes.py` (`bulk_edit`) | ships slim `qubit_meta = [{id, grid}]` (grid = `grid_location` verbatim or null) |
| `web/templates/_bulkedit.html` | `⚏ Qubits` popover + amber pill; `mount(...)` gains `{chip: active_name, qubits: qubit_meta}` |
| `web/static/bulk-edit.js` | picker menu (map + grouped list), `bulk-qubit-off` layer, dirty guard, pair follow, scoped stats, combined search count, `showAllQubits()` |
| `web/static/style.css` | `.bulk-qmap*`, `.bulk-qubit-pill`, `.bulk-qonly`, `.bulk-qdirty`, `.bulk-qubit-off` |
| `tests/test_bulk_edit.py` | picker/pill render, mount payload (chip key + grid), null-grid degrade |
