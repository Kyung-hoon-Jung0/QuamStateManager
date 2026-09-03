# 107 — Cross-save undo journal (Ctrl+Z / Ctrl+Shift+Z) + tray ✕ no-confirm + Discard all

*2026-08-10. User feedback: after Apply-to-live, Ctrl+Z could not walk back
through earlier modifications, and no redo existed anywhere in SM. Wanted:
free Ctrl+Z / Ctrl+Shift+Z across the save/apply boundary — plus the tray's
per-change ✕ demanding a confirm on every press, and no way to revert all
staged modifications at once.*

## The covenant (binding, user-stated)

> 우리 SM의 큰 규약중에 하나는 user가 apply to live chip 버튼을 누르는 행위가
> 최소 1번은 있어야 live를 직접 chip에 업데이트한다.

**Any direct live write requires ≥ 1 explicit press of Apply-to-live.** The
user explicitly chose STAGING semantics (option b) over direct-to-live undo
(option a): a journal step only stages inverse edits into the review tray;
reaching the chip still takes the Apply press. This is the same doctrine
family as docs/104 #1 (the Apply button needs no confirm — the labeled press
IS the consent): consent = one explicit Apply act, no more (no dialogs), no
less (no silent writes).

### AMENDED 2026-08-12 by the user (docs/117)

> 즉, auto apply를 함으로써 SM은 이제 사용자가 무언가를 변경하면 곧바로 state에
> 적용되게 하는거지. … 값을 modify하고 그 셀을 나가기만 하면 곧바로 live에
> 적용이 되는거야.

**A direct live write happens only on an explicit Apply press OR inside a
user-enabled auto-apply session** — default OFF, always visible while armed,
and auto-disarmed the moment the chip refuses a write. The floor did not move:
one explicit user act still stands between an edit and the live chip. What
changed is its SCOPE — the act now authorizes a session rather than a single
write, which is exactly what the user asked for and what the pill makes
impossible to forget. Everything the old sentence protected is still true:
nothing writes live silently, nothing forces over a chip that moved, and every
applied change stays revertible (now individually, from the applied log).

The journal itself is unchanged by the amendment: a journal step still only
STAGES. Under an armed session that staged inverse is then flushed by the same
`/state/apply-to-live` press-alike as everything else — one door, not two.

### AMENDED AGAIN 2026-08-16 by the user (docs/120 item 8)

> 많은 성숙한 사용자들은 vscode의 auto save mode를 켜놓고 쓰거든? … SM은 원래
> **초기 디자인 컨셉** 자체가 절대로 source of truth 자체는 *유저의 허락없이는
> pull/push*를 하지 말자! 라고 컨셉을 잡았거든. 이제 이걸 버릴때가 온거지.
> 유저가 원하는 것은, **허락하면 auto pull/push를 할수있도록!!** 하는 것이거든.

docs/117 amended the covenant's scope for **writes to live**. This amends the
other direction for the first time — replacing the WORKING COPY from live —
because until now that also required an explicit act.

**Auto-Sync** is one session with three switches (default OFF as a whole; all
three ON inside the popup, per the user): auto pull · auto replace on pull ·
auto push. The line the user drew, verbatim:

> 사용자가 1-1을 체크박스를 활성화한다! 라는 행위자체가 이미 사용자가 replace를
> pull할 때 조용히 무조건 그냥 하도록 하겠다고 동의하는 행위로 간주할 수 있기
> 때문에 1-1이 활성화 되었을 때는 **Live상태가 무조건 이긴다**.

So the floor still has not moved — it is the same shape as before, one
explicit act authorizing a session:

