# 195 — Auto-Sync asks about the field, not the file

Date: 2026-09-17. Customer, on-site, after docs/187:

> auto sync 는 그거 눌러놓고, 다른 곳에서 편집하고 SM에서 편집했을 때, 메뉴 팝업이
> 헷갈려 하더라구… sync 버튼이 working.. 인가로 변하고, 또 다른 것도 뜬다고 해서..
> 이거 단일화 시켜서 간단히 만들어. […] 어차피 외부에서 바뀐거 auto하게 했으니까
> 외부에서 바뀌었으면 그거 반영해서 알아서 pull하고, **"만약에 사용자가 SM에서 바꾼게
> 동일한 field라면"** 그 때는 사용자한테 어떻게 할지 선택하게 하는게 어떨까? **이때만.**

That is the whole design, and it is better than what was there.

---

## 1. What was actually wrong

Auto-Sync decided with **one whole-file question**: *is the working copy dirty
at all?* Two places asked it, identically:

- `routes._auto_pull_due` (`:16679`) — whether to even tell the client a pull
  is worth pressing;
- `/auto-sync/pull` (`:15980`) — whether to perform it.

```python
if _quam_ctx_dirty(ctx) and not sess.get("pull_replace"):
    return False / return "", 204
```

So an experiment writing `q2.T1` while the user edited `q1.f_01` produced
**exactly** the prompt an experiment overwriting `q1.f_01` produced. Same
banner, same three buttons, same words — for a case with nothing to decide and
a case with a real collision. A user who sees the same box for both learns to
dismiss both, which is what was reported.

The panel's own switch offered only the two ends of that:

| `pull_replace` | behaviour |
|---|---|
| ON | *"the live chip always wins, without asking — unapplied edits in SM are discarded and are not recoverable with Ctrl+Z"* |
| OFF | *"SM asks instead of discarding your work"* — on **every** external change |

The middle — adopt what does not collide, ask about what does — did not exist.

The `working..` in the report is real too: `.state-status-badge` renders
`Working state · N unsaved` (`_pending_tray.html:75`), which is simply what the
badge says once you have edits. It read as a malfunction because it appeared
alongside a banner that had no business being there.

## 2. The mechanism — and where the missing information already was

Separating "they moved it" from "I moved it" needs the value the path held
**before** the user touched it. `Differ().diff(working, live)` cannot supply it:

```
S = sync point     W = S + my edits     L = S + their edits
diff(W, L) says only THAT they disagree, never whose edit caused it.
```

But `ChangeEntry.old_value` **is** S, for exactly the paths that matter. So:

> a path the user edited is in conflict **iff** the live value differs from that
> path's *earliest* `old_value`.

Exact, and it costs no extra read. `core/sync_conflict.py` is that rule as a
pure function (`classify` → `Verdict`), with `covers()` for created/deleted
subtrees (`qubits.q1` must collide with `qubits.q1.T1` but never with
`qubits.q10`).

**The honesty rule is asymmetric on purpose.** A wrong "no conflict" destroys
work silently with no Ctrl+Z; a wrong "conflict" costs one prompt. So dirt the
module cannot enumerate — saved-but-unapplied content whose change log was
journalled away, a stash mid-merge — makes the verdict `unaccounted`, and
Auto-Sync asks exactly as before. Silence is only ever granted over edits that
were counted.

## 3. What changed

**The pull** (`/auto-sync/pull`) computes the verdict and takes one of three
branches instead of one:

- **no collision** → tells the client to press `/state/sync?mode=reapply` via
  `HX-Trigger: autoSyncMergePull` — the door that already pulls live and puts
  the user's edits back on top. The server decides, the client presses one
  tested door: the same division docs/187 chose for the push side. Nothing is
  announced, because resolving this without the user is what arming Auto-Sync
  *meant*.
- **collision** → 204 as before, with the paths recorded.
- **verdict unavailable** → 204. Fail closed.

**The client** reports *which* cells are typed-but-uncommitted (`dom_path=…`,
`app.js:3527`) rather than a bare `dom_dirty=1`. A cell with no dot-path still
counts through the flag, so the answer never gets weaker than it was.

**The signal** (`_auto_pull_due`) carried the same blanket test, for a good
reason at the time — *do not advertise a pull the policy will refuse*, or the
client POSTs a 204 every 5 s forever, each taking the shared apply latch. The
policy stopped refusing the non-colliding case, so that guard became the thing
preventing the resolution. It now consults a verdict **remembered against a
fingerprint** (`_auto_pull_sig`: `mutation_seq`, the live mtimes, `working_dirty`
— two stats, no live content read), so a known unchanged collision stays silent
and any real movement re-arms it.

**The banner names the collision.** Since a non-colliding change never reaches
it any more, an appearance means a genuine two-sided edit:

> ⚠️ **1 field** was changed both here and on **260907_KRS_5Q**:
> `qubits.q1.anharmonicity` — choose which to keep.

