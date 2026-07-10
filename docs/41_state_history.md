# 41 — State History (full-chip snapshots + restore)

A left-sidebar entry (**State History**, below Bulk Edit) that turns the
existing per-chip snapshot store into a review-and-roll-back surface: every
captured `state.json` + `wiring.json` over time, framed by the experiment that
produced each, with diff / compare and two restore modes. It is a *view +
restore layer* over `HistoryManager` — it adds no new capture path and shares
the same snapshot store that powers Param History (no double-write of data).

## Architecture

```
core/history.py            HistoryManager — full state+wiring snapshots at
                           instance/history/<chip>/<ts>/, content-hash dedup,
                           SnapshotMeta (now incl. label + pinned), fingerprint
                           routing, align(), _prune (pinned-exempt)
web/routes.py              /state-history (list, experiment-attribution view),
                           /state-history/snapshot (Take snapshot),
                           /state-history/<ts>/stage      (Mode 1)
                           /state-history/<ts>/restore-live (Mode 2)
                           /state-history/<ts>/label      (label + pin)
                           reuses /api/history/<ts>/diff + /api/history/compare
web/templates/_state_history.html   timeline + restore/pin/compare controls
web/templates/_sh_confirm.html      409 gate warning + force-retry button
web/static/app.js          StateHistory module (compare-2-selected) +
                           the stateRestored consumer (closes a stale inspector)
```

## Two restore modes (both route through the single live-writer)

- **Mode 1 — stage (safe, default).** Load the snapshot into the *working copy*
  (`safe_io.write_state_wiring(wc.working_folder, …)` under the captured ctx's
  build lock), then `_rebuild_after_working_copy_replaced(ctx)`. The user
  reviews the diff and applies through the normal **Apply to live** flow. Never
  writes the live chip directly. Confirms over a dirty working copy.

- **Mode 2 — restore-live (gated).** Replace the live chip in one step:
  `write_state_wiring(working_folder) → working_copy.apply_to_live(wc, force) →
  _rebuild_after_working_copy_replaced(ctx)`, all under the **single build
  lock** (`_active_wc_lock(ctx)`) — never a second live writer.

Both rebuild every derived cache (store / search / engine / pulse_index /
generated_config) through the one shared `_rebuild_after_working_copy_replaced`
entrypoint, so no menu is left showing stale content (the stale-chip bug class
this helper exists to prevent).

## Safety gates (all S1)

restore-live refuses, or requires explicit confirmation, on each of:

1. **origin ≠ live** — a chip opened from a dataset run archive is read-only
   (`_archive_write_blocked()` → 409); restore-live is blocked, and the UI
   suppresses the Apply/restore affordances.
2. **unsaved working-copy edits** — would be discarded; warns (409) with a
   `force_pending=1` confirm button.
3. **wiring-topology mismatch** — `align(fingerprint_of(snap), fingerprint_of(
   live))`; a non-aligned snapshot (a different chip's topology routed into the
   same `<chip>` dir) warns (409) with a `force_align=1` confirm.

These are **independent tokens**: forcing past the unsaved-edits gate does *not*
silently skip the topology gate, so the wiring-mismatch warning is always shown
before live wiring is overwritten. A bare `force=1` is a master override (tests
/ scripted use). The current live is snapshotted **before** the overwrite (and
the restore aborts if that pre-snapshot fails), so a restore is itself
reversible.

## Captured-ctx discipline

Every mutator binds its build lock and snapshot source to the **captured**
context's folder (`_active_wc_lock(ctx)`, `path = ctx["path"]`), not the
live-active context — so a concurrent `/load` flipping the active context
mid-request can't hand a write the wrong folder's lock or cross-wire snapshot
source vs. target. Applied across all 8 mutation sites (save / sync /
pull-apply / apply-to-live / stage / restore-live).

## Cross-menu integration

- **Events.** stage and restore-live emit `HX-Trigger: pulses-changed,
  stateRestored`. `pulses-changed` refreshes an open Pulses table;
  `stateRestored` closes a stale qubit/pair/pulse inspector left open on another
  menu (it also avoids a 404 when the next edit targets a pulse the restored
  snapshot doesn't have). The tray is refreshed OOB on every mutating response.
- **Chip switch.** `/load` and `/workspace/select` return `HX-Redirect` so
  `base.html` re-renders with a fresh chip-identity tray + origin badge — a
  plain redirect would swap only `#table-pane` and leave the topbar showing the
  previous chip (a live-vs-archive trap).
- **Labels / pins.** A pinned snapshot is exempt from pruning (protect a
  known-good baseline). `_prune` only reads per-snapshot meta when actually over
  budget, so the default (effectively unbounded) retention never pays the O(N)
  meta scan under the lock.

## Retention

`DEFAULT_MAX_SNAPSHOTS` is effectively unbounded today; `_prune` deletes the
oldest *unpinned* snapshots only when the count exceeds the budget. Triggers:
`save` / `manual` / `auto` / `experiment` / `restore`.

See also `docs/20_param_history.md` (the shared snapshot store),
`docs/28_conflict_safe_io.md` (working-copy + armored I/O), and the
pre-customer multi-role audit that hardened the gates above.
