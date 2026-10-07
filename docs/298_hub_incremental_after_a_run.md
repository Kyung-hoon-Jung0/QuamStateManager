# 298 — The ledger surfaces after a run, an undo or an edit: derive only what changed

**Date:** 2026-10-08 · **Trigger:** the two items docs/292 left open. After every new run each
ledger surface (Chip Status Trends, the metric meta, the Param History grid and its cell drawer)
re-read every point of every path it shows; and a staged, unapplied edit of ANY field recomputed
everything too, because every kept part was keyed on the chip's `mutation_seq`.

**Result.** Each path's answer is now kept between reads and served again only when the ledger
PROVES nothing it was derived from changed; each surface part is kept with the facts of the chip's
state it read and served again after an edit while they still read the same. Every served answer
is byte-identical to a from-scratch answer: 36 random sequences (1,050 operations, on synthetic chips and a copy of a real archive), **23,622 comparisons, 0 mismatches** (§3). The harness found two real
defects on the way, both fixed and pinned (§3). Mutation sweep **42 / 42 RED**, every pin-claimed rule. Fault injection:
10 faults, 80 surface comparisons, 0 mismatches. On a 10,000-run chip, after one new run: Trends
1,638 -> 149 ms, the grid 835 -> 112 ms, the meta 1,031 -> 86 ms; after a staged edit of an
unrelated field: Trends 1,176 -> 19 ms (§7). Real Chrome on a copy of a real archive: ALL OK (§8).

## 1. Spec (written before the implementation; amended where the harness proved it wrong, §3)

### 1.1 What is kept

| Layer | Where | Kept | Valid while |
|---|---|---|---|
| a path's answer | `value_history.read`, `_KEYS` | one entry per (chip, the target's holder and every pointer hop on the way, drawer limit, rename scope): the answer, the ledger state it was derived at (its *stamp*), every holder spelling the derivation asked about (found or not, with a list element's list), the events whose facts it read | §1.2 rules 1-4 |
| a presented row | `hub_status._ROWS` | a curated (qubit, property) row or a leaf series with each point's words | the SERIALS of the per-path answers it was built from (a serial names one answer object) and the dataset roots its data links resolve against |
| a surface part | `hub_status._CACHE` (the memo docs/283 added, validated differently now) | the Trends fragment, the curated rows, the metric meta, the Changes list, the pair badges, the rename marks | the ledger version, the roots, the sync status and the open folder are the same, and every recorded FACT of the chip's state still reads the same (§1.3) |

Nothing else is kept that was not kept before; the read index (docs/279) is unchanged.

### 1.2 A path's answer: when a later ledger state may serve it

A stamp is `(ledger id, reader opening, data version, rewrite-log mark, newest eid, number of
events, first event)`. An equal stamp means nothing changed at all. Otherwise the kept answer is
served only when ALL of these hold against the read snapshot:

1. **The same ledger file, reader opening and rewrite log** (its epoch), and the log still covers
   the answer's position (not trimmed past it, not gone back to an earlier position).
2. **No in-place change of what it read.** The ledger's REWRITE LOG (below) is read for every
   change logged since the answer's position among the events up to the newest one it saw:
   * something that moves every event -- an event deleted, the order (`ord`) changed, a root, a
     path or a blob changed, a rename-era row rewritten -- invalidates every answer;
   * a change row of an existing event inserted, updated or deleted (a re-diff after a late run or
     a rewritten run folder, a re-proof after `node.json` changed) invalidates the answers that
     asked about that holder spelling;
   * an event's own facts updated (a flag: UNDONE / PARTLY_UNDONE by an undo, SOURCE_GONE when a
     run folder is deleted, REWRITTEN, OVERLAPS_SM_WRITE; its status or time) or its SM facts
     changed invalidates the answers that read that event (it has a row on a holder the answer
     asked about, or a segment of the answer starts there).
3. **The same events up to its newest one** -- counted (a removed event is logged too; the count
   is the belt to the log's braces) -- and **the same first event** (every derivation reads the
   state at position 0, rows or not).
4. **No row of an event added since on a holder spelling it asked about**, wherever that event
   was placed (at the head, or between two earlier ones: a late run, a snapshot SM took before an
   apply), and no added event moved a rename era.

