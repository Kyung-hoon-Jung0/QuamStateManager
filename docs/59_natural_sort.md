# 59 — Natural sort for qubit / pair ids

**Feedback:** labs numbering qubits `q1 … q11` see `q1, q10, q11, q2, q3 …`
whenever a table sorts by name (Live State Edit header sort, Qubits page,
etc.). Chips with letter + single-digit ids (`qA1…qD2`) masked the bug —
natural and lexicographic orders coincide there.

## Root cause — two layers

1. **The default order everywhere** came from `loader.qubit_names` /
   `qubit_pair_names`, which were a plain `sorted(keys)` — so double-digit
   chips listed q10 before q2 *before any sort button was pressed* (bulk grid
   rows, Qubits/Pairs pages, sidebar, scheduler targets — everything that
   iterates the store's name lists).
2. **The client sort comparators** were plain string compares:
   `app.js`'s generic `.sortable` table sorter (Qubits / Pairs / Resonators /
   Flux / Couplers / Pulses tables), and the `__id__` branch in
   `bulk-edit.js` + `pair-edit.js` grid sorts.

## Fix

- `core/loader.py` gains a public `natural_key(name)` (digit runs compare
  numerically, text runs case-insensitively, raw string tie-break; the
  `re.split` alternation keeps tuple positions type-aligned so comparisons
  never mix int/str). Both name properties sort with it.
- The three client comparators use
  `localeCompare(b, undefined, {numeric: true, sensitivity: 'base'})` —
  the built-in natural collation.

## Tests

`tests/test_natural_sort.py` — key ordering (double-digit, lettered, pairs,
mixed shapes never raise), `QuamStore` name-list ordering on a synthetic
q1…q11 chip, and source pins asserting the three JS files keep
`numeric: true` (a plain comparator regressing re-introduces the bug).
