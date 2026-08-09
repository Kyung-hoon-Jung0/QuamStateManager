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

---

# Amendment (2026-08-09) — a derived column is IDENTITY; the row supplies the ADDRESS

**Report:** on a 10Q tunable-coupler chip, Live State Edit could not find
`exponential_filter`. Json Tree View found it immediately.

## Three defects, measured

1. **The entity-suffix fold minted names that exist nowhere.** The chip spells
   its two-qubit pulses `cz_flattop_pulse_q1_q2` while its declared pair id is
   `coupler_q1_q2` — not a suffix of it. `_strip_entity_suffix` is anchored and
   runs ONCE, so it removed only the trailing `_q2` and left
   `cz_flattop_pulse_q1`, a key **no qubit owns**.
2. **Cells addressed by formatting that template.** `routes.bulk_edit` did
   `path = spec["tmpl"].format(name=qid)`, so every mis-folded column resolved
   on zero rows: **252 of this chip's 256 "Z+" columns**, and **1,671 of 5,624
   derived columns across 40 real chips** (KRISS 21Q: 52 of 185).
3. **The cap then cut silently.** Those 252 ghosts sort ahead of the z Port+
   group, pushing the real `exponential_filter` column to index 419 — past the
   400 cap — and the cap's own truncation note is `kind="note"`, which `/bulk`
   filtered out. The column search only scans the model it is handed, so it
   reported nothing. Json Tree View walks the raw tree, which is why it was
   unaffected. Fixed separately by raising the cap to 1200 and rendering the
   note (that change is what unblocked the user).

Measurement note: the fold **bought nothing** on this chip. Single-strip and
no-strip both yield exactly 452 columns with an identical row-coverage
histogram — 0 columns saved, 234 broken.

## The fix — the pair grid had already solved it

`pair_columns.derive_pair_columns` has returned `(columns, path_map)` since it
shipped, where `path_map = {pair_id: {col_key: (real_dot_path, mode)}}`, and
`_pair_bulk_grid` looks the path up per (row, column) with no `.format()`
anywhere. `qubit_columns._make_leaf` already *received* `real_segs` and threw
it away, using it only to pick a unit.

So the qubit grid now carries the same thing, kept **on the column** so the
`(columns, curated_tmpls)` return shape every caller unpacks is unchanged:

* `paths` — `{qid: real_dot_path}`. Absent qid ⇒ that qubit does not carry the
  leaf ⇒ `_empty_pair_cell()`: a blank with **no input**, deliberately distinct
  from *declared but null*, which stays fillable.
* `modes` — `{qid: "edit"|"runtime"|"listedit"}`, per row.
* `multi` — how many operations some qubit owns under this folded name.

**Mode had to follow the pointer CHAIN.** `_kind_of` judged the local value
only, which is harmless while a column is dead and dangerous the moment it
resolves: a cross-ref landing on `#./inferred_*` would have rendered an
editable box over a value the chip computes, and committing it writes a literal
that kills inference. Measured at **94 such cells** corpus-wide (18 on the
reporting chip, 33 on a 21Q CR chip) — all invisible today precisely because
the column is dead.

**Ambiguity is disclosed, not unfolded.** A qubit in two pairs owns both
`cr_square_qA2-qA1` and `cr_square_qA2-qA3` under one folded name. The cell
addresses the first in walk order (the pair grid's rule) and the header wears a
`⁺` whose tooltip says how many there are and that the cell shows the first.
Unfolding those columns instead was implemented and **rejected on measurement**:
it is honest but it takes a 21-qubit CR chip from 115 columns to 925.

## Extension-shaped, proven cell by cell

The binding constraint was that supporting this chip must not change what any
existing chip does. Verified by loading main's `qubit_columns.py` side by side
with the branch's and comparing what **every cell of every column of every
chip** addresses:

```
40 chips · 5,624 derived columns
  columns per chip      identical on every chip (452/452, 185/185, …)
  cells that MOVED      0      (resolved before, addresses elsewhere now)
  cells BLANKED         0      (resolved before, no address now)
  columns LOST          0
  live cells            55,906 -> 60,713   (+4,807)
  dead columns          1,671  -> 344      (irreducible: runtime self-refs
                                            and genuinely dangling pointers)
```

`/bulk` also got *cheaper* on the reporting chip — 290 ms → 182 ms, 8.04 MB →
7.78 MB — because a cell the qubit does not own no longer ships an input.

Pins: `tests/test_qubit_columns.py::TestPerRowAddressing` / `TestPerRowKind` /
`TestFoldMultiplicity`, and `tests/test_bulk_edit.py::`
`TestPerNeighbourCellsAddressTheRealKey` (which asserts the ghost path is
*never* posted). The two pre-existing fold tests were pinning templates that
are dead in their own fixture — they still pass, because the fold itself is
deliberately unchanged.

## What the adversarial pass caught (and what it cost)

Three independent reviews plus a judge ran against the first version of this
change. They found **two blocking defects, both introduced by the fix itself**,
and both invisible to the addressing proof above — because in each case the
address was correct and the *rendering* was not:

* **A list-valued column's null rows became writable scalar boxes.** The row
  mode had *replaced* the column kind, and a `null` is neither `listedit` nor
  `is_list`, so a qubit whose `exponential_filter` is not set yet got a plain
  text input that happily stored a bare float where every sibling holds
  `[[amp, tau], …]`. **192 cells on 13 chips**, on the OPX filter taps and the
  readout discrimination data — including `exponential_filter`, the column this
  whole change exists to reach. The row mode is now a **floor**, never a
  replacement.
* **Tab parked in every read-only blank.** `_tabMove`/`_gridMove` returned the
  first `.bulk-cell` in a `<td>`, and the keydown handler `preventDefault()`s
  before focusing — which overrides the `tabindex="-1"` those inputs already
  carry. Harmless while ~2 % of cells were read-only; unusable at 63 %. Measured
  per row: an **already-supported** chip went from 12 read-only cells (longest
  run 2) to 43 (longest run 28); the reporting chip to 314 (run 48). Keyboard
  navigation now skips read-only cells in both grids, which is what the
  elements' own `tabindex` had been asking for all along.

Also taken from the review: the chain probe was structurally inert for every
port leaf (it was handed the alias path, which `_walk` cannot traverse — it now
gets the resolved `ports.*` path), the cache's shallow copy no longer isolated
the model once `paths`/`modes` made its values mutable, and — the same class as
the original report — **searching for the operation id the chip actually uses
found nothing**, because the header only carries the folded name. The real ids
now ride in a `search` field that feeds the column haystack; labels are
unchanged.

## The one deliberate behaviour change on existing chips

Per-row mode makes **123 cells across 10 already-supported chips** go from
editable to read-only `⟳` — almost all `qubits.<q>.xy.LO_frequency =
"#./upconverter_frequency"`. This is main's own bug being fixed: the column
kind is `runtime` only when **every** row is (`ks == {"runtime"}`), so a mixed
column put a text box over a self-ref, and committing it would have replaced
the pointer with a literal. Everything else measured identical:

```
cells whose address is unchanged, kind main -> branch
  edit -> runtime   123    (protective; see above)
  anything else       0
```

`MOVED / BLANKED / LOSTCOL` stay at 0 after every one of these fixes.
