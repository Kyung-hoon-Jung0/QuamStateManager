# 73 — Undo navigation + visible server tier (r16 feedback ⓪-2 + ②, 2026-08-03)

Branch `feat/undo-nav`. User report: "I edited port values, pressed Ctrl+Z —
nothing happened; instead it changed a value I typed in ANOTHER tab." Plus ②:
the Review tray showed newly-added hierarchical keys as a bare "+ added"
without the values.

## The mechanism that was broken

The Ctrl+Z chain (docs/20 v2) is wizard → LiveEditUndo (in-memory, `.bulk-cell`
only) → server `POST /undo`. After any successful apply, `data-orig` is
rewritten to the applied value, so LiveEditUndo's audit-r10 drain skips its
whole stack as "staged", returns false, and the press falls through to the
SERVER tier — which pops the GLOBAL newest change-log group (`ChangeEntry`
carries no page provenance). If that group was committed by another page/tab,
the revert had **zero visible effect** on the current page (`_revertCell`
only patches a matching inspector form / Explorer node; the bulk re-GET skips
while cells are dirty). The user experienced "Ctrl+Z did nothing" while a
value elsewhere silently changed.

The audit-r10 drain itself is KEPT (deliberate: stale entries must never eat
presses) — the fix is making the server tier **visible and navigable**.

## Design (adversarially reviewed)

**No peek route.** Navigation is driven by the `/undo` RESPONSE — the
existing `HX-Trigger: cellsReverted {entries}` payload, now carrying
`source_file` + `deleted` per entry. A peek-then-undo design would race a
concurrent commit (undo something other than what was peeked); the response
is authoritative by construction and costs zero extra round trips.

**`UndoNav` (app.js)**, wired at the end of the `cellsReverted` listener:

- `visibleEl(dp)` — first VISIBLE owner of a dot-path on the current page
  (bulk cell / All-Values input / inspector inline-edit form / Explorer tree
  node; `getClientRects().length` gate — a hidden bulk column deliberately
  counts as NOT covered).
- **Covered** → flash in place (`leu-flash` + scroll-into-view), kept pending
  through the async `/bulk` re-GET so the highlight survives the swap
  (8 s expiry).
- **Not covered** → `ownerSurface(entries)` (anchor = the OLDEST entry, the
  same one the server toast names):
  - `qubits.<q>.**` single-entity → `/qubit/<q>?focus=<dp>` into
    **#inspector-pane** — #table-pane (and the user's typing in it) is never
    touched; `?focus=` scrolls + focuses the exact field.
  - `qubit_pairs.<p>.**` single-entity → `/pair/<p>?focus=<dp>` likewise.
  - multi-entity qubits/pairs → `/bulk` into #table-pane.
  - ports/octaves/mixers/twpas/wiring/top-level → Explorer via
    `_navigateToExplorerPath` (nav + lazy-expand + highlight + parent
    fallback; now explicitly window-bound).
- **Typing preservation** (the r16 hard requirement): before a #table-pane
  navigation, `stashDirtyInputs()` serializes in-progress typing
  (bulk cells value≠data-orig; inline-edit inputs value≠data-committed) into
  `sessionStorage["quam_undo_stash"]` (30 min TTL); `restorePass()` on every
  htmx:afterSwap refills any now-present inputs (dispatching `input` so the
  existing handlers re-mark them dirty) and consumes the stash. All-Values
  self-preserves via its dirty Map and is skipped. A one-shot
  `window._undoNavAt` stamp (4 s, the `_stateRestoredRefresh` precedent)
  bypasses the bulk/pair-grid "discard your edits?" beforeSwap confirms —
  the typing was stashed, not discarded, so the confirm would be a lie.

**Tray ↶ tooltip** now NAMES the next server-undo target: the newest tray
item's path + "+N more in this action" via the new
`data-group-id` on `.tray-change-item`.

## ② Review tray shows created/deleted VALUES

The change log always carried the full subtree (`create_subtree` /
`delete_subtree` deepcopy into `new_value`/`old_value`) — only the renderers
dropped it. `_pending_tray.html` + `_changes.html` now render:
- scalar create/delete → `+ <value>` / `− <value>` inline;
- mapping create/delete → the old summary as a `<details>` summary, expanding
  to a leaf list (`dot.path  value` rows) via the new `flatten_leaves` Jinja
  filter (cap 40, lists are leaf values — mirrors the merge walkers).

## Pins

- `tests/undo_nav_selfcheck.cjs` (real app.js in jsdom; U1–U9: visibility
  gate + hidden-column escape, owner mapping, covered flash-no-nav,
  inspector-pane deep link with no bypass stamp, stash→refill→consume,
  cellsReverted end-to-end, tooltip path + group count, Explorer routing)
  + driver `tests/test_undo_nav.py`.
- `tests/test_web.py::TestUndoNavServerR16` — /undo payload fields, tray
  `data-group-id`, created scalar shows the value (no bare "+ added"),
  created subtree lists leaves.
- docs/20's tier semantics unchanged; this doc amends its server-tier UX.
