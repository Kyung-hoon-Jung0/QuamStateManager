# 62 — Live State Edit search-typing performance

**Audit finding** (customer + maintainer both reproduced): typing keywords in
the Live State Edit search box is slow.

## Root cause — measured on a real customer chip

The server is innocent (`/bulk` renders in 0.11 s warm 0.06 s). The DOM is
the battleground: **153 columns × 29 rows ≈ 2,000 editable cells in a
2.6 MB table** (r7 made every dynamic column default-visible). Per keystroke:

1. `bulk-edit.js` ran `applySearch()` synchronously — rebuilding a lowercase
   text haystack for every cell (`closest()` + `toLowerCase()` ×2 per cell)
   and class-toggling ~2,000 `td`/`th` elements. While a partial token keeps
   changing matches ("f_" → "f_0" → "f_01"), nearly every keystroke moves
   column visibility → repeated full-table reflows.
2. `pair-edit.js` binds its OWN un-debounced listener to the same
   `#bulk-search` box — a second full scan + toggle pass per keystroke.
3. (Since docs/57) `_refreshGlobal` also rebuilt the ⚏ Qubits picker menu on
   every keystroke of a CELL edit.

## Fix

- **Debounce both listeners** (120 ms, one filter pass per typing pause) —
  bulk-edit and pair-edit each keep their own timer; the localStorage persist
  stays immediate.
- **Haystack cache** across keystrokes: `{key: hidden-column set, rowMap:
  WeakMap(row→haystacks), colHay}` — row-ELEMENT keyed so sorting never
  stales it. Invalidated centrally in `_refreshGlobal` (every cell input /
  commit / reset / mirror write funnels through it), on the JSON-cell badge
  write, and on mount (fresh DOM).
- **Dirty-signature gate** on the ⚏ picker refresh: `_applyQubitVisCore` +
  `_buildQubitMenu` run only when the dirty-ID *set* changes, not per
  keystroke.

Net effect per keystroke while typing: two full scans + reflows → zero
(work happens once, ~120 ms after the last key, with cached haystacks).

## Explorer

Audited alongside (same customer chip): `/explorer` renders 200 in 0.04 s /
0.11 MB — healthy; no action needed.

## Tests

`tests/bulk_search_selfcheck.cjs` (jsdom, behavioral): keystroke does NOT
filter synchronously; filter lands after the debounce; cached repeated
searches filter correctly; editing a cell invalidates the cache (the new
value is searchable); sorting doesn't stale the cache. Driven by
`test_live_edit_search_perf.py`, which also source-pins the pair-grid
debounce (a bare `applySearch` reference would silently reintroduce half
the cost).
