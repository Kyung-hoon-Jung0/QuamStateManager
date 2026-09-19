# 200 — The one run whose point lands on the wrong day

Date: 2026-09-18. docs/196 recorded this as an open item from a code reading.
It is now **reproduced**, and it is precisely the case the customer asked about:

> 비록 sync버튼을 뒤늦게 눌렀더라도, trends의 그래프에 찍히는 value들의 날짜는
> **반드시 실험을 수행한 그 날짜/시간 이어야한다**

docs/196 §1 answered the general question correctly — a late sync does not move
a run's point, because `_entry_timestamp` mints the stamp from the run's own
date folder + `HHMMSS`. That holds for every run ingested from its own folder.

**One run in the batch is different**: the one whose state the chip was already
holding when SM opened.

---

## 1. The reproduction

```
the run ran 2026-09-10 14:15:16 and wrote T1 = 4.2e-5
SM is opened   2026-09-18

1. SM adopts the live chip   -> snapshot 20260917_195647  kind=exp  run_id=None
2. the run's ingest arrives  -> {"ingested": 0, "skipped_duplicate": 1, "enriched": 1}
3. history holds             -> 20260917_195647  run_id=77  exp=some_node

stamped with the RUN's date (2026-09-10): NONE
```

The mechanism is an ordering, not a bug in either half:

- SM was closed, so history has never seen this content. The adopt snapshot
  therefore **cannot dedup** and lands stamped `_ts_stamp()` = now.
- The run's ingest then finds the content already stored and takes the
  docs/132 **enrich** path, which adds `run_id` and `experiment_name` —
  and deliberately does not touch `timestamp`.

That deliberateness is right for the case enrich was written for. Its docstring
says so: *"the user applied a run's fit values before the near-real-time ingest
saw the run … its `kind` is deliberately NOT changed (the user DID pull it)"*.
When a human pulled the values, the human's timestamp **is** the truth.

An **auto-adopt is nobody's deliberate act**. Nothing happened at that moment
except SM noticing. So the stored time describes an event the user does not
care about, and the run's own time — which is right there in the entry — is
discarded.

Consequence: on Trends, the most recent run's value sits on the day someone
opened SM. Every other run in the batch is correct.

## 2. What is already right, and must stay right

The linkage survives: the row gains `run_id=77` and the experiment name, so the
"After #77" chip and the run deep-link work. Only the x position is wrong. A fix
must keep that (pinned by `test_the_enrich_does_attach_the_run_even_today`).

And the control case passes today — ingest before adopt produces a correctly
stamped row, which is exactly why docs/132 reordered the scheduler hook. The
open/adopt path never got that reorder.

## 3. Fix options, with their real costs

**(a) Re-stamp on enrich.** When the existing row is an *automatic* adopt
(`trigger="auto"`, `run_id` empty) and the incoming run's derived stamp is
earlier, move the snapshot to the run's time. Correct, and narrow enough not to
touch the human-pull case enrich was written for.
*Cost:* the timestamp **is** the identity. It is the folder name, the
`meta.json` field, the `param_history` key (`WHERE timestamp = ?`), the leaf
index key, and an entry in the manifest sidecar. Moving it means a coordinated
update across four stores with a crash-safe order — its own round, with its own
review.

**(b) Reorder, as docs/132 did for the scheduler hook.** Attribute the adopted
content to a run before snapshotting, so the correct stamp lands first and the
adopt snapshot deduplicates away.
*Cost:* "which run produced this content" needs a lookup that does not exist at
open time; the full answer is the backfill, which docs/142 deliberately moved
off the request path.

**(c) Stamp the adopt snapshot from the live file's mtime** rather than `now`.
Cheap (one stat), and arguably more honest for every adopt — the snapshot
records content that was written then, not noticed then.
*Cost:* mtime is not always the run's time (a restore, a copy, a touch), and
snapshot stamps must stay unique and ordered. Needs its own argument about what
the stamp is allowed to mean.

**Not chosen here.** All three touch the snapshot store, which is the most
coupled state in the app, and this was found at the end of a long stress round.
Shipping a partial change to history stamping without a review is the worse
call than recording it precisely.