The rewrite log is kept by SQLite TRIGGERS in the ledger file (`hub_store`), so a write by any
process -- another SM window, an older SM build -- is logged. Each entry is `(eid, pid)` for a
change row, `(eid, NULL)` for an event's own facts, `(0, NULL)` for what moves every event. A
reader only asks about events up to the newest one it saw, so an append (whose entries name only
the new event) never invalidates anything. The log has an epoch, drawn anew whenever a writer
(re)creates it, and a version: a ledger whose log is missing, incomplete or of other trigger
definitions vouches for nothing until the next writer remakes it (then under a new epoch). It is
trimmed past 65,536 entries; a reader stamped before the trim re-derives. Measured on a new run
and an apply: no entry for an event a reader saw; an undo logs the write it took back; a deleted
run folder logs its event.

**Why the answer is identical.** A key's derivation (docs/282: alias segments by `holder_at`,
the holder's rows, the in-force series, the undo marks, the rename marks) reads the ledger only
through the rows of the holder spellings it asks about, the events those rows belong to (their
fields and SM facts), the events at its segment starts, the roots table, the blobs of long
arrays, the rename-era timeline and position 0. Rules 2-4 cover each of these: a row that is not
added, removed or changed, of an event whose facts did not change, in an order that did not
change, with no new row on any spelling the walk asked about (so `holder_at` takes the same steps
at every position it evaluates, and no new breakpoint appears), gives the same derivation.
Positions move when an event lands between two others, but no answer carries a position, only
eids, times and ords, which do not move. A key's answer depends only on its own target, so a
subset re-derived among kept ones is the answer it gets among all of them.

### 1.3 A surface part: when an edit of the chip may serve it

A part records every fact of the chip's CURRENT state its compute read, through the table:

| Fact | Read by | Evaluated as |
|---|---|---|
| `targets` (paths) | every ledger read (`series_many`) | each path's holder now and every pointer hop (`value_history.target_sig`) -- an alias retargeted by an edit moves it |
| `currents` (paths) | the metric meta (`matches_current`) | each path's value now, in the ledger's own terms |
| `qubits` | the curated rows | the qubit names |
| `ctarget` (base) | a confusion matrix behind an alias | the alias's holder now |
| `meta_paths` | the metric meta | the panel paths, enumerated through the one resolver, and the load ids beside them |

A part computed at another edit counter is served again when its token without the counter is
the same and every fact evaluates the same now; nested parts hand their facts to the part that
read them. A part computed while the chip changed is stored under a token no request holds (its
facts still say what it read). Two parts read the state outside these facts and stay recomputed on
every edit: a Trends fragment for a TYPED path (it asks the state whether the path names a leaf),
and the full Changes page, which is now rendered per request from the kept list (§3, found 2).

### 1.4 The list in the task, mapped

| Change | What happens |
|---|---|
| new events appended | rule 4: only paths with a row in them are derived again; every part over them recomputes from kept rows (`_ROWS`) |
| an old event rewritten by undo / source gone / any in-place change | rule 2, per path and per event, from the triggers' log |
| an alias retargeted | in a run: the pointer holder is a spelling the walk asked about (rule 4 / rule 2); by an edit: the target signature is the key of the kept answer, and the `targets` fact of every part over it |
| the current value (`matches_current`) | the `currents` fact of the meta only; Trends and the grid draw recorded points and do not read it |
| a chip switch | every slot names the chip's history folder and the open folder |
| a reload of the ledger from disk | a reopened reader is another reader opening (rule 1), and closing a reader drops that chip's kept answers |
| another SM process writing the ledger | the triggers run in its connection too (rule 2); its appends are rule 4 |

## 2. What was built

