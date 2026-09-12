# docs/187 — Auto-Sync turned itself off on the one bench it was built for

2026-09-12, customer, on-site:

> auto sync가 이상하다고 함. 예를 들어 live edit에서 enter를 누르면 반영이 그
> 다음부터 auto sync가 깨짐. 그리고 이유없이 pull&apply 문구가 뜬다고 함.
> (외부변경없는데도)

Two symptoms. One mechanism, and it is not an edge case on their bench.

## The missing ingredient was the other program

Two reproduction rounds failed before the third succeeded, and what they ruled
out is worth as much as what the third found:

1. **Three Enters, three different rows, 4 s apart** — all three landed on the
   live chip. No conflict, session stayed armed.
2. **Four rapid Enters on the SAME row**, two of them inside a flush that was
   still in flight, all three Auto-Sync switches on — all four landed.

Neither could reproduce it, because both held constant the thing the customer's
bench does continuously: **another program writing the chip.**

The evidence that this is their shape, read rather than guessed:

```
instance/instances/4928.json   chip_path = D:\work\...\quam_states\260907_KRS_5Q   (SM on :5050)
~/.qualibrate/config.toml      state_path = the same folder
~/.qualibrate/logs/…           "Saving machine to active path <that folder>"
                               19:50:26 · 19:51:32 · 19:53:00 · 19:53:54 · 19:54:35
```

A qualibrate node saves the machine to that folder on **every run — every
30–60 seconds**, and SM has the same folder open. So a node writes the chip
*between two of the user's edits* as a matter of course.

Round 3 adds exactly that, and reproduces both symptoms at once:

```
[after Enter with the chip moved underneath]
   auto=null  pillOn=false pillOff=false  conflict=true  pull&apply=true
[after a SECOND edit]
   auto=null  pillOn=false pillOff=false  conflict=true  pull&apply=true
on disk: the user's 0.0099 and 0.0088 never reached the chip
```

## What was wrong

`/state/apply-to-live` raises `StaleLiveError` when live moved away from the
sync point, and docs/117's rule was: **disarm**, because "a background writer
the user may have forgotten about must never keep pushing at a chip that
moved."

That rule is right for the session it was written for — **push-only**. It is
wrong for a session that also armed **pull**, and on this bench it is wrong
every 30–60 seconds: Auto-Sync died within a minute of arming, and from then on
every edit silently stopped reaching the chip while the user kept typing. The
feature turned itself off on precisely the event the user armed pull to handle.

The second symptom is the same event seen from the front: the conflict tray is
where **⇄ Pull & apply (merge)** lives, so it appeared "이유없이" — for no
reason the user could see, because the writer was their own calibration run
rather than a person.

## The fix

A session that armed pull has already granted SM permission to take live
changes. A push that finds live moved is exactly what pull exists to resolve.
So that case **merges** — pull the live change, re-apply the pending edits on
top, push — instead of disarming.

- It is the **composition of two permissions the user already granted**, not a
  new one.
- It is `pull_replace`-neutral: that flag governs whether an auto-PULL may
  DISCARD the user's values, and this path keeps them.
- **Push-only sessions keep docs/117's disarm byte for byte**, and so does the
  legacy `/auto-apply/arm` door, which grants push alone.
- The decision is the server's, like every other Auto-Sync branch (docs/120
  item 8); the client only presses the door the conflict tray already offers
  (`doStateSync('apply')`).

Two details the code carries comments for:

- **The client must wait for the shared `_applyInFlight` latch.** The signal
  arrives with the flush's own response — *before* that flush's `done` releases
  the latch — and `doStateSync` bails on a held latch **silently**. Without the
  wait the merge would simply never happen. Bounded at ~2 s, because a latch
  that never clears must not leave a timer running for ever.
- **A merge budget of 3 consecutive failures**, then it disarms after all. A
  node between two edits is ordinary; a merge that keeps conflicting is not,
  and a background writer must never retry for ever. An apply that **lands**
  refills it — the budget counts consecutive failures, or a long healthy
  session would eventually disarm on its Nth unlucky node write.

## After

Same driver, same outside writer, same chip copy:

```
[after Enter]     auto=1  pillOn=true  conflict=false  pull&apply=false
[after 2nd edit]  auto=1  pillOn=true  conflict=false  pull&apply=false
on disk: q5T1 = 3.21e-05   ← the outside write SURVIVED
         edited = 0.0088   ← the user's edit LANDED
```

Both values on the chip, which is the whole point: the node's write and the
user's edit, not a choice between them.

## ② The other half: a replace-pull discards your edits, silently

Found by asking whether ① covered the panel's **default** — all three switches
on. It did not, and this is a second way the same complaint reaches the user.
Measured through the real routes:

```
replace=False   pull -> 204   pending 1 -> 1   T1 still 9.99e-05   (it asks)
replace=True    pull -> 200   pending 2 -> 0   T1 back to 2e-05    (discarded)
```

That is what the checkbox says it does, and **the semantics are unchanged
here**. What was wrong is that the server had always announced it —
`autoSyncPulled {"replaced": true}` — and a grep of the whole tree found **no
consumer**: no listener, no template, nothing. So on a bench where a node
writes every 30–60 s, a value the user had just typed reverted under their
cursor with no explanation at all.

The pull now counts what it is about to drop **before** pulling — afterwards
there is nothing left to count, and a message that reports a replace must not
be able to understate it — and one warning names the number and the way back.
The way back is real: that pull already snapshots `kind="backup"` exactly when
it is discarding, and a pin asserts the snapshot is taken so the sentence
cannot quietly become a lie.

In real Chrome, driven by the app's OWN auto-pull (not a synthetic dispatch —
the synthetic one carried `replaced:false` and returned early, so the toast can
only have come from the real path):