| pull | replace | local edits | behaviour |
|---|---|---|---|
| on | on | either | live wins, **silently** — the tick IS the consent |
| on | off | no | pull silently (a provably clean copy — today's `RECONCILE_SYNCED`) |
| on | off | **yes** | **do not pull**; the drift banner asks |
| off | — | — | byte-identical to today, and that row is a test |

The third row is docs/87 ("SM never swaps what you are looking at") intact:
with replace unticked, SM still refuses to choose between the user's work and
the chip. The only configuration in which SM discards work without a question
is the one the user explicitly asked for.

Where it lives: the POLICY is entirely inside `POST /auto-sync/pull`, so it can
never be half-implemented across client and server. The client's only job is to
notice `auto_pull` on the existing `/state/drift` poll (no new poller — docs/110)
and press the button, sharing `window._applyInFlight` with the manual Apply and
the push flusher so the two directions cannot interleave. Arm-ability is split
per direction: the push gate refuses on `live_diverged`, which is correct for
push and exactly backwards for pull.

## What was actually the boundary

The change log dies on **/save** (`saver.save()` clears it), not on apply —
apply calls save first. So "cross-apply undo" is really cross-SAVE undo, and
the capture points are the three save sites (`/save`, the pull-apply helper,
`/state/apply-to-live`).

## Design

### The journal (`core/undo_journal.py`, pure)

- `segment_change_log(log)` splits a change log into user-action units —
  contiguous same-gid runs, `None` ⇒ singleton. Provably ≡ what iterated
  `undo_group` would pop (the property `tests/test_undo_journal.py` pins).
- Sidecar `instance/working_state/<key>.undo_journal.json` (sibling FILE of
  the working-copy dir — the GC scan iterates `is_dir()` children and never
  sees it), keyed by `working_copy.key_for`, written via
  `safe_io.atomic_write_json` under a module lock; bound 200 units, trimmed
  only at append (capture ends at the tip, so the cursor can never point into
  a trimmed range **by construction**).
- **Two-phase capture**: units are serialized inside the same `store._lock`
  hold that stashes the reapply updates (phase 1, `_journal_prepare`), but
  appended to the sidecar only after the save SUCCEEDS (phase 2,
  `_journal_commit`) — a failed save keeps the log, and appending at prepare
  time would duplicate the units on the retry. All journal I/O is advisory:
  a failed write never fails the save.
- **cursor := len(units) after every capture and on every fresh context
  build / wholesale replace** — one RAM rule (never persisted, never derived)
  that collapses restart, LRU eviction and pull/stage/restore into the same
  edge. The cached fast path deliberately does NOT reset: a live ctx's
  units+cursor+staged steps are coherent RAM state, and resetting mid-walk
  would let the next Ctrl+Z re-stage a unit already sitting in the tray.

### /undo routing (no peek — docs/73 holds)

Top log group ordinary ⇒ today's `undo_group`, byte-identical (plus a redo
push). Log empty **or** top group's gid starts `jrn:` ⇒ **journal step**: the
cursor−1 unit's inverse is staged via the modifier under gid `jrn:<unit-id>`
— entries iterated in REVERSE (a rename's inverse re-creates the old subtree
before deleting the new one, mirroring `undo_group`'s LIFO), values restored
VERBATIM (`coerce=False` — the field's current type may differ, e.g. a later
type-fix, and coercion would cast the restoration back). The `jrn:` prefix is
the routing marker: /undo seeing it on top walks DEEPER; /redo seeing it
un-stages. It survives segmentation for free — saving staged journal steps
records them as ordinary units again, so the history stays walkable
(emacs-style; there is deliberately no journal-forward-walk).

- **All-or-nothing**: a partial staging (create clobber — the key re-appeared)
  rolls back LIFO, answers 409 "nothing changed", cursor unchanged.
- **Drift reported, never blocking**: values that moved since the unit was
  recorded are counted off the modifier's own returned `old_value` (zero
  extra reads) and named in the toast; staging proceeds (it is covenant-safe).
- Exhausted / archive / cursor 0 ⇒ the same silent unchanged-tray no-op the
  empty-log Ctrl+Z always produced (pinned byte-equal). Archives never get a
  sidecar.
- The response reuses the exact `/undo` tray + `cellsReverted` shape — UndoNav
  and the typing stash work unchanged, zero client special-casing.

### POST /redo (Ctrl+Shift+Z) + the seq handshake

Top group `jrn:` ⇒ un-stage it (this IS `undo_group`) and cursor+1. Otherwise
pop `ctx["redo_stack"]` and re-apply the group forward (fresh gid for multi-
entry ordinary frames, `None` for singles; only `jrn:` gids are ever reused —
they carry the cursor bookkeeping: re-staging a discarded journal step
re-consumes its unit).

