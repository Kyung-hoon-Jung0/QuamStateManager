# docs/255 -- a refused apply changes nothing; a touch is not a change

2026-10-03, agent validation campaign, stream D P1 cluster **D-03 / D-04 / A-11**
and the P3 "a refused approve leaves int->float drift that reached live"
(`D:\work\sm_qa_rigs\agent\FINDINGS.md`).

This is the one live-write path everyone uses: the window's Apply, Auto-Sync's
push, Dataset "Apply to chip", an agent's `apply_to_live`, a person's approval
of an agent run. Amends docs/28 (the gate), docs/65 (the re-apply stash on a
conflict), docs/107 (when the journal unit is committed) and docs/116 (the
identical-content fast path, now also at the route).

## Report

- **D-03.** Touch the live files without changing a byte (a backup tool, an
  editor's no-op save, QUAlibrate re-saving unchanged content). Every apply and
  every approve is now refused `stale_live`, while `live_diverged` is false and
  `take_live` says "already matches". There is no door out.
- **D-04.** A refused MCP `apply_to_live` leaves the agent's value in SM's
  working copy. The tray is empty and `live_diverged` is false, so SM holds a
  value live does not, and nothing on screen or in the agent API says so.
- **A-11.** A `stale_live` refusal still empties the tray (the door saves
  before it checks), and the undo journal gains a unit for a write that never
  happened.
- **P3.** A refused approve leaves int->float drift in the working copy, which
  the next apply carries to live.

## Root causes (lines at 6e8c731e)

| id | where | cause |
|---|---|---|
| D-03 | `core/working_copy.py:727-741` (`apply_to_live`) | the gate was `live_changed(wc)`: mtimes. Unchanged mtimes were confirmed by content hash; MOVED mtimes were taken as the answer. The docs/116 adopt only helps when the working copy equals live -- with an edit pending it does not, so a touch refused. Everything else in SM asks about content: `reconcile_with_live` ("mtime refresh", docs/28), `live_diverged_now`, the drift banner, MCP `take_live`. So the two sides of the same screen disagreed (base, real Chrome: "Your apply wrote nothing: the live chip had changed in between" directly under "The live chip has not changed since SM last synced"). |
| D-03 | `working_copy.py:793-801` | the TOCTOU re-check right before the write compared mtimes only, so a touch landing in that window refused too. |
| A-11 / D-04 | `web/routes.py:25421-25457` (`state_apply_to_live`) | stash -> journal prepare -> `saver.save()` (clears the change log) -> `_journal_commit` -> `working_dirty=True` -> only then `apply_to_live`, which refuses. Nothing put the save back. The window's tray then read the stash (docs/65), so a person saw "N unapplied", but `/api/agent/tray`, which reads the change log, saw 0, and `live_diverged` stayed false after a touch. |
| A-11 | `routes.py:25170-25193` (`_sync_pull_apply_to_live`) | the same save-before-check order on the other door (Pull & apply, the docs/65 staged carve-out, the Ctrl+Z walk, Auto-Sync's merge). |
| A-11 | `routes.py:25466-25478`, `25531-25546` | the Keep-mine `expect_live_hash` refusal and the `no_backup` refusal both returned AFTER the save. |
| P3 | `web/agent_api.py:1237` (`_take_back_saved`) | with the log saved away, an approval refused at the door was taken back by re-setting each old value through `Modifier.set_value`, which coerces to the field's CURRENT type. An int field that the approval had widened to float (`7126300123.4`) came back as `7126044234.0`. Measured on base. |

## Behaviour now

**The gate asks about content (D-03).** New `working_copy.live_moved(wc)`:
has the live CONTENT moved away from the sync point? A touch (mtimes moved, the
content hash unchanged; also a re-serialisation of the same documents, since
`content_hash` is over the parsed documents) is not a move. It **re-anchors**
the sync point's mtimes (meta first, the hash unchanged), exactly as
`reconcile_with_live` has always done. Both checks in `apply_to_live` use it:
the top gate and the re-check right before the write. The re-check reads
content only when the mtimes did move, so the common path costs nothing extra.
Legacy metas (no hash) and an unreadable live pair keep the old answer (the
mtime).

Why re-anchor and not just answer: every later look must agree. Without it, the
route's backup logic still saw "mtime moved", deferred the pre-apply backup
until after the write, and "Revert last apply" then held the new values. A pin
(`test_revert_last_apply_after_a_touch_points_at_the_pre_apply_values`) holds
that.

**A refused press changes nothing (A-11, D-04).** Two layers, one rule.

1. `_apply_preflight` decides the common refusal **before** anything is
   stashed, journaled or saved. Refused when the live content moved
   (`live_moved`) and is not already what the press would write. It is
   stat-first: with the mtimes where the sync point left them it reads nothing,
   and `apply_to_live`'s own content check (which always ran) remains the gate;
   a change the mtimes hid is refused there and put back by layer 2. The payload is
   the store's own documents (`content_hash(store.state, store.wiring)`), the
   bytes the save would write, so the docs/116 no-op ("live already holds
   this") is still not a conflict. Anything unreadable answers "not refused":
   the door's own gate still decides, as before. The refusal goes through one
   body for both cases, `_apply_stale_refusal`, which is the old
   `except StaleLiveError` branch moved out unchanged (conflict tray, Auto-Sync
   disarm or merge signal, docs/187).
2. A refusal that can only be seen **after** the save (a write racing the press,
   the Keep-mine confirm going stale, no backup possible) puts the save back.
   `_press_begin` / `_press_save` record what the press changes: the dirty flag,
   the re-apply stash, the working pair's exact bytes and stat fingerprint, the
   change log the save clears, and the store's mutation counter. Then
   `_press_undo` restores the bytes, the mtimes, the log entries and the flags.
   It does that only when this is provably the press's own work:
   - the working pair still has the fingerprint the save left;
   - the documents were not swapped;
   - every store mutation since is an edit appended after the save (the
     saver's own `_only_edits_since` rule).

   Otherwise the save stands, journaled, exactly as before this change. An edit
   typed during the press stays, after the restored ones.

The Keep-mine `expect_live_hash` check moved before the save (it reads live
only). The `no_backup` refusal puts the save back.

One thing a refused press may still leave behind is the sync point's recorded
mtimes, re-anchored after a touch. That is bookkeeping about live, not about
the press: the content hash it vouches for is unchanged, and every page load's
`reconcile_with_live` does the same.

**The journal (docs/107) is committed where the push is decided.** That is on
success, or on a failed write (a write that FAILED is not a refusal: a
read-only share or a locked `state.json` leaves the edits saved in the working
copy as before, and that save is journaled). Never on a refusal. "Two-phase"
keeps its meaning: the units are prepared under the store lock with the stash
and appended only once the outcome is known. The window this opens (the change
log cleared, the units not yet appended, for the length of the write) is
covered for the person by the client: `UndoQueue.pump` (`app.js`) holds a
Ctrl+Z press while `_applyInFlight` or any mutating request is in flight. For a
second window it is a timing edge, like the docs/80 cross-window stance.

**What SM holds that live does not is visible (D-04).** After a refusal the edit
is in the tray, the change log, where the window, `/api/agent/tray` and the
sync panel all read it. `live_diverged` is raised from the content verdict.
`stale_live` for a machine caller now says it: *"nothing was written and nothing
changed in SM -- 1 staged edit is still in the tray. Take live needs an empty
tray (undo yours first), or let a human merge"*, with `pending` and
`live_diverged` fields. The MCP bridge's existing advice ("undo, take_live,
re-stage") is now literally possible: an agent's `/undo` pops its own refused
group. Before, it hit an empty tray and was refused `journal`.

**P3.** An approval refused at the door now finds its group still in the log,
so `_stage_writes` takes it back with `undo_group` (`_take_back`), which restores
the recorded old values verbatim. `_take_back_saved` (the coercing path) is now
reached only when the live WRITE itself failed; see "Not done here".

**What it costs.** Measured on a synthetic 27.5 MB `state.json` (big30x is
19 MB), on this loaded machine:
- the common path (mtimes unmoved) adds one stat pair (0.04 ms) and, only when
  the tray holds edits, the raw snapshot of the working pair taken under the
  build lock before the save (13.9 ms);
- a moved mtime adds one live content read before the save (32.9 ms), which is
  what decides the refusal without saving.

`apply_to_live`'s own reads are unchanged.

**What a person sees.** The same refused state as before ("Apply wrote nothing
-- live changed · Resolve…", and the panel's Pull & apply / Re-apply / Take
live / Keep mine). The difference is that the edit is still an unapplied edit in
the grid and tray, not saved into the working copy. One visible consequence: like
any unapplied edit, leaving the page now raises SM's unsaved-edits guard
(`app.js` beforeunload). Before, a refusal had silently made the edit durable as
a side effect. Now it is exactly as durable as it was before the press.

## Not done here

- **The write-failure path still coerces.** When the live write itself fails
  (WinError 32, a read-only share), the save stands by design and an approval is
  taken back by `agent_api._take_back_saved`, which re-sets old values through
  type coercion. The same int->float drift is reachable there (measured
  mechanism: an int field widened by a non-integral float comes back `.0`). The
  fix is one keyword in `agent_api.py` (`set_value(..., coerce=False)`, the
  docs/107 rule for restorations). It is left to the agents working in
  `agent_api.py`, per this task's boundary.
- **The race-path rollback leaves the saver's `.bak` rotation file** in the
  working folder. That is the saver's own history of the pre-press content, not
  the working copy. The pins exclude it only on the race paths. The preflight
  path writes none.
- **A history BACKUP version can still be recorded on the race path.** It is
  taken before the write when the mtimes had not moved yet (QA F5's rule); this
  change does not delete history.
- Restart durability of the tray is unchanged (A-22: an SM restart drops staged
  rows). A refused edit now has the same durability as any staged edit.

## Verified

**Tests (cqt env).** The 77 test files that touch `apply_to_live` /
`stale_live` / `live_diverged` / the undo journal / auto-apply / sync /
`working_copy`, plus the new file:

- base `6e8c731e`: 2791 passed, 26 skipped, 0 failed;
- this branch: 2803 passed, 26 skipped, 5 failed. All 5 were pins of the old
  *mechanism*, updated to the new contract:
  - `test_auto_apply::...keeps_the_edit` and
    `test_autosync_merge::...still_there_to_be_merged` asserted the edit
    survived in `working_dirty or pending_reapply`; it now survives in the
    tray, which they assert;
  - `test_live_sync_server` x2 asserted `pending_reapply` after a refusal
    (the same-field gate still asks, and still merges a different field);
  - `test_live_sync_server::test_a_refused_push_is_not_listed_as_applied`
    asserted 3 journal units; A-11 is exactly that third unit. It now asserts 2,
    with the refused edit still in the tray where Ctrl+Z pops it first.

  Sixteen more related files (Ctrl+Z client, apply UX, undo trail/nav, FSP
  compensation, live diff, integration, chat, autofit engine, ...): base 295
  passed / 15 skipped, branch 295 / 15.
- **final tree, all 77 + 16 + the new file in one run: 3113 passed, 41 skipped,
  0 failed** (= 2791 + 295 + 27; the skips are base's 26 + 15).

**Pins.** `tests/test_refused_apply_changes_nothing.py`, 27 tests on real files
in `tmp_path`. Against base: **20 of 27 red**. The 7 green on base are the
contracts that held before and must keep holding:
- a real change is still refused;
- the docs/116 no-op;
- Keep mine and Take live after a refusal;
- a failed write keeps its save;
- the two "never put back over someone else's write" guards.

Mutation sweep: each piece reverted alone, the file run, then restored.
**22 of 22 red.**

| reverted piece | caught by |
|---|---|
| M1 `live_moved`: a touch counts as moved | `test_the_windows_apply_lands_after_an_mtime_touch` |
| M2 `apply_to_live` top gate back to the mtime | `test_apply_to_live_itself_accepts_a_touch` |
| M3 the pre-write re-check back to mtime only | `test_a_touch_between_the_check_and_the_write_is_not_a_conflict` |
| M4 the route preflight removed | `test_revert_last_apply_after_a_touch_points_at_the_pre_apply_values` (the preflight is also where a touch is re-anchored before the backup timing is decided) |
| M5 the preflight ignores the docs/116 no-op | `test_live_already_holding_the_payload_is_not_a_conflict` |
| M6 route `stale_live` after the save: the save stands (old) | `test_a_refusal_seen_only_after_the_save_puts_the_save_back` |
| M7 rollback: working-pair fingerprint guard removed | `test_a_refusal_never_writes_over_a_working_copy_it_did_not_write` |
| M8 / M9 / M10 / M14 rollback skips the log / the bytes / the flags / the mtimes | `test_a_refusal_seen_only_after_the_save_puts_the_save_back` |
| M11 the journal committed at save time again | `test_the_windows_apply_lands_after_an_mtime_touch` |
| M12 the pull-apply door: the save stands (old) | `test_a_staged_push_refused_after_its_save_changes_nothing` |
| M13 the Keep-mine hash check back after the save | `test_a_keep_mine_whose_confirm_went_stale_changes_nothing` |
| M15 the `stale_live` message no longer names the tray | `test_an_agents_refused_apply_changes_nothing_and_says_why` |
| M16 the `no_backup` refusal: the save stands (old) | `test_a_forced_push_with_no_backup_changes_nothing` |
| M17 the rollback guard rejects everything | `test_a_refusal_seen_only_after_the_save_puts_the_save_back` |
| M18 / M19 success no longer journals (route / pull-apply door) | `test_the_windows_apply_lands_after_an_mtime_touch` / `test_pull_and_apply_lands_both_values` |
| M20 a failed write no longer journals its save | `test_the_save_stands_journaled_when_the_live_write_fails` |
| M21 `live_moved` does not re-anchor the touch | `test_revert_last_apply_after_a_touch_points_at_the_pre_apply_values` |
| M22 the mutation-steps guard accepts anything | `test_a_refusal_never_puts_back_over_another_windows_save` |

The first sweep had two survivors, M7 and M21. Each was a guard with no test
that only it could fail, so the last two pins in the table were added for them.
The script that ran the sweep is `scratchpad/p1c255/mutate_255.py` (session
scratch).

**Real Chrome.** The rig was `D:\work\sm_qa_rigs\agent\rP1C`: a copy of the
campaign chip with the network at 127.0.0.1:1, port 5117, CDP 9437, a sandboxed
USERPROFILE/HOME. It was deleted afterwards. The journey is
`tests/browser/journeys/refused_apply.cjs`, run against both builds, each from a
fresh rig copy, with real mouse clicks. It ran on this branch twice: before the
preflight became stat-first, and on the final code. Both gave identical hashes.
Logs, screenshots and the `refused_apply.json` hash record are in
`D:\work\sm_qa_rigs\agent\ev_p1c255\` (`shots_final`, `shots_fix`, `shots_base`). Live `state.json`
SHA-256 (first 12 hex digits); `wiring.json` stayed `92316ec9c154` throughout:

| step | this branch | base 6e8c731e |
|---|---|---|
| edit qA1.T1 in Live State Edit, touch live (same bytes) | `2edb87fddd35` -> `2edb87fddd35`, mtime +7 s | same |
| tray Apply (`doStateSync('apply')`, the pull-merge door) | lands, `283ea2414bf5` | lands |
| Save to working state, touch, review **↑ Apply** (`/state/apply-to-live`) | **lands**, `64671faccff8` | **refused**, live stays `283ea2414bf5` (D-03, screenshot) |
| Auto-Sync push-only, touch, edit -> flush | lands, `ec0b0eb78af2` | the Auto-Sync popup never offered its switches (the chip stayed "refused"), so nothing flushed |
| outside write of different bytes, edit -> flush | **refused**, live stays the outside writer's `ba99ca4828f8`, tray `1 unapplied` / refused, `/api/agent/tray` count 1, `live_diverged` true | no flush; the tray had piled up 2 unapplied edits on top of a working copy still holding the refused `1.32e-5` |
| sync panel | Pull & apply · Re-apply · Take live · Discard · Save · Keep mine | — |
| open /qubits -> back -> reload | the edit is still in the tray, "Apply wrote nothing -- live changed"; leaving raised the unsaved-edits guard once | — |
| Pull & apply | both values land (`T1 1.34e-5`, `qA2.T2ramsey 4.1e-5`), `daf72cab5c63`, In sync | — |

Console: 0 JS exceptions, 0 console errors, 0 log errors. Chrome logged 2
interventions ("Blocked attempt to show a 'beforeunload' confirmation panel for a
frame that never had a user gesture") for the automated back/reload; the journey
reports them apart rather than dropping them.

## Follow-up at integration: the write-failure take-back is verbatim too

`agent_api._take_back_saved` (a live write that fails after the save) now writes each old value with `coerce=False`. Before, an int field widened by a float came back as `5000000000.0`.

**Pin:** `tests/test_takeback_verbatim.py`. Dropping `coerce=False` turns it red.

