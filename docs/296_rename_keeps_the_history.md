# 296 — A renamed qubit keeps its history

Step 2 of the customer's ask (docs/295: change qubit indices `q1, q2, …` to
`q0, q1, …`). docs/295 made Re-generate carry each qubit's calibration under
its new id. This doc makes every history surface follow it.

## The problem

Every history surface keys a value by its PATH or by the qubit name in a run.
After a shift rename, `qubits.q1.f_01` names the qubit that was q2. Without
more:

- the value drawer, Column History, Chip Status Trends and Param History joined
  two physical qubits into one series under `q1`;
- the event that adopted the rename listed thousands of fake changes (every
  path of every qubit "added", "removed" or "set" to another qubit's value);
- a Stage or Restore of a version from before the rename wrote the old names
  back (undoing the rename);
- a Datasets "Apply fitted value" from an old run wrote its `q1` fit onto
  today's `q1`, which is another qubit.

Measured on the real 21-qubit chip below: a version diff from before the rename
against now was 28,459 changes untranslated, and 1,255 translated -- exactly
the plain (un-renamed) rebuild's own changes.

## The record and the era

The rebuild writes a rename record into the rebuilt state's top-level
`extras.qubit_renames` (a list, oldest first, carried verbatim through every
later save and rebuild):

```
{"id", "at", "by": "re-generate",
 "qubits": {source id: new id},          # the renames, for display
 "pairs": {source pair: new pair},       # real pair renames
 "tokens", "pair_map",                   # the full maps the rebuild applied
 "source_qubits", "source_pairs",        # the source's ids
 "qubits_after", "pairs_after"}          # the rebuilt chip's ids and pair members
```

It is written only where the root class keeps `extras` (the merged state has
one, or the build env's schema of the root class has the field; stock
`BaseQuam` does). Otherwise the build result says history cannot follow.

A saved state's ERA is the tuple of record ids it carries. Every copy of the
chip carries its era: each run's saved state, every snapshot, the working
copy. Nothing is dated by a clock, so a run made on the old chip after the
rebuild was written is still the old era.

## The rule (`core/rename_lineage.py`)

One record is one `Step`; a chip's records are its `Lineage`. Between two eras
a path goes back to their common prefix, then forward.

- **Forward** (older -> newer) is the rebuild's own rule: `regen_merge.
  rename_source_qubits` / `_rewrite_ids` / `token_pattern`, the same functions
  docs/295 uses. A whole state moves forward through `Step.fwd_state`, which
  calls `rename_source_qubits` with the record's maps -- one implementation.
- **Backward** (newer -> older) is exact or `None`. A renamed qubit or pair maps
  to its source id. A qubit or pair the source did not have under any name has
  no history before the rename: its path translates to `None`, never to
  whatever the old chip kept under the same name. A source id renamed away (its
  id now names a brand-new qubit) is `None` too.
- **Values** follow the same rule: a pointer segment by segment, a pair id
  whole, an id inside a name by its tokens; below `extras` only pointers
  change. Long scalar lists are compared as recorded (the ledger keeps one hash
  for them), so `active_qubit_names` shows as changed where its names changed.

`Lineage.label` gives the renames between two eras for a surface to show.
`rename_lineage.remember` keeps a chip-level registry of every record seen
(`history/<chip>/qubit_renames.json`) so a state restored to before a rename
can still read the newer eras.

## Identity

`history.fingerprint_from_dicts` now names qubits and pairs in the BASE era
(before the first rename): renaming a qubit does not make another chip. So a
renamed rebuild routes to the same history folder, the ledger does not mark its
runs `chip uncertain`, and a full rename with no name in common stays one chip.
A qubit with no name before the rename is `+<id>` in the fingerprint.

## The surfaces

**The change ledger (the normal case: a chip with a data folder).**
`hub_eras.EraTimeline` reads each position's era from the ledger itself (the
rows of `extras.qubit_renames.<i>.id`).

- Value history (`value_history.read`): `holder_at` spells the asked path in
  each position's era, rename boundaries split the alias segments, and every
  value is compared in today's spelling. `points` follow the holder across
  renames (`recorded_as` names the old spelling); a value change exactly at the
  rename is a `set` of that qubit, never a pointer `via`. This one read serves
  the drawer, Column History (both tabs), Chip Status Trends and metric meta,
  the Param History grid and the agent API.
- Event lists (`hub_query.timeline`): an older event's rows are spelled today
  (`recorded_as` keeps the saved spelling); the event where a rename came into
  force has its rows recomputed qubit by qubit (`renamed_here`); the record's
  own ~250 leaves are folded into that one note; a path filter matches each
  event in its own era. This serves Param History Changes and the Calibration
  log (whose run targets are also shown in today's names, with the recorded
  names beside them and in the search text).
- Chip Status Trends lists every recorded path under today's name and marks
  each chart with a dotted `renamed` line; the drawer chart has the same mark.
- Versions / State History: Diff, Compare, Stage and Restore re-express a
  version from an older era in today's names first (`routes._side_today`).
  A restore never moves a value onto another qubit and never undoes the rename.
- The diff workbench (`/diff`, State and Wiring tabs, 2-way and N-way) moves
  every side saved before a rename into the newest side's era first
  (`routes._pairs_in_one_era`); sides of two different renames of one source
  are compared as recorded.

**The snapshot history (a chip with no change ledger).** It cuts at the rename
instead of joining: `HistoryManager.rename_keep` keeps only snapshots of the
chip's current era (a snapshot more than a day older than the first record
cannot carry it; a newer one is read once). The legacy drawer says how many
older snapshots it left out and points to linking a data folder.

**Datasets** (see the section below).

## Datasets

An experiment run saved before a rename names qubits by its old ids: in its
node.json parameters, its fit results and its saved state. `core/run_names.py`
holds the open chip's lineage (`ChipNames`) and one run's translation
(`RunNames`): a run's era comes from the ledger (`value_history.run_eras_known`,
one read per ledger version), else from its own saved state
(`rename_lineage.folder_era`). A chip never renamed reads no era at all.

Writes translate or refuse -- never guess:
- **Apply fitted value** (`fit_targets.resolve_fit_targets(names=)`): paths in
  today's names, the header says "(now q0)"; a fit of a qubit with no name
  today, or of a run whose era is unknown, is "Not applied: ..." with no Apply
  button.
- **Interactive clicks and the Data tab**: literal targets are translated
  (`run_names.translate_clickable`), `{q}` targets are filled from a `names`
  map; a click with any target that has no name today is refused (a toast).
- **Apply to chip**: the run's state moves forward (`forward_state`) before it
  is staged; if it cannot, 409 "Not loaded: ...".
- **Apply selected to chip**: each picked leaf is translated; one with no
  qubit today is a skip row with the reason.

Reads follow the qubit:
- **Datasets > Trends**: series keyed by today's names; the translation is part
  of the index tokens (an index built under another translation is rebuilt).
- **Compare's Trend Tracker**: an older-era run is moved forward first; one
  that cannot be is left out and named.
- **The Datasets table**: an older run carries `qn` / `pn` (today's names)
  beside `q` / `p` (as recorded). The cell shows today's name with "(as ...)".
  A qubit filter, the picker and a bare qubit id in the search name a qubit
  TODAY, so a run that recorded `q1` for another qubit is never matched; free
  text still finds the recorded spelling.
- **A run's Prev State diff** and every snapshot-to-snapshot diff
  (`history._diff_snapshot_dirs`, also the snapshot list's change summary)
  compare in one era (`rename_lineage.in_one_era`, the one rule the diff
  workbench uses too).

## Verified on a real chip

A real 21-qubit, 32-pair chip copy, rebuilt in the real build env with every
qubit renamed (letter-grouped ids to `q0..q20`, the step-1 rename); five run folders
copied from a real run (three before the rename, two after).

- The rebuild: 21 qubits and 32 pairs renamed, 0 values lost, the record
  written.
- Every one of 13,131 leaves of the renamed rebuild, read back through the
  lineage, lands on the same leaf of the plain rebuild with the same value
  (13,130; the one other is `active_qubit_names`, a hashed long list whose
  names did change). No leaf is reached twice, none is missed.
- A version diff from before the rename against now: 1,255 changes, the same
  path set as the plain rebuild's (28,459 untranslated).
- Real Chrome (`D:\work\sm_qa_rigs\renhist`): the drawer of `q1.f_01` reads the
  old values under its old id ("as <old id>") and the dotted mark; Column History rows
  `q0..q20` each read their own qubit; Chip Status Trends draws one continuous
  line per qubit with the `renamed` mark; the Calibration log shows the old
  day's runs in today's names ("recorded as <old id>"); the rename run lists only
  real changes; Param History Changes filtered on `q0`/`q1` finds the old rows.

## Review

An independent adversarial review confirmed the core (shift, swap, full
rename, rename onto a removed id, a qubit the rebuild added, a pair rebuilt
reversed, two renames, an old run after the rebuild, `_` in ids -- in tests and
in real Chrome) and reproduced 6 P1 and a set of P2 defects. All are fixed and
their repros are pins in `tests/test_rename_review.py`:

- P1: Chip Status answers were cached per history dir, so the source folder
  and the renamed rebuild (one dir, one `mutation_seq` 0) shared them: the
  table token now carries the open folder and the era.
- P1: the Versions quick-diff cache had the same collision, and a version
  newer than the open chip was compared as recorded: every diff and compare
  now puts its sides in one era (`routes._sides_in_one_era`, the one rule);
  Stage and Restore still only move an older version forward.
- P1: Apply selected translated the path, not the value (a `thread` or a
  pointer spelled in the run's names named another qubit): values follow
  (`RunNames.value`).
- P1: another chip's runs (in this chip's data folder) were translated by this
  chip's rename: only this chip's runs follow it -- the ledger's own chip
  verdict first, else the identity ladder on the run's saved state.
- P1: the Datasets delta poll shipped rows without today's names.
- P2: an old run after the rebuild was marked as a rename (three marks for
  one): a rename is marked only where a record first comes into force.
- P2: a swap whose values crossed had no raw row, so the event lists missed
  the change: every path the rename respells is a candidate there.
- P2: a record no state in reach carried (the source folder open, no
  registry) read as "removed": records are rebuilt from the ledger's own rows.
- P2: rebuild labels (`q0_removed`) were listed as paths and table names.
- P2: the Data tab refused a click on a run whose node.json names no qubits:
  every id of the run's era is in its names map.
- P2: the snapshot cut hid untouched qubits too and said "another qubit": it
  is per path now.
- P2: an unreadable registry was rewritten from nothing; a never-renamed pin
  blocked a function the code no longer called; the fingerprint JSON-dumped
  every record per call; wording (pair, "(as ...)", long rename lists).

Mutation sweep of the review fixes: 13 of 13 caught.

## Not changed / limits

- A long scalar list (more than 16 entries) is one hashed value in the ledger,
  so a list of qubit names shows as changed where the names changed.
- A qubit or pair the rebuild ADDS (no source qubit under any name) has no
  history before the rename, even when an older state had one of that id: it
  moves forward as `<id>_removed` (the conservative answer: never join).
- `/datasets/compare` compares runs' parameters and fit results by their
  recorded names (display only).
- Found and fixed beside this work (`tests/test_regen_active_lists.py`):
  Re-generate emptied `active_qubit_pair_names` on every rebuild, renamed or
  not (32 of 32 on the real chip), and re-activated every qubit -- the wizard
  has no control for the active lists, the build writes its defaults, and the
  merge took the new value for those membership keys. Each active list is now
  kept for the entities the rebuild still has (renamed ones under their new
  ids); an entity the source did not have takes the build's default. The real
  rebuild keeps 32 of 32 active pairs under their new ids.

## Pins

- `tests/test_rename_lineage.py`: the record, eras, one step both ways,
  between eras, the registry, the rebuild writing (or not writing) the record.
- `tests/test_rename_history.py`: the ledger read (drawer, Column History by
  run, the boundary change, a new qubit, an old run after the rebuild, a full
  rename, a swap with equal values), the event lists (Changes, Calibration
  log, path filters, the folded record), versions (Stage, Diff), Chip Status
  paths and marks, the snapshot cut (drawer, grid, Column History).
- `tests/test_rename_datasets.py` + `tests/rename_click_selfcheck.cjs`: every
  Datasets surface (writes translated or refused, Trends, the Trend Tracker,
  a chip never renamed reading nothing).
- `tests/rename_datasets_table_selfcheck.cjs` (via
  `test_rename_datasets_table.py`): the Datasets table's search and filters.
- `tests/generate_regen_rename_selfcheck.cjs` N10: the build result says
  whether history follows.

Mutation sweeps: 26 + 9 (this work), 34 (Datasets), 13 (review fixes), 3
(active lists): every one caught.
