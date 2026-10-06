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

Three more records follow the row in the same call (`remapRegenRecords`):
- the populate-protect baseline and the touched / filled cells, so the server
  diffs each row against its own source values;
- `spec.lines[].element` and `heldPins`, so a renamed qubit and its pairs keep
  the ports they are cabled to. Before this, a rename dropped every pin and
  re-allocation could pick other ports.

A deleted row's leftover record never shadows a qubit renamed onto its id:
`remapKeysBy` gives moved keys priority. A leftover is never sent, because
`qubitSourcesForBuild` filters to `spec.qubits`.

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
- every pointer;
- every string that IS a qubit id (`id`, a channel's `thread`, a TWPA's
  plain-name `qubits` list);
- qubit ids inside keys and pointer segments, `_`-delimited. For example
  `cz_SNZ_flux_pulse_q2_q1` becomes `cz_SNZ_flux_pulse_q1_q0`, the name a
  fresh build gives that pair's pulse.

`extras` is free-form and kept verbatim (docs/81). Its pointers' entity
segments still follow, because a pointer is structure.

A pair with a renamed member takes the id the rebuild gave the pair with the
same (control, target).

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

## Pins

- `tests/test_regen_qubit_rename.py` (22): shift, swap, rename onto a removed
  id, the record's checks, purity, id-in-names / strings / lists, extras
  verbatim, an id inside a word, `run_regenerate` on disk, populate protection
  of a renamed row, the FSP offer, the route passing the record.
- `tests/generate_regen_rename_selfcheck.cjs` (via
  `test_generate_regen_rename.py`): hydrate record, the rename moving the
  record / baseline / touched cells, deleted-row priority, the POST body in
  both modes, the result rows.
- `generate_naming_selfcheck.cjs` F8 now pins the block SHOWN in Re-generate.

Mutation sweep: 17 mutations, 17 caught:
- 6 server, round 1;
- 6 server, round 2: token rename, plain strings, extras, displaced, pair
  remap, word boundary;
- 5 wizard: remap call, POST body, priority, result rows, touched cells.

## Not done (step 2)

Param History, Trends and the Calibration log key by path. After a rename,
`qubits.q1.*` history splices two physical qubits. Recording the rename in the
chip's history (and breaking or continuing each series at it) is the next
step.

Lab code that builds operation names from qubit names now finds the new names.
Code that hard-codes an old name does not.