The verdict is computed in `_drift_conflicts`, called from where
`live_drift_count` is already computed — the reconcile that raises the banner
had to read both sides, so it is free (docs/87's rule) and, crucially, has no
ordering dependency on a pull having run first. The first cut recorded it only
in the pull, and the banner therefore still showed the generic wording, because
the banner goes up **before** the pull runs. Real Chrome found that, not a test.

`pull_replace` is untouched: a user who ticked it asked for the live chip to win
outright, and it still does.

## 4. Verified on the real chip, in real Chrome

Auto-Sync armed (pull on, replace off, push off); an outside writer doing what
`machine.save()` does to `260907_KRS_5Q`.

**Case A — different fields.** SM holds an edit on `q1.anharmonicity`; the
writer changes `q2.anharmonicity`:

```
signals : autoSyncMergePull {"chip":"58be13c445147757",
                             "external":["qubits.q2.anharmonicity"],"kept":1}
toasts  : Pulled the live state and re-applied 1 edit — review them in the tray
banner  : []                      <- nothing to decide, so nothing is asked
q2 on disk: 216000000.5           <- their write survived
```

**Case B — the same field.** The writer changes `q1.anharmonicity`:

```
merge signal : none
banner : "⚠️ 1 field was changed both here and on 260907_KRS_5Q:
          qubits.q1.anharmonicity — choose which to keep."
buttons: Review & sync · ↓ Take live — discard my edits · ↑ Keep mine — overwrite live
on screen 201,000,000.0   on disk 199000000.5   (both kept; the user chooses)
```

Zero console complaints; the chip ends at 0 differences from pristine.

## 5. Pins, and three things the sweeps corrected

`tests/test_sync_conflict.py` (20) + `tests/test_autosync_merge.py` (+16, 71
total). **Mutation sweeps: 8/8, 9/9, 7/7 — after three GREENs.**

1. **A failed verdict read as "no conflict" was unpinned** — the one direction
   that must never fail open. Pinned by stubbing `_auto_pull_verdict` to None.
2. **`len(change_log)` in the fingerprint was redundant**, and I had kept it on
   the belief that `undo_group` does not bump `mutation_seq`. It does. The pin's
   own guard assertion caught my premise (`assert 2 == 1`) before the claim
   reached this document; the term was removed and the behaviour pin kept.
3. **Two keys held one fact** (`live_conflicts` + `live_conflict_sig`), so a
   mutation of either could not be observed — the gate required both. Collapsed
   into one record, which made the pop load-bearing and therefore pinnable.

`tests/autosync_merge_selfcheck.cjs` (29) unchanged and green; it failed once
under the mutation sweep's own CPU load and passed 3/3 standalone — its C3–C5
are wall-clock bound (~2 s), the flake class docs/141 §4ae names. Pre-existing:
this round added a listener for a different event and did not touch that path.

## 6. Still open

- The panel still offers `pull_replace`. With per-field conflicts it has little
  left to do, but removing a switch a user may have ticked deliberately is a
  separate decision, not a side effect of this one.
- `working_dirty` with no change log is still `unaccounted` (it asks). The
  undo journal holds those units, so it *could* be enumerated — deliberately
  not done here, because it widens the silent path and this round's whole
  argument is that the silent path must only cover what was counted.

---

## 7. Self-review: the merge signal could loop (fixed)

Reviewing this change against the codebase's own history — docs/123–125,
docs/141 §4ac/§4ae and docs/160 §5b–5e are all rounds where the *fix* carried
the next defect — turned one up here, and it is the shape docs/187 already
fixed once on the other side.

**The loop.** The merge branch POPPED its record. So:

```
poll  -> _auto_pull_due: dirty, nothing remembered   -> advertise
pull  -> no collision -> pop the record, signal, 204
client-> presses /state/sync?mode=reapply
```

When that press lands, `live_diverged` clears and it ends. When it does **not**
— the client's own latch gives up after ~2 s (docs/187 R9), the door can refuse
a staged payload (docs/65), a write can fail — nothing was recorded, so the next
poll advertises again. **Measured: 8 signals for one unresolved state, and
unbounded.** Each pull also takes `window._applyInFlight`, which gates the
manual Apply buttons — which is precisely the harm the original
`_auto_pull_due` comment was written to prevent.

**The fix** is the budget the push side has carried since docs/187
(`_AUTO_MERGE_TRIES`, 3), spent per SITUATION rather than per session: the
record now persists and counts, and a new fingerprint — another edit, another
live write — starts again at one. Exhausted, the signal stops and the banner,
already up, offers the explicit choices.

One record carries both verdicts (`live_auto_at`: `sig`, `conflicts`,
`merge_tries`), because two keys holding one fact is what the earlier sweep in
§5 already caught here.

**The gate and the door are pinned separately**, and the sweep is why. They
guard different harms — the door stops the SIGNAL, the poll gate stops the
client POSTing at all — and each shadowed the other:

- first sweep: two GREENs, because every test consulted `_auto_pull_due` first,
  so the door's own budget was never reached. Added a test that posts to the
  door directly (a stale tab, a retry — the poll and the pull are separate
  requests).
- second sweep: a different GREEN, the mirror image — with the door holding the
  line, the gate's half became unobservable. Added an assertion on the
  advertise decision itself.

**Sweep 6/6 red.** Total for docs/195: 8/8, 9/9, 7/7, 6/6.
