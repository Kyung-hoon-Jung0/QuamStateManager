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

## Also found, not fixed here

The conflict tray renders **no Auto-Sync pill at all** — `pillOn=false` *and*
`pillOff=false` in every pre-fix snapshot above. When SM does still disarm
(push-only, or the budget spent) the tray says nothing about it and offers no
way to turn it back on; only a transient toast mentions it. The pill returns as
soon as an ordinary tray replaces the conflict one, so it is recoverable rather
than stuck. Recorded, low severity, deliberately out of scope for a fix the
customer is waiting on.

## Verification

- `tests/test_autosync_merge.py` — 14 pins: the reported sequence end to end
  through the real routes; that the merge lands **both** values; that a
  push-only session and the legacy arm door are unchanged; that an unarmed
  apply is untouched; the budget, its exhaustion and its refill; and four pins
  on the shipped `auto-apply.js` — it listens, it presses the right door, it
  consults the latch, and the wait is bounded. A handler that never runs is the
  failure mode this project keeps finding (docs/120 ②, docs/149), so those are
  pinned on the file that ships.
- `tests/repro_autosync_enter.cjs`, `…_enter2.cjs`, `…_external.cjs` — the
  three reproduction drivers, **including the two that did not reproduce**.
  They record what the shape of this bug is *not*.
