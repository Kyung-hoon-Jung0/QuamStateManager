# 295 — Re-generate: a renamed qubit is the same qubit

## The ask

A customer asked whether SM can change a chip's qubit indices from `q1, q2, …`
to `q0, q1, …`. The Generate wizard already had a naming scheme (step 4:
`q1…`, `q0…`, grid, custom) and per-qubit rename inputs, but Re-generate hid
both. That was deliberate: the value merge matched the source chip and the
rebuild by PATH, so a rename moved calibration by NAME.

Measured with the merge directly (toy chip, q1/q2 renamed to q0/q1):

| new qubit | physically | `f_01` it got | should be |
|---|---|---|---|
| q0 | old q1 | build default | old q1's |
| q1 | old q2 | **old q1's** | old q2's |

Old q2's values were reported only as "not carried". Pairs had the same
problem: `_reconcile_pair_ids` matched by member NAME.

## What changed

**Identity is the wizard row, not the name.** Re-generate keeps
`state.regenQubitSource = {current id: source id}`. It starts as the identity
at hydrate. `applyQubitIdMap` (the one door for every rename: scheme Apply,
per-qubit rename, renumber) moves it with the row. The build POST sends it as
`qubit_sources`.

More records follow the row in the same call (`remapRegenRecords`):
- the populate-protect baseline and the touched / filled cells, so the server
  diffs each row against its own source values;
- `spec.lines[].element` and `heldPins`, so a renamed qubit and its pairs keep
  the ports they are cabled to. Before this, a rename dropped every pin and
  re-allocation could pick other ports;
- each source pair's orientation record (`regenPairOrient`, QA r2-09), so a
  renamed pair is not read as reversed.

A row deleted on the board takes its record into the undo snapshot, and Undo
gives it back. A row the count drops loses its record, and a row the count
adds has none, so a new row never inherits a deleted qubit. A port-CSV import
replaces the chip and clears the record. `remapKeysBy` gives moved keys
priority, so a stale key never shadows a qubit renamed onto its id.

"Reset step" stays consistent after a rename:
- Step 4 snapshots carry the records (`snap.ids`), so a reset of step 4
  returns the source names together with their records, and Ctrl+Z undoes
  both.
- Steps 3, 5 and 6 are restored from the source chip in source names, and
  `regenRekeyRestored` puts them back in the current names. Pins, values and
  touched cells stay on their own qubit. A source qubit whose name a renamed
  row took is dropped, never handed to that row.

**The server re-expresses the source in the new ids before anything matches.**
`regen_merge.source_renames` checks the record. It drops:
- an unknown source;
- a source claimed twice;
- a target the build lacks;
- a non-string.

A record that cannot be checked falls back to matching by id, which is today's
behaviour.

`regen_merge.rename_source_qubits` then rewrites the source state and wiring:
- qubit keys;
- every pointer, including one kept under `extras`, which still points into
  the renamed chip;
- qubit ids inside keys AND string values, `_`-delimited, by one rule. For
  example `cz_SNZ_flux_pulse_q2_q1` becomes `cz_SNZ_flux_pulse_q1_q0`, the
  name a fresh build gives that pair's pulse. A value names an operation as
  often as a key does: a lab CZ plays `pulse.id`, the string naming its
  operation. With keys renamed and that string not, 0 of 169 macro pulses
  resolved on a real chip, and a swap silently played the other pair's pulse.
  `id`, `thread` and a TWPA's plain-name `qubits` list follow the same rule.

The pattern holds EVERY source id, longest first, so `q1_b` is its own qubit
and is never read as `q1` + `_b`. A key that would collide is never merged
into another.

Other `extras` keys and values are free-form and kept verbatim (docs/81).

A pair with a renamed (or removed-and-reused) member takes the id the rebuild
gave the pair with the same (control, target). With no such pair (deleted, or
rebuilt reversed) it is held as `"<id>_source"`: its old id now names a
different pair, or none, and a NEW pair the user added under that id must not
inherit it. A pair rebuilt reversed is reported as `q1-q2_source`. The
source-drift check (`source_drift`, QA r2-35) re-keys the source the same way,
so each renamed row is compared with its own values.

A source qubit the user REMOVED, whose id a rename now uses, is held as
`<id>_removed`. Its values stay "not carried", and its partner pulses
(`cz_flattop_pulse_q1_removed`) cannot take the renamed qubit's names.

Both `run_regenerate` and `pending_fsp_offers` apply the rename. The FSP offer
for a renamed row is found through its source qubit. The build result lists
`qubits_renamed` / `pairs_renamed`.

## Verified on a real 21-qubit chip (OPX1000, 4 MW + 4 LF FEMs, 32 pairs, 4 TWPAs)

