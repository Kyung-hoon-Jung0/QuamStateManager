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
