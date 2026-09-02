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
| `tests/test_undo_live.py` | 30: the walk down/up, persisted cursor, truncation, OFF mode, pending edits, saved-but-unapplied content, a staged snapshot, drift, a chip that moved (rolled back, never clobbered; redo refused too; the walk resumes after taking the live changes), archive, restart (journal-forward fallback), the flush outside `store._lock`, stage→apply unit, dataset Apply-to-chip unit, restore-live unit, the unit's `old` = the chip's value, structural subtree/list round trip, too-large, foreign live owner (both directions), dead owner, the applied-log ✕ keeping the cursor, live-redo triggers, a redo frame bound to its unit, sidecar re-read |
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
