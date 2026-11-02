# 309 — Re-generate keeps the lab's own forms (2026-11-02/03)

Report: a port swap during Re-generate changed other keys and pointer strings, not only the swapped port.

## What changed (`core/regen_merge.py`, `core/regenerate.py`)

1. **The source chip's own pointer forms come back** (`MergeStats.source_forms_restored`). After the merge, a leaf
   where the source used a different form (a literal where the builder wrote a link, or another pointer spelling)
   is put back in the source's form when every value on the chip still resolves the same. Candidates are tried in
   halves, so one form that cannot land does not cost a full re-check per form (300 forms with one failure: < 5 s).
2. **A lab's separately calibrated literal beats a builder link** (`MergeStats.links_not_taken`). Example: the
   builder links `x90.alpha` to `x180.alpha`; a chip that calibrated them apart keeps its own number. Exceptions:
   a protected (Populate-edited) path keeps the link; and a source value EQUAL to the link target stays a link, so
   a Populate change of the target (e.g. a moved LO) reaches the linked field too.
3. **Lab wiring values under a rebuilt line are carried** (`graft_wiring_values`): plain values such as
   `dc_channel` under a line the rebuild also made. The rebuild's cabling stands; a pointer is never carried; a
   line the rebuild did not make brings nothing back.
4. **Ports a lab object or a spare output needs are carried** (`_carryable_ports`): a port only a lab top-level
   object (not a stock `quam.` class) points at, and a spare output on a FEM the spec still declares, of the
   matching kind (an analog port never lands on an MW FEM).
5. **Removing a qubit drops only its entry** inside a lab object (`_prune_removed_entries`): before, one channel
   pointing at the removed qubit's wiring dropped the whole object. A miss outside a removed entry still drops
   the object. Dropped leaves are listed in `residual_lost` ("not carried").
6. `wiring.json` is written once (TWPA renames and carried wiring values together).

## Verified

- Three real chips in their own lab env: an unchanged regen gives an identical `generate_config()`; a z-port swap
  moves only that port's two wiring references and its delay/filter.
- `Quam.load()` + `generate_config()` pass for every rebuilt chip. CZ nodes: identical simulated samples on
  QM-SaaS for five CZ nodes on one chip; identical compiled QUA for five nodes on another (no SaaS from that env).
- Two further chips: the SOURCE does not load in the available env, so only the rebuild was checked (it loads).
- The builder adds CZ operations for reversed pairs: structure added, nothing lost.

Pinned by `tests/test_regen_lab_shapes.py`, `tests/test_regen_merge.py`, `tests/test_regenerate.py`.

## Review round (2026-11-03)

An adversarial review reproduced three defects in the first cut, all fixed with pins:

- **Form restoration was O(refused x leaves)**: each refused candidate re-resolved the whole state. A 30-qubit
  chip (~273k leaves, 1,500 refused) did not finish in 500 s. Now only the subtrees a candidate is connected to
  (through pointers in either form) are re-checked: 0.96 s. Equality with the old algorithm checked on 50 seeds.
- **A removed qubit's literal-only entry stayed** inside a lab object (pruning ran only on a pointer miss). Entries
  keyed by a removed qubit or pair, or pointing at one, are now pruned whether or not they land.
- **Lab wiring values crossed a line-kind change** (QDAC -> LF-FEM kept `dc_channel`). Values carry only when the
  source and rebuilt line have the same port-reference keys. The suspected rename-order loss did not exist (the
  source is renamed before the carry); pinned.

Stress: `tests/test_regen_merge_fuzz.py`, seeded synthetic chips (200 seeds, 2-8 qubits): identity rebuild,
port swap, qubit removal, Populate protection, no new dangling pointer, determinism, merge < 2 s.
