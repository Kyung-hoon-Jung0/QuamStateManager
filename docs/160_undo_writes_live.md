# 160 — Ctrl+Z after Apply-to-live writes the chip

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (fifth commit) · **Covenant amendment #3**

## The ask (CRITICAL, many users)

> apply to chip을 적용한 이후에도, ctrl z가 되기를 강력하게 원하고 있음… 안정적이고
> 무엇보다 빠르게 작동이 되도록.

After the feasibility read (§1 of the conversation record) the user chose:
**default ON, a Settings toggle to turn it off**; A → C → B, all three.

## 1. What the walk already did, and where it stopped

docs/107 journals every save/apply as user-action units (sidecar
`instance/working_state/<key>.undo_journal.json`, 200 units, restart-safe),
so Ctrl+Z already crossed the apply boundary — but a journal step only
STAGED the inverse into the review tray, under the covenant "a live write
needs one explicit Apply press". From the user's chair: "I pressed Ctrl+Z
and the chip did not change" — the last step (press Apply again) is what
they experienced as "undo does not work after apply". Users with Auto-Sync
push ON already had the live undo without knowing it.

And three paths produced no journal unit at all (docs/65: wholesale content
has no change-log entries): State-History **stage → Apply**, the dataset
**Apply to chip** button, and **restore-live**. After those, Ctrl+Z had
nothing to walk; only ↺ Revert last apply worked.

## 2. The amendment

> **A direct live write happens on an explicit Apply press, inside an armed
> Auto-Sync session, OR as the undo/redo of a change that reached the chip
> through one of those.** The Apply press was the consent for the change;
> Ctrl+Z withdraws that same consent and needs no second press. Machine-wide
> setting, default ON, Settings → "Ctrl+Z writes live"; OFF is docs/107
> byte-for-byte.

Every write still goes through the ONE door (`_sync_pull_apply_to_live`,
the core the ⚡ button and the Auto-Sync flusher press) with its staleness
gate: a chip that moved is never clobbered.

## 3. What shipped

### A — the live walk

- `_undo_journal_step` stages the inverse (unchanged), then
  `_undo_live_flush` pushes it through the door with `journal=False` — the
  walk step is recorded by the CURSOR, never as a new unit (else the next
  Ctrl+Z would undo the undo). The cursor is persisted in the sidecar
  (version 2, `"cursor"`), so a restart or another window resumes where the
  chip stands. A redo frame `jrn_live` goes on the redo stack.
- **Ctrl+Shift+Z** after a live undo: `_redo_journal_forward` CAS-checks the
  unit's OLD values (the undo put them there — anything else means the chip
  moved), stages the forward ops under the unit's `jrn:` gid, flushes, and
  on any refusal un-stages so nothing is half done. The redo stack is
  RAM-only; when it is empty but the persisted cursor sits below the tip
  (after a restart), `/redo` walks the journal forward from the cursor.
- **The editor rule**: `append_units` truncates the units past the
  sidecar's persisted cursor before appending — a new action after a live
  undo discards the redo branch, so the journal stays a straight line of
  what is in effect. A staged-only step never moves that cursor, which is
  what keeps the OFF mode identical (a re-save of staged steps still
  appends them as units — `test_save_of_staged_steps_appends_units`).
