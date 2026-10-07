# 298 — The ledger surfaces after a run, an undo or an edit: derive only what changed

**Date:** 2026-10-08 · **Trigger:** the two items docs/292 left open. After every new run each
ledger surface (Chip Status Trends, the metric meta, the Param History grid and its cell drawer)
re-read every point of every path it shows; and a staged, unapplied edit of ANY field recomputed
everything too, because every cached part was keyed on the chip's `mutation_seq`.

**Result.** Each path's answer is now kept between reads and served again only when the ledger
PROVES nothing it was derived from changed; each surface part is kept with the facts of the
chip's state it read and served again after an edit while they still read the same. Every served
answer is byte-identical to a from-scratch answer: __HARNESS__. Mutation sweep __MUT__. After one
new run on a 10,000-run chip: __AFTER_RUN__ (§7).

## 1. Spec (written before the implementation; amended where the harness proved it wrong, §3)

### 1.1 What is kept

| Layer | Where | Kept | Valid while |
|---|---|---|---|
| a path's answer | `value_history.read`, `_KEYS` | one entry per (chip, the target's holder and every pointer hop on the way, drawer limit, rename scope): the answer, the ledger state it was derived at (its *stamp*), every holder spelling the derivation asked about (found or not, with a list element's list), the events whose facts it read | §1.2 rules 1-5 |
| a presented row | `hub_status._ROWS` | a curated (qubit, property) row or a leaf series with each point's words | the SERIALS of the per-path answers it was built from (a serial names one answer object) and the dataset roots its data links resolve against |
| a surface part | `hub_status._CACHE` (unchanged memo, new validation) | the Trends fragment, the curated rows, the metric meta, the Changes list, the pair badges, the rename marks | the ledger version, the roots, the sync status and the open folder are the same, and every recorded FACT of the chip's state still reads the same (§1.3) |

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
trimmed past 65,536 entries; a reader stamped before the trim re-derives.

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

__FILES__

## 3. The equality harness

__HARNESS_SECTION__

## 4. Pins

__PINS__

## 5. Mutation sweep

__MUTATIONS__

## 6. Fault injection

__FAULTS__

## 7. Before and after

__PERF__

## 8. Real browser

__BROWSER__

## 9. Still open

__OPEN__