| File | What |
|---|---|
| `core/hub_store.py` | the rewrite log: `rewrite_log(seq, eid, pid)`, 16 triggers, the epoch and version in `meta`, created (or remade under a new epoch) by the schema transaction of the next writer; `rewrite_mark` / `rewritten_since` for readers. Additive: no schema version change; an older SM ignores the objects, and its writes still fire the triggers. |
| `core/value_history.py` | `read` split into `_derive` (docs/282's derivation, unchanged, for any subset of keys) and the kept answers: `_KEYS` (a `ramcache.KeyedMemo`, 96 MB, under the shared budget), `_stamp`, `_Reuse` (§1.2), `target_sig`; `_Rows` records every holder spelling a derivation asks about; each answer gets a serial (`serials` in the read's result); shadow mode (`SM_RAM_VERIFY=1`) re-derives every kept answer it serves and raises on a difference. A By-run read (Column History) is always derived from scratch. |
| `core/hub_index.py` | `ON_CLOSE`: closing a chip's reader drops its kept answers (memory only). |
| `web/hub_status.py` | the facts (§1.3): `target`, `fact`, `_eval`, `_holds`, `metric_paths`; `part(..., narrow=)` validates an older part by its facts (through `KeyedMemo`'s incremental compute, so shadow mode checks it too); `series_many` is no longer a kept part (the kept answers are) and records the `targets` fact; `_kept_rows` / `_ROWS`: a curated row or a leaf series per entity, by the answers' serials and the dataset roots. |
| `web/routes.py` | `_value_history(targets=, scope=)` (the table resolves once, so a part's value and its facts come from one resolution); the metric meta reads its panel paths and current values through facts; a typed Trends path is `narrow=False`; Changes split into `_hub_param_changes_data` (kept) and a page rendered per request. |
| `tests/test_hub_incremental.py` | §4 |
| `tests/browser/journeys/hub_after_a_run.cjs` | §8 |

## 3. The equality harness

`eqh.py` (scratch, not shipped): each sequence is a fresh chip -- synthetic (6 qubits, 3 pairs,
40 runs: aliases that retarget, confusion matrices, a long array, removed values, NaN and text,
pair RB fidelities, patches on half of the T1 moves) or a byte copy of a real archive's runs (the
first 300 as history, the rest arriving one per "run") -- and a random series of operations:
a new run; a run placed between earlier ones; a staged edit of a shown field, of an unrelated
field, of an alias pointer; apply; undo; redo; a run folder deleted; a run folder's state or
`node.json` rewritten; another process flipping a flag in place (its own SQLite connection); a
reload of the ledger (readers closed); a chip switch. After every operation, a random 75% of 16
requests (Trends: default, five metrics, an alias badge, a typed path; the metric meta; the grid
twice; two cell drawers; two value drawers; Changes as a full page and two fragments; Column
History) is answered as served, then from scratch (every kept part swapped for an empty cache, the
read index rebuilt), then as served once more; both served answers must equal the from-scratch
one byte for byte. Counters show the kept paths were exercised, not bypassed.

| Run (final code) | Chips | Sequences x operations | Comparisons | Mismatches | Path answers served again after the ledger moved | Parts served again after an edit |
|---|---|---:|---:|---:|---:|---:|
| seed 3 | synthetic | 20 x 30 | 13,484 | **0** | 34,079 (re-derived 2,111) | 1,474 (recomputed 91) |
| seed 5 | synthetic | 10 x 30 | 6,762 | **0** | 16,837 (re-derived 1,285) | 870 (recomputed 46) |
| seed 11 | archive copy | 3 x 25 | 1,674 | **0** | 9,723 (re-derived 53) | 183 (recomputed 3) |
| seed 13 | archive copy | 3 x 25 | 1,702 | **0** | 8,142 (re-derived 51) | 215 (recomputed 2) |
| **total** | | **36 x 1,050 ops** | **23,622** | **0** | 68,781 | 2,742 |

Operations covered (seeds 3 + 11): 215 new runs, 30 runs placed between earlier ones, 55 + 81 + 28
staged edits (shown / unrelated / alias), 54 applies, 50 undos, 25 redos, 29 run folders deleted,
22 + 24 run folders rewritten (state / `node.json`), 20 writes by another process, 14 reloads,
27 chip switches. The rounds before (8 sequences, then a 20-sequence round stopped when it hit the
first defect below) ran on the code before the two fixes and found them.

Found by it, each fixed and pinned before the counts above:

1. **The newest run rewritten in place.** A run folder's saved state rewritten while that run was
   the newest event: its re-diffed rows are INSERTED for the newest eid, and the first trigger
   definition skipped inserts for the newest event ("its own creation"). The metric meta kept
   naming an older run as the newest change of a qubit's T1. Every insert is logged now; a reader
   only asks about events up to the newest one it saw, so an append still invalidates nothing.
2. **The full Changes page kept its top bar.** Pre-existing (docs/283 kept the full page): the
   pending tray and the sync state are part of the full page, and an apply that writes nothing new
   (the staged value is the value live holds) empties the tray with no edit counter and no ledger
   change -- the kept page said "1 unapplied edit" after it. The full page is rendered per request
   from the kept list now.

## 4. Pins

`tests/test_hub_incremental.py`, 50 test functions (51 cases), generic synthetic chips only:

* **the log** (10): the trigger covers every events column but `t_ord` (the insert default) and
  `ord` (logged as every event) -- a column added later fails it; an append logs nothing for an
  event a reader saw; an undo logs the write it took back; a deleted run folder; a re-proof; a
  deleted change row; a changed order; a removed event; a log of other trigger definitions, and an
  incomplete one, vouch for nothing until the next writer remakes them under a new epoch;
* **kept answers** (25): a new run re-derives only the paths it wrote; an alias retargeted by a
  run; a path asked about before it existed; an undo; a flag set in place; a re-proof; another
  process writing; a run placed between earlier ones; before the first one; an event placed
  first; a rename era appended, and one written into an old run; a reopened reader and a new
  epoch; a trimmed log; a log that went back; a rewritten run folder, and the newest one; an SM
  fact changed by another process; a root moved; an event removed without its log entry; an
  element of a long list, and an alias to one; the ledger start read by a path recorded later;
  the drawer limit; shadow mode;
* **surfaces after an edit** (8): an unrelated edit recomputes nothing; a shown value recomputes
  the meta only; an alias retargeted by an edit; a typed Trends path and the full Changes page
  recompute on every edit, the Changes fragment does not; an apply that changes no value; a qubit
  gone from the state; a data folder unlinked; a part computed while the chip changed;
* **the state's other facts and renames** (6): a load id edited moves the meta; a matrix alias that
  starts naming a recorded matrix brings its row; a renamed chip keeps what a new run did not
  touch; a rename era appended, and one written into an old run, that touch none of the kept
  answer's spellings; the era today is part of a kept answer;
* the equality harness, short (2 seeded sequences, 60 comparisons).

Each kept-answer pin asserts what was derived again (the answers' serials) AND that the answer
equals the from-scratch one.

## 5. Mutation sweep

`mutate.py` (scratch): one source edit at a time (the anchor must occur exactly once), the pin file
run with `-x`, the bytes restored in `finally` and checked; RED only on an assertion in a test (a
`TypeError` from a malformed mutation is not RED). **42 / 42 RED** on the final code and pins, one
clean sweep; the source verified byte-identical afterwards.

| Rule broken | Caught by |
|---|---|
| the events trigger misses `flags` | the trigger covers every events column |
| a change row inserted for the newest event is not logged (the first definition) | the newest run rewritten in place |
| a change row insert / update / delete is not logged | a rewritten run folder; a re-proof; a deleted change row |
| an SM fact update, a root update, an order change, an event delete is not logged | an SM fact changed by another process; a root moved; a changed order; a removed event |
| a remade log keeps its epoch; the reader ignores the log version | a log of other trigger definitions |
| reuse ignores a trimmed log / a log that went back / its epoch / a reopened reader | the matching pins |
| reuse ignores a change of every event / rewritten paths / rewritten events / a rewritten era row | a root moved; a rewritten run folder; a flag set in place; a rename era written into an old run |
| reuse skips the event count / the ledger start | an event removed without its log entry; an event placed first |
| reuse ignores appended rows / an appended era | a new run; a rename era appended |
| a missing holder / an element's list / an answer's events / its segment starts are not recorded | a path that did not exist; an alias to a list element; a flag set in place; the ledger start read by a path recorded later |
| a kept answer ignores the holder now / the drawer limit / the rename scope | an alias retargeted by an edit; the drawer limit; the era today |
| facts never checked; no `targets` / `currents` / panel-path / qubit-name / matrix-alias fact | a shown value edited; an alias retargeted by an edit; a load id edited; a qubit gone; a matrix alias that starts naming a recorded matrix |
| a typed path kept across edits; the full Changes page kept | the typed path and the full page; an unrelated edit |
| a part computed while the chip changed is an exact hit | that pin |
| kept rows ignore the answers' serials / the dataset roots; a part's base ignores the ledger | the random sequences; a data folder unlinked |

The first sweep surfaced four survivors that were pins missing, not rules redundant: the era
gates (a rename the lineage already knows, brought by a run whose rows touch none of the kept
answer's spellings), the rename scope in the slot (the live state losing its record), the
matrix-alias fact (an alias that starts naming a matrix the ledger recorded through it), and the
element's list and the segment starts (an alias whose target is a list element; a path recorded
only after the ledger began). Two more were malformed mutations. Each has its pin now.

## 6. Fault injection

`faults.py` (scratch), a synthetic chip, every surface compared with a from-scratch answer after
each fault (8 requests each):

| Fault | Mismatches | What the surfaces say |
|---|---:|---|
| another process rewrote an old change row's value (its own connection) | 0 | the drawer shows the new value |
| another process flagged an old run folder gone | 0 | |
| every kept answer of an OLDER ledger state put back into the caches (value_history, rows, parts) after a run, an apply and an undo | 0 | (validation, not deletion, keeps them out) |
| an older copy of the ledger put back over the live one (SQLite backup, every connection open, kept answers in memory) | 0 | |
| a new run after that | 0 | |
| the log made incomplete (a trigger dropped), then an old run flagged in place | 0 | (no answer vouched for) |
| the log remade by the next writer (new epoch) | 0 | |
| the log trimmed past every kept answer | 0 | |
| the ledger file corrupted in the middle | 0 | "the change ledger could not be read" (S7's labelled fallback) |
| the ledger put back from a good copy | 0 | |

## 7. Before and after

`probe.py` (scratch): one process per side, the Flask test client (server time, no network); a
synthetic chip of 32 qubits (each run moves one qubit's T1, its own patch on every other run;
every 5th an f_01, every 13th an x180 amplitude, every 17th a confusion matrix, every 29th a pair
RB fidelity); each surface measured on its own right after the change, the read index prewarmed
first (the projector does that after a commit; its time is listed separately). "Before" is the
base commit `232b8fab` exported read-only; the two sides alternate on the same restored chip, two
rounds each. The PC ran another job's six worker processes throughout: tens of percent of noise,
the ratios hold.

**2,000 runs, 32 qubits** (ms, median of 2 rounds per side; the round that generated the archive is left out of "warm", its first sync was still running). Read index after each new run, per round: before [[50.0, 310.9, 54.4, 47.7, 42.7], [57.1, 322.6, 62.4, 61.9, 71.3]], after [[46.6, 264.6, 47.9, 43.1, 45.7], [51.3, 273.1, 46.1, 46.7, 47.2]].

| Case | Trends before | Trends after | grid before | grid after | meta before | meta after | cell drawer before | cell drawer after | value drawer before | value drawer after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| warm (nothing changed) | 5 | 4 | 30 | 27 | 6 | 5 | 3 | 3 | 14 | 9 |
| after one new run | 405 | 63 | 276 | 68 | 415 | 57 | 196 | 31 | 17 | 14 |
| after a staged edit of an unrelated field | 428 | 13 | 308 | 29 | 340 | 24 | 303 | 4 | 15 | 8 |
| after a staged edit of a field it shows | 437 | 13 | 339 | 28 | 387 | 36 | 221 | 3 | 18 | 9 |
| after an apply (an SM write) | 423 | 63 | 306 | 66 | 428 | 47 | 216 | 15 | 18 | 16 |
| after an undo | 431 | 108 | 281 | 68 | 344 | 48 | 201 | 17 | 20 | 18 |

**10,000 runs, 32 qubits** (ms, median of 2 rounds per side; the round that generated the archive is left out of "warm", its first sync was still running). Read index after each new run, per round: before [[578.2, 716.1, 150.2, 165.9, 146.2], [188.5, 979.7, 242.3, 199.2, 180.6]], after [[219.7, 1086.6, 150.3, 165.7, 210.0], [186.6, 885.6, 141.2, 173.3, 167.5]].

| Case | Trends before | Trends after | grid before | grid after | meta before | meta after | cell drawer before | cell drawer after | value drawer before | value drawer after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| warm (nothing changed) | 10 | 11 | 46 | 51 | 4 | 5 | 3 | 3 | 23 | 8 |
| after one new run | 1,638 | 149 | 835 | 112 | 1,031 | 86 | 664 | 191 | 36 | 28 |
| after a staged edit of an unrelated field | 1,176 | 19 | 686 | 43 | 897 | 17 | 797 | 5 | 20 | 7 |
| after a staged edit of a field it shows | 1,382 | 18 | 737 | 43 | 980 | 27 | 665 | 4 | 21 | 9 |
| after an apply (an SM write) | 1,208 | 144 | 822 | 86 | 1,080 | 52 | 667 | 33 | 21 | 32 |
| after an undo | 1,143 | 249 | 1,024 | 100 | 933 | 66 | 688 | 41 | 34 | 41 |

* **Warm** is unchanged (a request with nothing changed was already served from the kept part).
* **After a run** what is left is the paths the run wrote, derived again whole: at 10,000 runs a
  T1 path holds ~300 points (~50 ms each, profiled), then the parts over them; the cell drawer's
  191 ms is that, for the path its run wrote.
* **After an undo** the paths the undone write touched are derived again (rule 2, per path).
* **The read index** (the bracketed numbers) is unchanged: ~150-210 ms per new run at 10,000 runs
  (it rescans every event to check none changed), ~1 s when an event lands between earlier ones;
  it runs on the projector after the commit, before the surfaces ask (§9).

## 8. Real browser

SM served from this worktree on **5301** (production mode on the Flask development server, a sandboxed
`HOME`/`USERPROFILE`, no qualibrate config) with a **copy** of a real archive's runs: the first
352 as the chip's history, the rest in a pool. Headless Chrome on CDP **9601** (a scratch profile,
deleted after), real mouse events through `cdp.cjs`. Journey
`tests/browser/journeys/hub_after_a_run.cjs`: **ALL OK, 21 checks, console errors 0.**

| Step | Seen |
|---|---|
| Chip Status > Trends, the f_01 pill pressed | the q6 line before (`01_trends_before.png`) |
| the next run of the pool copied into the data folder (its own patch sets q6 f_01 = 4,155,629,126.26 Hz) | SM ingests it; Trends redrawn: a q6 point with exactly that value, "#353 08_qubit_spectroscopy / its own patch set it", click target `...:353`; the hover card says so (`02_trends_new_run_hover.png`); a click opens run #353 in the inspector (`03_trends_click_opens_new_run.png`) |
| a staged edit of a field no surface draws (`z.joint_offset`) | the q6 line's every point, value and word unchanged (`04_trends_after_unrelated_edit.png`) |
| a staged edit of q6 f_01 itself | Trends still draws only recorded points; the f_01 tile's meta: "Not in this chip's change ledger yet -- edited ... The ledger's newest change of it: 2026-08-10 10:27 -- #353 08_qubit_spectroscopy -- its own patch set it" (`05_meta_after_edit.png`) |

The first journey runs were the journey's own defects, each fixed: a pill pressed before the page
settled; a point's trace index counted hidden traces and its mark index counted gaps (Plotly draws
neither); the hover card read from the first chart's hover layer; leaving a page with staged edits
opens SM's own "leave this page?" prompt (now accepted, like a person; headless Chrome logs
"Blocked attempt to show a 'beforeunload' confirmation panel" when there was no gesture yet --
filtered as expected, not an app error).

