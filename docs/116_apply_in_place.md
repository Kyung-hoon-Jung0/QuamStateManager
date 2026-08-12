# 116 — A gate must refuse on the question it exists for (2026-08-12)

User report, verbatim in spirit: *"dataset에서 state를 불러오면 그거 live에 저장이
되는데, 저장하고 live가 변경되었으니 SM이 live를 SM에 반영할거냐고 되묻는 상황이
연출되고 있어. 이거 명백한 버그라고 생각해. 애초에 한번 버튼 누르면 live에도 SM에도
그냥 곧바로 적용되야 정상."*

They are right, and the shape of the bug is worth recording because it is a
class, not an incident: **a gate that answers a different question than the one
it refuses on behalf of.**

## Reproduction (real chip, copied to scratch — the customer's live files were
never touched)

Load a chip, register a data folder, open a run's State tab, press **⚡ Apply to
chip**. With the live chip untouched since SM synced, one press writes live and
says so — that half was already correct (measured: meta hash == live hash ==
working hash, `live_changed()` False, no banner, drift 0).

Now the ordinary lab situation: something wrote the live chip after SM loaded it
(a qualibrate node, another window, the very run being applied). One press then
produced:

> "Run #174's state is staged, but the live chip changed since it was loaded —
> resolve in the top bar (your staged state is safe)."
> ⚠ *The live chip changed since you loaded it* — "An experiment program updated
> the live state after you loaded it. Your working state holds loaded/saved
> content — **choose which side wins**" → [↑ Keep mine — overwrite live]
> [↓ Take live — discard mine]

Nothing reached live. One intended press became: read a warning · go find the
top bar · pick from choices that describe a different flow · answer a native
confirm dialog.

## Three layers, all confirmed in code

### 1. The gate asked the wrong question (the root cause)

`working_copy.apply_to_live`'s staleness check compares the live folder against
**the working copy's sync point** (`wc.synced_state_mtime` / `synced_live_hash`)
— never against the content it is about to write. So it fires whenever live
moved away from *us*, including when live already holds **byte-for-byte what we
are about to write**. Reproduced: live and the staged payload both `0.079`, the
write is a provable no-op, and SM still refused and asked the user to resolve a
conflict between a value and itself.

That is not an edge case for docs/108's button: **the run whose snapshot you are
applying is usually the very program that last wrote the chip.**

`reconcile_with_live` has had an identical-content adopt since docs/28
(`if working_hash == cur_live_hash: … adopt`). `apply_to_live` never grew its
twin. It has one now: before raising `StaleLiveError`, if the live content hash
equals the content we would write, advance the sync point (meta first, exactly
like the write path — a failed meta write must never leave memory ahead of disk)
and return. Nothing is written because nothing would change.

The carve-out is identical-content ONLY. A live chip holding genuinely different
values is still never clobbered — pinned both ways.

### 2. The answer arrived in the wrong place, phrased for a different flow

The verdict was a one-line warning pointing at the **top bar**, and the tray it
pointed at asks *"choose which side wins"* — whose ↓ option discards the run the
user had just chosen. That is the vocabulary of *my edits vs live*; this flow is
*push the snapshot I explicitly selected*.

New `templates/_ds_apply_conflict.html` renders in `#ds-load-state-result` —
where the button was — and offers the choice the press actually meant:

- **⚡ Apply run #N over live** (error-tinted, un-primary — docs/86's visual
  language for a push that replaces someone else's write; posts the existing
  `/state/apply-to-live?force=1`)
- *Review changes* (the existing review modal)
- *Leave live as it is* (the staged run stays staged)

plus one line naming the reversibility (`↺ Revert last apply`). The conflict
tray still swaps OOB, so the two surfaces cannot disagree.

### 3. One decision cost two answers

`_sh_confirm.html` is itself a confirmation: a panel that names what will be lost
with a button labelled with the act. It also carried `hx-confirm`, so clicking
that button raised `window.confirm` asking the same question again. Removed
(docs/104 #1 — the labelled press IS consent). The `confirm` kwarg is still
accepted and ignored, so its five call sites stay valid.

**Deliberately NOT changed:** the conflict tray's own force button keeps its
`hx-confirm`. docs/86 requires one confirm that names what disappears, and that
button has no prose beside it to do the naming. The new in-place panel does, so
it needs no dialog — the same rule, applied to two different surfaces.

## Also fixed: a drift count could outlive its verdict

`live_drift_count` (docs/87's "N values differ") was written in one place and
cleared in one place, but the boolean it describes was cleared in five. A banner
re-raised by the throttled re-check — which has no count of its own — could
therefore print a count from an EARLIER divergence. The count now dies with the
verdict at all five sites, and the re-check drops it rather than inheriting it
(None ⇒ the banner says nothing, docs/87).

## Pins

`tests/test_dataset_apply_to_chip.py`:
`test_apply_that_changes_nothing_is_not_a_conflict` · `test_a_real_difference_
still_conflicts` · `test_conflict_answers_where_the_press_happened` (in-place
panel, names the run, one continuation, no dialog in the panel — scoped to the
panel, since the OOB tray legitimately keeps its own) ·
`test_gate_panels_ask_once_not_twice`. 141 tests pass across
`test_state_roundtrip` / `test_overwrite_live` / `test_working_copy` /
`test_state_history` / `test_live_replace_routes` / `test_sync_robustness`.

Real-browser verified end-to-end, including the proof that the double-ask is
gone: the force button used to block the browser with a native dialog (it hung
CDP), and now completes in one click straight into the in-place panel.

## The rule worth carrying forward

A gate exists to protect something. Write the check against **that** — "would
this destroy someone else's work?" — not against a proxy that is merely
correlated with it ("did the file move?"). When the proxy and the protected
property disagree, the user is asked to resolve a conflict that does not exist,
and the fastest way to make them stop reading warnings is to show them one that
is not true.