```
warning: Auto-Sync pulled the live chip and replaced 1 unapplied edit
         — that is what "replace" does. The previous state was snapshotted
         first: State History can bring it back.
```

## Mutation sweeps: 20/20

**① 12/12.** **② 8/8** — but the eighth needed two goes, and both attempts are
the lesson.

`8 a replace-pull stops refusing without replace` went GREEN first. The outer
guard in `/auto-sync/pull` *looks* like a duplicate of the one inside the build
lock, and for a **server-side** edit it is. It is not for `dom_dirty`: a
fill-down or a pasted column lives only in the DOM until Apply, so `change_log`
and `working_dirty` are both clean, the inner guard — which asks only
`_quam_ctx_dirty` — lets the pull through, and a whole filled column goes with
no prompt. Only the client can see that work, which is why it reports it. My
pin passed `dom_dirty=0` and could not see any of it.

And the replacement pin **was appended after a module-level function's
`return`**, making it a nested `def` that never ran — pytest collected 19, not
20, and reported green. A test that does not run is the failure mode this whole
session kept finding; it found one more in its own fix.

## ③ The conflict tray showed no session at all

Every pre-fix snapshot above carries **both** `pillOn=false` *and*
`pillOff=false`. The conflict tray REPLACES `#pending-tray`, and the Auto-Sync
pill lived only there — so exactly while a live-write conflict was on screen,
nothing said whether Auto-Sync was on, nothing said it had just been turned
off, and there was no control to turn it back on. A toast that vanishes in
seconds was the only mention of a session that had stopped writing.

The pill is **one partial** now (`_auto_sync_pill.html`), included by both
trays. A second copy would drift from the first, which is what this project
keeps a single-renderer rule for. The extraction is proved inert rather than
asserted: eight pill states (armed both / push / pull, off, off-with-pending,
blocked, absent) render **byte-for-byte identical** before and after.

Both conflict render sites go through one `_conflict_tray()` helper, so neither
can be the one that forgets a variable — a caller that omits any of
`auto_sync`, the two armable verdicts or `change_count` makes the pill render as
*nothing*, silently, which is the defect itself.

And the tray **says** what happened, where the user is actually reading:

| situation | what the tray says |
|---|---|
| the merge is running (pull armed) | "Auto-Sync is still on and is resolving this itself" |
| it was turned off (push-only, or the budget spent) | "Auto-Sync has been turned **off** — it will not write to the live chip again until you turn it back on with the pill above" |
| the user never armed it | nothing at all |

Two things that required getting right: the verdict is decided **before**
rendering (the disarm used to happen after the body was already a finished
string, which is why the tray could not state it), and the session is popped
before the render — otherwise the pill reads the session it is about to throw
away and paints itself ON beside a line saying the opposite.

**Verified in real Chrome**, same driver, two worlds:

```
WORLD A  pull+push armed   -> auto=1     pillOn=true   conflict=false
                              both values on the chip (the merge, ① above)
WORLD B  push-only         -> auto=null  pillOff=TRUE  saysTurnedOff=TRUE
                              conflict=true, the edit safe in the working copy
```

World B is the exact inversion of what this section used to record: the pill is
there, it reads OFF, it is clickable, and the tray says so.

**Mutation sweep: 9/9** — after two GREENs that were both findings about my own
work rather than the product:

- `it claims a disarm that did not happen` was a **genuine no-op**: at that line
  `_will_disarm` cannot be false (`_auto` is armed and `_will_merge` is false,
  which is its definition). A variable that can only be True reads as though it
  might not be, so it was deleted rather than pinned — no pin can fail on a
  mutation that does not mutate.
- `the sync route stops using the one renderer` was a **conditional pin**:
  `if the response was a conflict, assert …`. The fixture never produced one
  from that route, so it asserted nothing and went green against a mutation
  that stripped the pill out of it. It forces the write to fail now, which is
  the only thing the pin was ever about.

One more trap paid: **a macro imported by the INCLUDING template is not visible
inside an include.** The partial failed with `'icon_bolt' is undefined` the
moment it had a second caller, and imports its own now. And `test_web`'s
`TestRound15ChromeHiding` asserted the wrapper's presence in the tray **file**;
it follows the include now and pins the rule — the element that CSS targets is
in the tray's markup — rather than which file spells it.

Running total across the three parts: **29/29 mutations.**

## Verification

- `tests/test_autosync_merge.py` — **30 pins**: the reported sequence end to end
  through the real routes; that the merge lands **both** values; that a
  push-only session and the legacy arm door are unchanged; that an unarmed
  apply is untouched; the budget, its exhaustion and its refill; and four pins
  on the shipped `auto-apply.js` — it listens, it presses the right door, it
  consults the latch, and the wait is bounded. A handler that never runs is the
  failure mode this project keeps finding (docs/120 ②, docs/149), so those are
  pinned on the file that ships. Then ② — that a replace-pull reports how many
  edits it dropped, that a pull dropping nothing does not claim it did, that
  the snapshot the message promises is really taken, that somebody listens at
  all, and the `dom_dirty` guard the sweep found unpinned. Then ③ — that the conflict
  tray carries a pill at all, that a surviving session reads ON and a disarmed
  one never does, that it can be re-armed, that the tray states the disarm,
  that it never claims one that did not happen, that a user who never armed
  Auto-Sync is told nothing, that BOTH conflict doors carry it, and that the
  pill has exactly one renderer which imports its own macro.
- `tests/repro_autosync_enter.cjs`, `…_enter2.cjs`, `…_external.cjs` — the
  three reproduction drivers, **including the two that did not reproduce**.
  They record what the shape of this bug is *not*.
