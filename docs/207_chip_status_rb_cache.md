# docs/207 — Chip Status stopped paying an archive sweep for derived RB fidelity

2026-09-25, branch `fix/qa3-csperf`, merged `db0a6cf`. Found while profiling
Chip Status on the 4,160-run KH archive for the QA campaign (docs/205).

## 1. What was measured

`/topology` (Chip Status' main render, which derives a per-gate fidelity from
each Standard-RB row via `_topology_with_derived_rb`): **97–112 ms → 10–42
ms**, and the 3 s persisted store-cache dump (which encodes a 16 MB JSON
index while holding the GIL) stopped stalling overlapping requests.

## 2. Cause

The docs/155 fix had already cut the sweep to ONE per render instead of one
per edge, but "one sweep" on a 4,160-run archive still meant re-scanning
every dataset store's date directories on every single Chip Status render —
the RAM-resident run index was never consulted first.

## 3. Fix (`7ccc3ce`)

- `_rb_cached_values`: derived per-gate values are cached by
  `(folder, pair, mtime_ns, size)`, bounded to 512 entries, LRU-evicted.
- `_resolve_rb_run`: run-folder lookups are cached by run id with a 60 s miss
  TTL, first tried against the RAM index (`_active_dataset_stores(fast=True,
  rescan=False)`) rather than a sweep; only a genuine miss triggers ONE
  archive rescan, shared by every row in that render.
- The persisted store-cache debounce moved **3 s → 30 s** — the persisted
  index is only an accelerator (docs/171), so losing 30 s to a crash costs a
  re-verify, not correctness.
- Validation: 97 tests across RB levels, the dataset store cache, lazy
  scaling, Chip Status and the new RB-cache regressions; all three new tests
  fail against the original helper, and disabling file-version invalidation
  fails its own regression test.

## 4. Folder-scoped cache key (`25838bb`)

Run ids are unique only WITHIN a data folder. Keyed by id alone, chip B's
run #7 was served the folder — and value — resolved for chip A's #7. The
cache key now carries the sorted set of data folders the chip actually
reads from (`scope`), so two chips never share a slot. Pinned by
`test_run_ids_are_scoped_to_the_chips_folders` (reverting the key change
turns it red).

## 5. Lazy store lookup restored (`e8c410b`)

The full-suite run on `qa/integration` caught `TestRbDerivationSweepsOnce`
counting every RAM-index store call as a sweep (it should count only
ARCHIVE rescans) and sharing a module-level memo across tests, which hid a
real regression: the folder-scoping change above made the in-memory store
list resolve EAGERLY on every render, even for a chip whose Clifford rows
carry no `load_id` at all. The lookup is lazy again — the store list is
built only inside `_resolve_rb_run`, on the first id actually seen — so a
chip with nothing to derive makes no store call whatsoever. New pin
(`test_nothing_to_derive_does_not_even_look_in_memory`) kills the eager
mutant; the existing sweep-count pins now clear the memo first and count
rescans only.

## 6. Pins

`tests/test_chip_status_rb_cache.py` (4: second render neither rescans nor
rereads unchanged fits; unknown ids share one sweep and remember misses;
folder-scoping) + `tests/test_share_io_cost.py::TestRbDerivationSweepsOnce`
(rescan counting, the lazy-lookup regression pin, the enrichment-still-
happens pin allowing the one documented retry).