## 9. Still open, and what this does not claim

1. **The read index still scans every event after each commit** (§7). The rewrite log can make
   that check O(new events) too (no logged change of an event up to the index's newest one means
   none of them changed); not done here: the index is shared by every ledger reader.
2. **A path a run wrote is derived again whole**, not extended by the new point. Appending to the
   kept answer is the next step for long paths (~50 ms per 300 points at 10,000 runs).
3. **A re-rendered fragment is still O(points)**: after a new run the Trends fragment is rendered
   again from kept rows (the template and the point data of every line).
4. **Facts are recorded where the state is read.** A future change to a part's compute that reads
   the chip's state outside `LedgerTable.target` / `fact` must record a fact or pass
   `narrow=False`; the equality harness and shadow mode (`SM_RAM_VERIFY=1`) are how such a slip
   shows.
5. **The log is not a lock.** A writer that bypasses SQLite (a file copied over a live ledger) is
   caught by the reader's file identity, the log's epoch and position, and the event count, not
   by the triggers (§6 puts an older copy back through SQLite and corrupts the file).
6. **Shared answer objects.** A kept answer is shared by every request until the ledger moves past
   it; callers build their own structures from it and never write into it (all callers checked).
7. Pre-existing, seen while reading (not changed): the kept Changes FRAGMENT shows the project name
   it was rendered with (`project_scope`) until the ledger or the folder changes.
