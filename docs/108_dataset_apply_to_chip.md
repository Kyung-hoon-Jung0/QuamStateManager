# 108 — Dataset "Apply to chip": one press from a run's snapshot to the live chip

*2026-08-10. User feedback (매우 중요): reaching the live chip from a run's
state took several clicks — Load State, then the top bar, then Apply. Users
found it painful. Requested: rename the button and make ONE click write the
chip and update SM, "since Revert exists, recovery is possible". The user
delegated the design judgment.*

## Why one click is covenant-compliant

The SM covenant (docs/107) says any direct live write requires **≥ 1 explicit
press of Apply-to-live** — one press, not two. A button labeled **Apply to
chip** IS that explicit apply act; a dialog re-asking what the label already
asked is friction, not safety (docs/104 #1, the no-confirm doctrine). What
makes one click *safe* is reversibility: the push goes through the SHARED
apply core, whose pre-apply snapshot arms **↺ Revert last apply**.

## What changed

`POST /dataset/<uid>/load-state?apply=1` — after the r11 staging succeeds,
the route calls `_sync_pull_apply_to_live(ctx, None)` (the one write path —
same pre-apply snapshot, same bookkeeping, same staleness handling as the ⚡
button) and translates the JSON for the dataset pane:

- **ok** → "Run #N's state is now LIVE on <chip>. Reversible — ↺ Revert last
  apply restores the pre-apply state." + clean tray OOB.
- **conflict** (live moved out-of-band since the chip was loaded) → the
  snapshot stays STAGED, the docs/65 `staged_conflict` tray renders OOB, and
  the live chip is **never force-pushed** — a chip that changed under us gets
  the honest choices, not a clobber.
- **error** → honest message stating the staging DID succeed (retry from the
  top bar).

Both 409 gates are UNCHANGED in substance (chip-identity `force_chip=1`,
pending-edits `force=1` — they answer a different question than consent) but
their confirm URLs carry `apply=1` through, and their messages name the
extra stake ("It will then be APPLIED to the live chip.").

UI (`_dataset_detail.html` State tab): primary **Apply to chip** (`apply=1`),
secondary **Stage only** (the r11 review-first path, byte-identical
server-side), and the read-only archive open unchanged. Tooltips name the
difference and the reversibility.

## Verification

`tests/test_dataset_apply_to_chip.py` (5): one-call live landing + armed
`last_apply` + clean tray; stage-only byte-identical legacy; both gates carry
`apply=1` and name the stake; the conflict case stages-but-never-clobbers
(live keeps the out-of-band value, the conflict tray renders); the template
offers all three actions. Legacy suites green: `test_dataset_load_state.py`,
`test_multifolder_datasets.py`, `test_state_roundtrip.py`,
`state_sync_selfcheck.cjs`.
