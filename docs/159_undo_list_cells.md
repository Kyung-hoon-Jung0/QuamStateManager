# 159 — Ctrl+Z on a list cell shows on screen; the 🕘 reaches it

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (fourth commit)

## The report

> ctrl z, ctrl shift z 는 잘 작동하는데, exponential filter 처럼 list가 들어가는
> 경우에, "실제로는" 바뀌었는데, 눈에 보이는 live edit의 displaying value는 그대로
> 고정되어있어서 사용자가 "어? ctrl z가 안되네?"라고 생각하게 만듬.
> 그리고 exponential filter의 경우는 마우스 hover했을 때, 시계모양이 나오지 않음

## 1. Reproduced (curl against a copy of the CQT 20Q chip, then real Chrome)

`qubits.q2.z.opx_output.exponential_filter` is a port field behind the qubit's
io pointer. Edit it through `/field/edit-batch` (what the ✎ JSON editor posts)
and press Ctrl+Z:

```
edit-batch → resolved_path = ports.analog_outputs.con1.4.1.exponential_filter
/undo      → cellsReverted.entries = [{dot_path: "ports.analog_outputs.con1.4.1.exponential_filter",
                                       old_kind: "null"|"other", old_value_disp: "" | "[[0.5, 123.0]]"}]
```

The Live-Edit list cell is a preview `<span class="bulk-cell-list" data-path="qubits.q2.z.opx_output.exponential_filter">`.
`BulkEdit.revertPaths` matched it by `data-path` only — the ALIAS — while
`/undo` names the RESOLVED leaf (docs/124 C-2: it always has, and the scalar
inputs carry `data-resolved` for exactly this). So the span was `missing`,
and per the docs/141 §4e contract a missing path schedules no rebuild. The
value in the working copy moved; the cell did not. Even when a span WAS found
(a direct list leaf), the repaint deliberately left it `uncovered` for the
rebuild because it had no honest string to write: `old_value_disp` for a list
was `_bulk_display(list)` = `str(list)` — Python's repr, which no cell renders.

The 🕘: the shared value-history button docks on `focusin` of a `.bulk-cell`
input. The list cell is a span — not focusable, not a `.bulk-cell` — so nothing
ever docked.

## 2. What shipped

- **The payload ships the grid strings.** `_revert_entry_payload` classifies a
  list as `old_kind: "list"` and sends `old_value_disp` = the qubit grid's
  preview and `old_value_badge` = the pair grid's `▦ N×M` badge — from
  `_list_preview` / `_list_badge`, which are now the ONE function each that
  `_list_json_cell` / `_list_pair_cell` render with. A repainted cell is
  byte-identical to a fresh render.
- **The span carries the resolved leaf** (`data-resolved`) and both grids'
  `revertPaths` match it on that axis too; the detached-column index
  (`grid-virt.js`) records it as well, so a cold list column is found by
  either name.
- **A list revert is repainted in place and covered**: the span's text
  (qubit grid), the badge input's value + `data-orig` (pair grid). A revert
  that changes the cell's SHAPE (the field back to null / a scalar) stays
  `uncovered` so the debounced rebuild repaints it honestly — the M-10 rule.
- **The 🕘 docks on the list cell**: the span is `tabindex="0"`, the
  `focusin` dock accepts `.bulk-cell-list`, and the width measurement reads
  `textContent` where there is no `.value`. **Use** on a history point opens
  the ✎ JSON editor started from that value (`openJsonCell(path, btn,
  prefillText)`; a list's fill is compact JSON) — a text input to type into
  does not exist for a list, and silently doing nothing was the alternative.

## 3. Measured in real Chrome (headless, CDP, real Ctrl+Z / Ctrl+Shift+Z key events)

The column is server-cold on this chip (rendered empty, hydrated on scroll —
docs/141 §4n), which is the realistic shape; the span appears after the
scroll pass with `data-resolved="ports.analog_outputs.con1.4.1.exponential_filter"`.

| press | span text | tray |
|---|---|---|
| (seeded) | `[[0.5,123.0]]` | 1 |
| edit-batch `[[0.25,99],[0.1,5]]` (outside the ✎ editor, no repaint by design) | `[[0.5,123.0]]` | — |
| **Ctrl+Z** | `[[0.5,123.0]]` | 1 |
| **Ctrl+Shift+Z** | `[[0.25,99],[0.1,5]]` | 2 |
| **Ctrl+Z** | `[[0.5,123.0]]` | 1 |
| click the span | `#fh-cellbtn` docked inside the span's `<td>` | — |

One XHR per press (`/undo` / `/redo` plus the banner refreshers), no grid re-GET.

## 4. Pinned

- `tests/test_undo_list_cells.py` — the payload (kind, preview, badge; scalars
  untouched), the one-function rule (`_list_json_cell` / `_list_pair_cell`
  call the helpers), the markup (`data-resolved` + `tabindex` on the span,
  `data-list` on the pair input), the client contract, and an end-to-end
  edit→undo on a fixture with the io-pointer shape: the undo names the
  resolved leaf, the span carries it, and the reverted preview renders.
- `tests/undo_repaint_selfcheck.cjs` — the listedit case re-pinned to the
  new contract (resolved-leaf match → covered + repainted; a revert to null →
  found, uncovered), the pair badge repaint, a runtime cell still uncovered.
  **4/4 mutations red** (drop the resolved axis, drop the repaint, never
  cover the span, drop the badge write).
- `tests/test_revert_payload.py` — `[1, 2]` is `list` now, not `other`.
- Re-run green: `test_bulk_markup`, `test_bulk_edit`, `test_bulk_virt_server`,
  `test_undo_trail`, `test_ctrlz_client`, `test_pair_columns`, and the
  `ctrlz` / `bulk_dyncols` / `bulk_virt` / `bulk_virt_server` /
  `pair_virt_server` / `undo_pages` / `cellbtn` / `tab_focus` selfchecks.

## 5. Noticed, not changed

- The ✎ editor's own post-save preview is `JSON.stringify(parsed)` client-side
  (`99` for `99.0`) while the server renders Python's `json.dumps` (`99.0` when
  the stored value is a float). The next render corrects it; not this round.
- An `/field/edit-batch` from outside the ✎ editor does not repaint the list
  cell (only the tray) — the editor is the only in-app door and it repaints
  itself, so nothing user-visible depends on it.