## 4. What ships instead

`tests/test_adopt_stamp.py` — three tests:

- the control (ingest first) **passes**, so the fix has a reference;
- the linkage pin **passes**, so a fix cannot regress it;
- the customer's scenario is **`xfail`**, asserting the DESIRED behaviour with
  the reason pointing here.

It is written as an xfail rather than a test of current behaviour on purpose: it
turns green the day (a), (b) or (c) lands, instead of quietly encoding the wrong
answer as correct and having to be rewritten then.

---

## 5. The fix (2026-09-19): (a), done as a move under three guards

The user asked for it ("1,2번 고치자"). Option (a), the re-stamp. §3's cost
estimate held, and it shaped the design rather than ruling it out.

**Which snapshots move.** The notice row has to have `trigger == "auto"` and no
run yet, and it has to be the only row holding that content. In SM, `auto`
means a pull of content an outside writer produced: the user's **Take live**
(`/state/sync`, `kind="manual"`) or a machine adopt (`kind="exp"`). A `save`,
`manual` or `restore` row is SM writing live at that moment, and docs/132's rule
still applies: the human's time is the truth. Those rows are only enriched,
exactly as before.

§1 was also imprecise about the user path. Opening a chip does not adopt
(docs/87 turned that off); the late **Take live** is what mints the "now" row,
and that is the button the customer named.

**When a move is allowed.** The run must be EARLIER than the notice. Nothing may
lie between the two stamps: no snapshot dir, no `param_history` row, no
`leaf_snaps` row. Pruned snapshots keep their index rows, and an index insert
can fail and leave only the dir, so each of the three records blocks the move on
its own. Under that guard, the move is a relabel. Every neighbour, every change
point and the `diff_summary` against the prior stay valid, so the index rows are
UPDATEd in place in one transaction. That includes `param_history_cp` and
`_cp_last`, whose rowid watermark would never see an UPDATE.

**The outcome equals ingest-first.** The meta becomes `trigger="experiment"`,
`kind="exp"`, plus the run fields, and `restamped_from` is kept as an audit
trail. `test_notice_first_ends_exactly_as_ingest_first` compares both orders
field for field, index rows included.

**Crash safety.** The move is copy-then-delete under an intent file
(`.restamp.json`). Every state a killed process can leave is the old row, the
new row, or both. That can show as a visible duplicate, but never as a row whose
dir and meta disagree. `_finish_restamp` completes or abandons the move. The
self-heal calls it before it compares disk with the index, so it never indexes
the copy as a second snapshot. A re-ingest of the same run converges on its own.

**Both ingest paths.** The single-run path (a Datasets page polling while the
run lands) and the bulk backfill (Param History's import: the path for a run
that was already on disk when SM opened, i.e. the customer's closed-SM batch)
both go through `_ingest_entries_into`'s duplicate branch. The batch
transaction is committed first so the move can take the write lock.

**Verified on the customer chip in real Chrome.** A run landed in the project's
dataset folder at 14:10:20 while SM held an older state. The live file changed,
the banner offered Take live, and it was pressed at 20:25, which minted
`20260919_112518_7923 auto/manual`. Param History → Import from workspace then
moved it to `20260919_051020_200 experiment/exp run 4200`, with the old dir and
every index row at the old stamp gone. State History's top row reads
**9/19/2026, 2:10:20 PM · EXP · after restamp_probe run #4200**.
`/topology/trends` (the Chip Status chart) carries the run's stamp 16 times and
the press's stamp 0 times. The chip was restored byte-for-byte afterwards.

Pins: `tests/test_adopt_stamp.py`. The xfail is now a plain test, alongside
`TestTheOrderOfArrivalDoesNotMatter` (13) and `TestAKilledReStampConverges`
(4). Mutation sweep: 16/16 RED. One pin was vacuous on the first pass and is
now fixed: the bulk-path pin never had an open batch transaction, because its
only entry was the duplicate. The two-holders guard cannot be reached through
today's callers (content dedup keeps one holder per hash, and every forced
capture is manual or backup), so it is pinned with a forced `auto` notice.