**Fork detection is a mutation_seq handshake, zero modifier hooks**: every op
of OUR machinery (undo / redo / journal step / discard / discard-all) stamps
`ctx["redo_seq"] = store.mutation_seq` afterwards; `_redo_begin` — called
before our next pop/push — clears the stack when the seq moved without a
stamp. Edits, reloads and pulls all bump the seq, so any foreign mutation
kills the dead timeline exactly like typing after undo in an editor. (A
per-frame seq snapshot cannot work: consecutive undos each bump the seq and
would invalidate their own older frames — the single expected-seq is the
correct realization of the plan's intent.)

### Client (app.js)

The capture-phase Ctrl+Z guard forked on `shiftKey`: undo chain unchanged
(now also records `window._lastUndoTier`); Shift ⇒ redo chain — wizard
mounted swallows (the wizard has no redo; falling through would act on the
chip behind the user's back), `LiveEditUndo.tryRedo()` (new second stack;
`record()` and `clear()` both empty it; a cell that moved since is never
clobbered), else `POST /redo`. Ctrl+Y deliberately unbound (native in-field
redo). Same input-focus guards as undo.

### Tray UX

- The per-change ✕ lost its `hx-confirm` — **recoverability licenses
  no-confirm**: `/discard` now pushes the discarded entry onto the redo stack
  (as an ordinary frame — a ✕ inside a staged journal step never moved the
  cursor, so its redo frame must not either), and the ✕ tooltip says so.
- **Discard all** (tray drawer foot, beside Save): `POST /discard_all` →
  `modifier.undo_all()` (new, core — loops `undo_group` to empty under ONE
  lock hold, returns groups newest-first). Each group lands on the redo stack
  in that order, so Ctrl+Shift+Z restores them one by one **in original edit
  order**. Staged `jrn:` groups un-stage too and the cursor moves back by
  their count. No confirm, same visual language as the ✕
  (`.btn-discard-all`).

## Edge rulings

Wholesale replace (pull/stage/restore): journal KEPT, cursor→tip
(`_rebuild_after_working_copy_replaced` calls `_journal_reset`), redo dies via
seq. LRU/restart: units reload from the sidecar, cursor→tip, redo dies
(correct — its RAM targets died). Multi-window: same-process serialized;
cross-process sidecar is load-merge-write + atomic write with residual
last-writer-wins TOLERATED (the journal is advisory history; the
apply/conflict machinery is the correctness layer — docs/80 stance). A ✕ on
one entry of a staged journal step: cursor untouched, the entry redoes as an
ordinary edit (documented asymmetry, recoverable). **Un-stage outranks the
redo stack** (observed in the real-browser pass): with a `jrn:` group on top,
Ctrl+Shift+Z always un-stages it first and only then replays discarded/undone
frames — Shift+Z walks the staged journal back up before replaying history,
which keeps Z/Shift+Z symmetric on the journal walk; the shadowed frames stay
on the stack and surface on the following presses (verified: ✕ → Shift+Z
un-stages the OTHER staged step → Shift+Z restores the ✕'d row).

## Files

`core/undo_journal.py` (new) · `core/modifier.py` (`undo_all`) ·
`web/routes.py` (capture ×3 two-phase, `/undo` journal branch + redo push,
`/redo`, `/discard_all`, `/discard` redo push, `_journal_reset` at fresh
build + rebuild) · `_pending_tray.html` · `style.css` (`.btn-discard-all`) ·
`app.js` (guard fork, redo chain, LiveEditUndo redo stack).

## Verification

`tests/test_undo_journal.py` (19: segmentation property, sidecar restart
persistence, the full Z/Shift+Z editor walk, seq invalidation incl. reload,
reversed inverse ordering with a real rename round-trip, drift toast,
poison-unit atomicity, archive no-op + no sidecar, cursor-0 byte-equal no-op,
staged-steps re-save walkability, discard-all ordered restore + jrn cursor
restore, single-✕ redo, no-hx-confirm pin) + `ctrlz_selfcheck.cjs` extended
(Shift+Z request shape + guards + wizard swallow; LiveEditUndo
undo→redo→undo round trip, fork clear, moved-cell guard, clear()).
Byte-identical suites green: `test_modifier.py`, `test_state_roundtrip.py`,
`test_undo_nav.py`, `test_ctrlz_client.py`, `TestBatchUndoAtomic`,
`test_column_history.py`, `undo_nav/tab_focus/state_sync/wiz_undo/
sidebar_tools` selfchecks.

**Real-browser pass** (a real 21-qubit chip copy, Chrome, screenshots in
`D:\work\sm-screenshots\2026-08-10_undo-journal\`): edit → ⚡Apply → Ctrl+Z
staged the inverse with the docs/76 Δ chip; Z Z walked both units; a server
RESTART mid-walk reloaded the journal from the sidecar and staging continued;
Shift+Z un-staged 2→1→0→silent; the ✕ discarded immediately (no dialog) and
came back via Shift+Z; Discard all emptied the tray and Shift+Z restored. The
pass caught ONE real bug: `window._wizUndo` exists on EVERY page (generate.js
is head-loaded) and the redo chain's bare existence check swallowed
Ctrl+Shift+Z app-wide — fixed by the new `_wizUndo.mounted()` probe (the
same `root()` gate `tryUndo` already used internally), pinned both ways in
`ctrlz_selfcheck.cjs`.

docs/20:755 amended: Ctrl+Z crosses the save/apply boundary **only by loud
staging** — live writes remain Apply-only.

### AMENDED A THIRD TIME 2026-09-03 by the user (docs/160)

> apply to chip을 적용한 이후에도, ctrl z가 되기를 강력하게 원하고 있음.

**A direct live write happens on an explicit Apply press, inside an armed
Auto-Sync session, OR as the undo/redo of a change that reached the chip
through one of those.** The Apply press was the consent for the change; Ctrl+Z
withdraws that same consent and needs no second press. A journal step now
stages the inverse AND pushes it through the one apply door (default ON,
machine-wide, Settings → "Ctrl+Z writes live"; OFF is this document's
behaviour byte-for-byte). Wholesale loads (stage → Apply, dataset Apply to
chip, restore-live) become one journal unit each. See docs/160.