- **Stages instead, and says why** (the toast + the Undo trail's tier):
  the setting is OFF · the working copy is not provably at the sync point
  (`_working_at_sync_point`: unapplied edits in the tray, a staged snapshot,
  saved-but-unapplied content — the door pushes the WHOLE working copy, so
  anything else it carries would ride onto the chip under a keypress that
  never meant it) · the journal's recorded value had moved · archive /
  read-only · a LIVE foreign window applied it.
- **Rolls back, all-or-nothing, when the door refuses** (the live files
  moved since the sync — the staleness gate — or the write failed): the door
  had already saved the staged step into the working copy, so
  `_rollback_walk_step` re-applies the pre-step values, saves again, clears
  the stash and the dirty mark, restores the cursor, and the toast says
  "Not undone — … take the live changes (drift banner), then undo again".
  Nothing changed anywhere. Same on the redo side.
- **Runs outside `store._lock`**: the door takes the build lock, and every
  wholesale-replace path (pull, stage, reconcile) takes the build lock and
  then `store._lock` — the first cut held the burst lock across the flush,
  an ABBA deadlock the review reproduced with two threads.

### B — wholesale loads are one unit

`_wholesale_unit` is a TREE diff of the CHIP's merged tree, read right
before the write (`_live_merged_tree`, inside the build lock in both apply
cores and in restore-live), against the working copy: a changed value is a
`set`, a subtree the chip gained or lost is ONE `create`/`delete` at its
highest changed ancestor, a list is a whole value — ONE unit, meta
`{wholesale, src}`. Both apply cores commit it when `staged_base` reaches
the chip; restore-live commits its own. Capped at 4,000 entries: past that
the unit is recorded with `too_large` and Ctrl+Z names it ("too many for
Ctrl+Z — use ↺ Revert last apply") instead of silently walking past.

Two things the first cut got wrong here, both found by the review: `old`
came from the WORKING view at stage time (unsaved in-memory edits included,
so Ctrl+Z wrote a value the chip never held), and the diff was per LEAF (a
deleted pulse's leaves had no parent to create into — 409; undoing an added
pulse leaf by leaf left an empty `{}` shell on the chip).

### C — another window

Units carry `meta.owner_pid`. `_journal_unit_foreign` stages (never
writes) a unit applied by another SM process that is still alive; a dead
owner (this window restarted) is ours. `_journal_sync` stats the sidecar
before every walk and re-reads units + cursor when another process wrote it.

## 4. Measured

| check | result |
|---|---|
| `tests/test_undo_live.py` | 42 (after §5b/§5c): the walk down/up, persisted cursor, truncation, OFF mode, pending edits, saved-but-unapplied content, a staged snapshot, drift, a chip that moved (rolled back, never clobbered; redo refused too; the walk resumes after taking the live changes), archive, restart (journal-forward fallback), the flush outside `store._lock`, stage→apply unit, dataset Apply-to-chip unit, restore-live unit, the unit's `old` = the chip's value, structural subtree/list round trip, too-large, foreign live owner (both directions), dead owner, the applied-log ✕ keeping the cursor, live-redo triggers, a redo frame bound to its unit, sidecar re-read |
| `tests/test_undo_journal.py` (OFF mode) | 21, unchanged pins |
| regression (undo, auto-apply, ctrlz, undo-trail, versions, roundtrip, live-replace, sync, overwrite, dataset apply, unseen edits, multi-instance, revert payload, sidebar tools, float panel, all of test_web) | 850 passed |
| node selfchecks (ctrlz, undo_trail, undo_pages, sidebar_tools, float_panel, auto_apply) | green |

Real Chrome (headless CDP, real key events, a copy of the CQT 20Q chip):
edit `q2.f_01` in the grid → tray 2 (f_01 + the f01↔RF-synced `RF_frequency`)
→ **Apply to live** → live file `[…001, …001]` → **Ctrl+Z** → `[…001, …000]`
(trail "→ live") → **Ctrl+Z** → `[…000, …000]`, the cell repainted →
**Ctrl+Shift+Z ×2** → `[…001, …001]` → Settings toggle OFF → **Ctrl+Z** →
tray 1, live unchanged, trail "staged" → **Ctrl+Shift+Z** un-stages.

## 5. Honest limits

- **Speed**: the decision and the screen are RAM (ms); the chip write is a
  file write — ~0.35 s local (docs/141), more on an SMB share. No design
  avoids it while the live file is the source of truth. One write per press;
  a burst still coalesces on the client (docs/141 §4e).
- **Two units for one synced edit**: editing `f_01` with "Sync f₀₁↔RF" writes
  `RF_frequency` as a separate change-log entry with its own gid, so Ctrl+Z
  needs two presses — pre-existing segmentation (the in-memory undo pops
  them one at a time too), visible now that each press writes the chip.
- **A refused flush changes nothing** — and the user must resolve the drift
  (take the live changes) before Ctrl+Z will write again. The press is not
  lost silently: the toast names the reason and the next step.
- **RAM redo frames die with the process**; the journal-forward fallback
  covers redo after a restart only from the persisted cursor upward.
- **A unit another LIVE window applied is never written from here**, in
  either direction; the toast names the window's pid. A dead owner (this
  window restarted) is ours.

## 5b. The review round (same day)

An independent adversarial review of the first cut (12 scratch pins, all
reproduced) returned 3 CRITICAL, 3 MAJOR, 6 minor — every one accepted:

| | finding | fix |
|---|---|---|
| C1 | ABBA deadlock: the walk held `store._lock` across the flush, which takes the build lock; pull/stage/reconcile take them the other way round (reproduced with two threads) | the journal step / journal-forward run OUTSIDE the burst lock; pinned by a spy on `store._lock._is_owned()` |
| C2 | the flush gate looked only at the change log: `/save`d content and a staged snapshot rode onto the chip, the staged one unjournaled | `_working_at_sync_point` (log, `staged_base`, `working_dirty`, `pending_reapply`) on both sides |
| C3 | the wholesale unit's `old` was the working view incl. unsaved edits | `old` = the live tree read right before the write |
| M1 | per-leaf structural diffs could not be replayed (409) or left `{}` shells | tree diff: one op per subtree, lists whole |
| M2 | a refused redo/undo was not all-or-nothing (the door saves before it writes) | `_rollback_walk_step` |
| M3 | `mark_unit` (applied-log ✕) dropped the persisted cursor | preserved |
| m1–m6 | owner rule missing on redo; no drift/versions triggers on a live redo; cursor not persisted after a refusal; redo frame not bound to its unit; `too_large` count off by construction; `last_apply` clobbered under an armed session | all fixed |

Five things the review checked and found fine are worth keeping: OFF mode
byte-identical; the truncation rule; the staleness gate never clobbered a
moved chip in any scenario; the docs/120 unseen-edit gate stays safe because
the flush requires an empty log; the `alr:` applied-log path untouched.

**Round 2** re-verified all 12 (14 pins, all passing on the fixed tree; the
13 neighbouring suites 256 passed) and found three defects the FIXES had
introduced, all reproduced and fixed the same hour:

| | finding | fix |
|---|---|---|
| N1 | `_rollback_walk_step` walked the staged list forwards; a unit touching one path twice rolled back to the MIDDLE value and saved it with `dirty=False` — a silent divergence from the sync point | LIFO inside the helper; pinned with a two-entry unit against a moved chip |
| N2 | `_journal_sync` judged "staged step alive" by the TOP log entry only; an `alr:` revert above a staged step let a sidecar re-read reset the RAM cursor to the tip — the docs/107 hazard (re-staging a unit already in the tray), in the OFF mode that must be byte-identical | any `jrn:` entry in the log counts; the ✕ route refreshes the mirror's mtime |
| N3 | the `too_large` skip PERSISTED a cursor below a unit still in effect; the next save truncated that unit away | the skip is RAM-only (docs/107), the forward step over an empty unit says so and writes nothing |

The lesson of the round is the one docs/141 §4ae already recorded: a suite
that is green says nothing about the states its fixtures cannot enter —
every one of N1–N3 lived in a state no shipped pin had entered (a two-entry
unit under a moved chip, an `alr:` group above a staged step, a save after a
skipped unit). All three are pins now.

## 6. Files

`core/undo_journal.py` (v2 sidecar, `load_state` / `save_cursor` /
`sidecar_mtime` / `forward_ops`, cursor-aware `append_units`),
`web/routes.py` (`_undo_live_enabled` / `/settings/undo-live`,
`_journal_sync` / `_journal_persist_cursor` / `_journal_unit_foreign`,
`_undo_live_flush`, `_redo_journal_forward`, `_wholesale_unit` +
`_journal_wholesale_commit`, the `journal=` flag on the apply core, the
stage/restore/dataset hooks, `undo_live` in `_ctx`), `base.html` (Settings
toggle), `app.js` (`toggleUndoLive`), `undo-trail.js` ("→ live" tier).

## 5c. The /code-review sweep (same day, after the commit)

A ten-finder code review over the six docs/156–161 commits confirmed two more
docs/160 defects before it was cut short by a session limit (its remaining
verifications were re-run by hand):

| | finding | fix |
|---|---|---|
| S1 | the Settings toggle POSTed a bare flip — two windows share the setting, so a press on a stale button turned it the wrong way (the docs/120 class: a press must mean what the presser could see) | the client sends `enabled=<inverse of what its button shows>`; an explicit value is idempotent, never a flip |
| S2 | the Auto-Sync applied log ignored the walk cursor: a unit Ctrl+Z had undone on the chip still offered an armed ✕, which then 409'd "has changed since", blaming a foreign write | a unit at or past the cursor renders **undone** (Ctrl+Shift+Z brings it back) with no ✕; the revert route says "already undone" |

Both pinned in `tests/test_undo_live.py`.

The sweep's full report (delivered after two interruptions, every verdict
re-derived by hand against the tree) named eleven more, all fixed in the
same commit as this section:

| | finding | fix |
|---|---|---|
| F1 | a staged snapshot applied TOGETHER with tray edits (docs/65 mixed state) journaled the edited paths twice — once as the edit unit, once inside the wholesale unit — so the second Ctrl+Z staged a value the chip never held | the wholesale unit excludes the edit unit's paths and is INSERTED BELOW it (`undo_journal.insert_units`): the walk undoes the edits first, then the base |
| F2 | the tray ✕ on a staged step's last entry left the RAM cursor below a unit still in effect; the next live undo persisted it and the next save truncated the unit away | `/discard` moves the cursor back up when it un-stages a whole `jrn:` group (as `discard_all` already did) |
| F3 | when the door's own SAVE failed, the staged step was still in the log and the rollback appended N inverses on top — 2N phantom edits under "nothing was written" | the rollback pops OUR entries by identity when they are still in the log, else re-sets + saves |
| F4 | a wholesale unit's walk response carried up to 4,000 entries in the HX-Trigger HEADER — past the project's own `_HEADER_PATCH_CAP` Chrome rejects the response, after the chip was written | past the cap: no entries, `structural: true`, the client resyncs wholesale (`stateRestored`) |
| F5 | `log_was_empty` sampled outside the lock + no unseen-edit gate on the walk's door: a concurrent edit from another window could ride the keypress onto the chip, un-journaled | sampled under the lock; right before the door, under the lock, the log must hold nothing but the step's own group — else `foreign_edit`, rolled back, the stranger's entry left in the tray |
| F6 | `_journal_unit_foreign` trusted a bare PID-alive probe — after a restart any process that inherited the old pid made the user's own history "another window's" | only a pid in the docs/80 `instances.peers()` registry is foreign |
| F7 | `nan != nan` made every NaN leaf a phantom wholesale entry; the drift check then refused every wholesale undo on a NaN-bearing chip | `_differs`: two NaNs are the same value (lists/dicts element-wise) |
| F8 | the redo CAS resolved THROUGH a pointer leaf and compared the target with the unit's old (the pointer string) — every pointer re-link redo refused | the CAS reads the raw leaf at the journal's path, as the modifier writes it |
| F9 | `_list_pair_cell` emitted the ALIAS as `data-resolved`, so the docs/159 repaint could not find a pointer-aliased pair list cell | the resolved leaf, as the qubit-grid twin always did |
| F10 | a list's `old_value_disp` is the grid's 24-char preview, and the inspector / tree / Undo trail consumed it as the lossless value | `old_value_json` rides beside it; those consumers prefer it |
| F12 | a REFUSED live redo dropped its frame; the next Ctrl+Shift+Z re-applied an older in-memory frame out of order | the frame goes back on refusal |
| + | the walk's `level: "warning"` toasts rendered green | the toast honours `warning` |

