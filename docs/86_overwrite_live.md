# 86 — the third choice: keep mine, overwrite live

*Status: shipped 2026-08-08. Branch `feat/sync-keep-mine`.
Amends docs/28 (working copy / safe I/O), docs/41 (State History restore) and
docs/65 (state roundtrip).*

## The report

> 지금은 QUAlibrate이나 Code ide 등 **외부에서** 수정한 것을 SM이 인식하면,
> diff를 보여주면서 사용자에게 선택지가 **Sync** 또는 **Close** 밖에 없거든?
> 그런데 … 테스트 run을 돌리고 실수로 parameter 업데이트를 한 경우들이 꽤 있어.
> 이런 경우에는 오히려 SM의 working state를 live chip으로 replace하고 싶을거란
> 말이지.

Two surfaces told the user their live chip had changed underneath them, and both
offered exactly one direction:

| surface | offered | missing |
|---|---|---|
| `_live_diverged_banner.html` | Review & sync · Pull live state | anything that keeps the working state |
| `_state_review.html`, **clean** branch | Sync | same |

## The button already existed — in every branch but the one that needed it

The review modal has three action branches, and reading them together is the
whole argument:

| working state | offered before |
|---|---|
| unsaved edits | Pull & apply · Re-apply · Discard |
| saved edits (`working_dirty`) | **↑ Apply to live chip** · Discard & pull live |
| **clean** | **Sync** |

"Write my working state over the live chip" was already implemented and already
exposed — in the saved-edits branch, and in the staleness conflict tray as
`Overwrite live (force)`. It was missing precisely where the user has made no
edits of their own: i.e. the case where a test run wrote the live chip and the
state SM is still holding is the good one. That is not a safety design, it is a
branch nobody filled in.

Nor is this new power: `/state-history/<ts>/restore-live` has always been able
to put an older state back on the live chip. What was missing was reaching it at
the moment the user is *told* about the drift, instead of after a hunt.

## Why it is safe enough to offer, and where it stops

Pull and push are **not** symmetric, and the design follows from that:

* A pull discards the working copy; the live files stay on disk.
* A push discards live content that may be a calibration another program just
  wrote.

So:

1. **It is never the primary action** and never the first button. Order is
   Review & sync → Pull live state → ↑ Keep mine — overwrite live, and it wears
   the same error-tinted style as the conflict tray's force button — the same
   act should not look like two different weights of decision.
2. **It is reversible, and says so.** `/state/apply-to-live` snapshots the
   pre-apply live and stores `ctx["last_apply"]`, which is what arms the tray's
   *Revert last apply*. That property is the reason this is offerable at all;
   verified end-to-end below.
3. **One confirm, and it names what disappears.** `GET
   /state/overwrite-live/preflight` reports how many live values differ, how
   many unsaved edits ride along, whether a run is writing this chip right now,
   and that the push is snapshotted. It is a separate endpoint fired **on
   click** rather than a count baked into the banner, because the banner renders
   on every page and reading live content on render is exactly what docs/28
   forbids.
4. **The push is forced.** The live chip has drifted by definition, so an
   unforced push would land on the staleness conflict screen and ask a second
   time — after the user has just been told exactly what they are forcing past.
5. **A run in progress is reported, not blocked.** A node writing this chip will
   re-write whatever we push when its next node finishes. That is worth saying;
   it is never worth refusing, because a run going wrong is the reason the user
   is here. (There is no scheduler edit-lock on the write path — `edit_lock`
   does not exist in the tree — so this advisory is the honest state of things,
   not a weakened gate.)
6. **Archives are refused** (409) and never render the button.
7. **An unreadable live folder is not an error.** The count comes back unknown
   and the confirm says so; the user may still legitimately want to write there.

## Verified on a real chip

Real 21-qubit chip, the customer's exact story:

```
1. loaded a real chip
2. a test run rewrote 42 live values out-of-band
3. review modal: branch=clean  rows=126  Sync=True  KeepMine=True
   preflight: live_changes=42  unsaved=0  run_active=False  reversible=True
4. overwrite: HTTP 200 in 810 ms — live restored to the working state
   preflight now: 0 differences
5. Revert last apply armed (pre_ts=…) → staging it returns the run's values
```

Step 5 is the load-bearing one: the overwrite is itself undoable, so the third
button cannot become a one-way door.

## The other half of the same report

A working copy that is **provably clean** never reaches either surface —
`reconcile_with_live` auto-pulls it (`RECONCILE_SYNCED`, `working_copy.py:462`).
That is deliberate (it is the stale-chip fix) and is not changed here, but it
means a user who has edited nothing in SM silently adopts a bad run's values.
The recovery path exists (the pre-pull state is snapshotted), so the fix is to
surface *revert* after an adoption rather than to block the adoption. Not in
this change.

## Pins

`tests/test_overwrite_live.py` (14) — the preflight's counts, the unreadable-live
and broken-run-probe degradations, the archive 409, the button in all three
review branches (and its absence when there is nothing to overwrite), the banner
offering both directions, and the push landing with *Revert last apply* armed.
`tests/state_sync_selfcheck.cjs` (+11) — one preflight then one confirm, the
confirm naming the count / the run / the reversibility, decline posting nothing,
a refused preflight never asking, and the push carrying `force=1` into
`#pending-tray`.