Real Chrome, a real customer build env (quam 0.6.0 / quam_builder 0.4.0):
1. step 4, scheme `q0, q1, …`, Apply names;
2. step 5 re-allocated every line on the source's own ports;
3. Generate.

Then the same chip was rebuilt WITHOUT the rename. An independent comparison script
(no shared code) renamed the plain rebuild's ids
and compared it leaf by leaf with the renamed rebuild:

- state: 10,441 vs 10,441 leaves, 0 only-in-one, **0 differing**
- wiring: 91 vs 91 leaves, **0 differing**
- both load through the lab's own root class and `generate_config()` gives 71
  elements / 729 operations on each.

The first real run, before the key / string rule existed, left the old ids in
three places: 42 `thread` values, 21 TWPA list entries, and 192 leaves of lab
pulse names. That run is what defined the rule.

## Review

An adversarial review of the first cut reproduced 4 P0, 2 P1 and 3 P2 by
running them. All are fixed and pinned above:
- P0: the CZ `pulse.id` strings;
- P0: Reset step 4 / 5 / 6 after a rename;
- P0: a new pair inheriting a twin-less old pair's id;
- P0: `_` inside ids;
- P1: source drift;
- P1: the pair-orientation record;
- P2: a reversed pair reported as removed;
- P2: a leftover record sent for a new row;
- P2: an `extras` pointer left dangling.

The first real-chip comparison shared its renaming rule with the code, so it
could not see the `pulse.id` defect. The re-verification adds a rule-free
check: each PHYSICAL pair's macro pulses, followed by pointer, in the plain vs
the renamed rebuild.
- 32 links, 0 missing, 0 differing values.
- Every `pulse.id` equals its operation key.
- The review's own count: 168/169 macro pulses resolve, the same as the
  source, whose one miss is a broken reference already in it.

In real Chrome: after a rename, Reset step 5 kept every qubit on its own
ports. Reset step 4 returned the names with their records.

A second round against those fixes closed every first-round finding. It
found four new defects, two of them regressions from the first cut:
- P0: board delete, then a rename onto that id, then Undo, overwrote the
  renamed row's record. Undo now restores a record only with the row it
  re-inserts, and never over a key another row holds.
- P1: the count down, then Ctrl+Z, re-added the row without its record.
  A dropped row's record is now kept aside, and the same id re-added gets it
  back. The count cannot tell "undo" from "raise again", and a re-added id
  is the same qubit everywhere else (the line inventory, the by-id merge).
  Any other added row has no record.
- P2: the pair label first carried a space, and the report reads a line's
  owner up to the first space. It is now `_source`.
- P2: a key collision kept a renamed key under its OLD name when a leftover
  key already had the new one. The renamed key now wins, and the leftover
  (which names a qubit the source does not have) steps aside as
  `<key>_stale`, in either key order.

The round also scanned 51 real chips for string leaves the value rule changes.
Only ids, `thread`, TWPA `qubits`, active-name lists, `core` labels and
`pulse.id` change, all consistently. Generate mode is unchanged: 13 state
snapshots match the pre-feature JS, and the POST only gains
`qubit_sources: null`.

## Pins

- `tests/test_regen_qubit_rename.py` (28):
  - shift, swap, rename onto a removed id;
  - the record's checks, purity;
  - ids in names / strings / lists, extras verbatim, an id inside a word;
  - `run_regenerate` on disk, populate protection of a renamed row, the FSP
    offer, the route passing the record;
  - the review findings: `pulse.id` after a swap, the twin-less pair id, the
    reversed pair's report, `_` ids, the extras pointer, source drift.
- `tests/generate_regen_rename_selfcheck.cjs` (via
  `test_generate_regen_rename.py`):
  - the hydrate record;
  - the rename moving the record / baseline / touched cells;
  - deleted-row priority;
  - the POST body in both modes;
  - the result rows;
  - Reset step 4 / 5 / 6;
  - the pair-orientation record;
  - board delete + undo, count truncation, the CSV import.
- `generate_naming_selfcheck.cjs` F8 now pins the block SHOWN in Re-generate.

Second-round pins: the source pair's report group, the leftover-key collision
in both orders, N8b-N8d (count down / Ctrl+Z, delete-rename-undo).

Mutation sweep: 33 mutations, 33 caught (4 more for the second round):
- 7 + 6 server before review (one is the FSP offer);
- 5 server review fixes;
- 5 + 6 wizard (one, count truncation, was missed and got its own pin).

## Not done (step 2)

Param History, Trends and the Calibration log key by path. After a rename,
`qubits.q1.*` history splices two physical qubits. Recording the rename in the
chip's history (and breaking or continuing each series at it) is the next
step.

Lab code that builds operation names from qubit names now finds the new names.
Code that hard-codes an old name does not.