Two more outside this document: docs/158's filter form lost `chip_key` on an
archived chip's view (the loaded chip's results landed under the other chip's
header — fixed, and the busy-index banner moved inside the results
container), and docs/156's ↗ after a full page reload NAVIGATED the still-open
calculator window (same name), wiping typed values — the page now pings a
`BroadcastChannel` first and only silence opens a new window (CDP-verified:
one window, same target, the typed value survives). Pinned in
`tests/test_undo_live.py` (42) and `tests/test_sweep_misc.py` (7).

## 5d. The second /code-review round (2026-09-03)

The same reviewer, re-run over the branch INCLUDING its own sweep commit,
returned fifteen findings. All fifteen were re-derived against the tree; two
turned out to describe the app's standing model rather than a defect
introduced here (noted below). The rest are fixed in one commit.

### The live walk (docs/160)

| | finding | fix |
|---|---|---|
| R1 | the sweep's own F12 fix — re-push a refused redo frame — JAMMED the stack for any NON-transient refusal (the setting turned OFF, a `unit_id` the journal moved under, an index the cursor had passed): every later press met the same refusal and the frames beneath became unreachable | a frame that no longer NAMES the walk cursor is stale: dropped, and that press does nothing else (it must never be applied to a stranger's unit — review m4). A refusal that leaves the step still next in line keeps its frame, as before |
| R2 | the empty / too-large skip moved the cursor with NO redo frame, so the redo stack and the walk drifted apart — the unit UNDER the skipped one could never be redone | every step that moves the cursor pushes a frame (`_push_jrn_live_frame`), skips included; an empty unit now also says so instead of looking like a dead key |
| R3 | the ✕ that un-staged a journal step moved the cursor up (sweep F2) but the redo that re-staged it did not move it back — the next Ctrl+Z staged that unit's inverse a second time on top of itself | the redo frame keeps its `jrn:` gid exactly when the ✕ moved the cursor |
| R4 | every live press re-anchored ↺ **Revert last apply** on the undo itself, so the button silently came to mean "redo what I just undid" | a walk step (`walk=True`) leaves `last_apply` alone — it is not an apply |
| R5 | and it took TWO full history snapshots per press (pre-apply backup + post-apply save), with none of the docs/117 throttle auto-apply added for exactly this | the pre-apply snapshot is gone for a walk (its only consumer was R4's anchor) and the post-apply one is throttled per chip by `_AUTO_SNAPSHOT_MIN_S`, as the auto-apply session does. A held Ctrl+Z is one gesture, not N archive copies |
| R6 | a coalesced `?n=k` walk press answered `stopped: null`, so the client dropped the remaining k−1 presses (the ordinary burst path signals `journal` for exactly this) | `_walk_burst_extra`: every walk response — down, up, and the skip — reports requested / consumed / stopped |
| R7 | a wholesale DELETE whose subtree contained an excluded (already-journaled) edit path was dropped entirely, leaving that subtree unrecoverable | the delete is recorded; its `old` is the chip's whole subtree, which restores the leaf too |
| R8 | `insert_units` read the persisted cursor and discarded it, writing the tip — an insert after a live undo resurrected units the chip no longer holds | it truncates at the cursor first, exactly as `append_units` does |
| R9 | `jrn_live` frames were appended raw, bypassing the `_REDO_MAX_FRAMES` cap every other frame obeys | pushed through one helper that caps |
| R10 | `_journal_sync` scanned `store.change_log` outside `store._lock`, while the caller takes it for the rest of the burst | the scan takes the (reentrant) lock |
| R11 | `_journal_unit_foreign` — a predicate on every press — did a directory scan plus one PID probe per registry entry | memoized for 2 s (never under TESTING). The unlinking of dead entries is `peers()`'s documented self-cleaning, kept |

### Outside the walk

| | finding | fix |
|---|---|---|
| R12 | the docs/159 list-cell repaint wrote the reverted preview but left `bulk-cell-modified` on — and still reported the path covered, so no rebuild followed: a reverted value sat under a red "unapplied edit" box forever | the class goes with the value (what the `<input>` branch already did through `data-orig`) |
| R13 | the pair grid's COLD-column search text was patched with the qubit grid's 24-char JSON preview, where a fresh render puts the `▦ N×M` badge | the badge (`old_value_badge`), which the in-DOM branch beside it already used |
| R14 | a `calc-here` answer focused the live calculator window but the page never recorded that a window exists, so Alt+C / the Calculator button opened a SECOND calculator beside it | `_extAlive`, kept true by the window's own announcements (`calc-here` / `calc-bye`), asked once per page load with a focus-free `calc-probe`; a press that finds no answer heals the flag and opens in-page after all |

### Not a defect

The fifteenth finding: `_wholesale_unit`'s `_src` classifies a path as wiring
by its top-level key, which mislabels a chip whose wiring.json carries a
top-level `qubits`. True — but that IS the app's one rule
(`QuamStore.source_file_for`), the same one the modifier stamps on every
change-log entry, so a wholesale entry and an edit entry can never disagree.
The real problem was a second copy of it; the copy is gone and the store's
own method is called. Changing the rule itself would be a separate change,
app-wide.

Pinned in `tests/test_undo_live.py` (`TestReviewRound2`, `TestJournalInsertCursor`),
`tests/test_sweep_misc.py`, `tests/undo_repaint_selfcheck.cjs` and
`tests/calc_window_selfcheck.cjs` (World C drives the real ping flow against a
faked BroadcastChannel bus).

## 5e. The pre-customer review — R-A1 (2026-09-04)

Found in a real browser on a copy of the customer's 20-qubit chip, before the
customer ever saw it. The grid's ⚡ **Apply to live now** does NOT go through
`/state/apply-to-live`; it PULLS the live chip, re-applies the pending edits on
top of the fresh state, and pushes (`doStateSync('apply')`). The re-apply
(`_replay_updates`) wrote every path as its own change-log entry with **no group
id** — so any ONE user gesture that touched several leaves came back split into N
undo units: the coupled `f_01` ↔ `xy.RF_frequency` pair (the grid mirrors an
edit of one onto the other), a multi-cell row, an FSP change bundled with its
compensating amplitudes (docs/126).

Before docs/160 that only cost extra Ctrl+Z presses in the editor. **After
docs/160 each of those units is a separate LIVE WRITE**, so one Ctrl+Z left the
chip holding half a gesture — `f_01` reverted while `RF_frequency` still held the
new value, an FSP change without its amplitudes. A qubit sitting at a frequency
its own drive line no longer matches is exactly the silent, hard-to-see
corruption docs/160 was supposed to make safe.

The replay map now carries the change entry's **group id** alongside each op
(`_tagged`/`_untag`; a single-field edit stays ungrouped, so every 2-tuple caller
and pin is untouched), and `_replay_updates` maps each original group id to one
fresh id — so a gesture that wrote several leaves is re-applied as ONE undoable
unit, not N. It does not over-group: two SEPARATE gestures re-applied together
stay two units (one press = one user action, docs/107).

Two distinct real gestures can never be merged by the remap: for two group ids to
collide on the same fresh id, `mutation_seq` would have to be unchanged between
their first-sightings, which requires every write of the first gesture to have
FAILED on replay — in which case that gesture contributes nothing to the journal,
so there is nothing to merge. A rarer capture whose dict-collapse leaves a group's
paths non-contiguous simply degrades to the pre-R-A1 separate-units behaviour
(never worse).

Verified three independent ways: (1) an A/B mechanism probe — with the fix one
gesture is one unit and one Ctrl+Z restores both leaves, with the fix stashed the
chip holds half a gesture; (2) a mutation-checked pin — reverting `routes.py`
fails `test_one_gesture_stays_one_unit_through_pull_and_reapply` with exactly the
two-unit split; (3) a real headless-Chrome run on the customer chip copy — edit a
coupled `f_01` cell → Apply → Ctrl+Z, and both `f_01` and `RF_frequency` come back
together on the live file (`A4f`). Pinned by
`tests/test_undo_live.py::TestReplayKeepsTheGesture`.

## 5f. The pre-customer review — the live walk (2026-09-04)

Seven live-walk defects, each reproduced against the real routes on a copy of
the customer's chip before the customer saw them, and each fixed with a
mutation-checked pin. Four are gathered here; the three larger ones (the chip
whose Ctrl+Z rewrote ANOTHER chip's live files, the mixed staged+edited apply
that lost the real pre-apply value, and the autofit-diverged working copy that
rode a Ctrl+Z onto the instrument) have their own sections below.

| | finding | fix |
|---|---|---|
| **F-NAN** | a leaf applied as NaN (an autofit/wholesale write — never through the finite-checked edit door) could not be undone live: the walk's drift counter compared with `!=`, and `nan != nan` is True, so every NaN step counted phantom drift and stayed staged with a false "the value had moved" toast — exactly on the NaN-bearing chips the sweep's `_differs` was written for | the two drift checks in `_undo_journal_step` use the NaN-aware `_differs`, not raw `!=` |
| **F-DRIFTBANNER** | a walk step refused because the chip drifted out-of-band told the user to "take the live changes (drift banner)", but the refusal response never escalated `liveDriftChanged` — so the page showed no banner and the tray still said "Synced" until a full re-render | the refusal path escalates `liveDriftChanged`/`stateHistoryChanged` like the success path (the banner re-checks live vs synced, so it appears only when there really is drift) |
| **F-BURST-SKIP** | redoing over a skipped (too-large/empty) unit MOVED the cursor — a step was consumed — but `_live_redo_response` reported `consumed:0 / exhausted`, so a coalesced `?n=k` Ctrl+Shift+Z dropped its remaining k−1 presses (the down side already reported this correctly via `_walk_burst_extra`) | the skip reports `consumed:1 / journal`; only a genuine nothing-to-redo / refusal reports `consumed:0 / exhausted` |
| **F-REDOJAM** | the sweep's §5d re-push of a refused redo frame was correct for a TRANSIENT refusal (the chip moved) but JAMMED on a PERMANENT one: with the setting turned OFF, every Ctrl+Shift+Z met the same OFF refusal, the `jrn_live` frame was re-pushed forever, and an ordinary in-memory frame BENEATH it was unreachable (the only escapes were turning the setting back ON — which writes the chip, the very thing the user turned off — or a new edit, which discards the stack) | an OFF refusal DROPS the `jrn_live` frame instead of re-pushing it: a live redo is unavailable in OFF mode by covenant, and dropping it keeps the redo ORDER correct (the frame beneath becomes the legitimate next redo) and unjams the stack. A transient refusal still keeps its frame and retries THIS step |

Pinned by `tests/test_undo_live.py::TestFinalReviewWalk` (F-NAN / F-DRIFTBANNER /
F-BURST-SKIP), `::TestForeignWindow::test_an_off_mode_redo_drops_the_live_frame_and_reaches_beneath`
and `::test_a_transient_redo_refusal_keeps_its_frame_and_unblocks` (F-REDOJAM,
which replaces the two docs/160 §5d pins that had pinned the OFF re-push). Every
pin was mutation-checked: reverting the `routes.py` change fails it with the
exact defect.
