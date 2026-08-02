# 65 — State roundtrip: staged content vs. the pull-first sync model (SUPER CRITICAL r8)

Date: 2026-07-31 · Branch: `fix/state-roundtrip` (stacked on `fix/tab-focus-calc`)

User report (verbatim intent): ① dataset State tab → **Load State** shows
nothing in Live Edit, and pressing the sync button then "fetches the live
chip's state instead", reverting everything; expected: the loaded state fully
becomes the working state and **Apply to chip** activates. ② Apply buttons
sometimes need **two presses** (e.g. Apply All). ③ **Revert last apply**
appears dead — expected: the revert shows up in Live Edit like user edits with
Apply active. "State back-and-forth는 민감하지만 반드시 성취해야 할 task."

## 1. Root causes (three independent defects)

### 1a. `/state/sync` destroyed staged content — the "it fetched live instead" bug

Every `/state/sync` mode ran **pull-first**: `sync_from_live` overwrites the
working files, then (`apply`) replays the change-log/stash edits on top and
pushes. That model is correct for *edit-shaped* pending work — but content
loaded **wholesale** (State-History stage, dataset **Load State**, **Revert
last apply**) has **no change-log entries and an explicitly cleared stash**.
So `mode=apply` pulled live over the staged files and pushed live back onto
itself: a net no-op that silently discarded the staged state. `discard`/
`reapply` destroyed it silently too.

`/save` was never affected — it stashes the change log (`pending_reapply`)
before clearing, so pull+replay reconstructs saved edits. The presence of that
stash is exactly the discriminator.

The tray itself had the correct branch ("↑ Apply to live chip" hx-posts
`/state/apply-to-live`, a whole-file push) — but the review modal's
**Pull & apply** (shown whenever `unsaved > 0`, including staged+edited mixed
state), the conflict tray, and any stale-tray `doStateSync('apply')` path all
funneled into the destructive pull-first flow.

### 1b. No client bridge from the stage routes to the Live-Edit grid

The stage routes emit `HX-Trigger: pulses-changed, stateRestored,
diagnostics-changed` — and the Live-Edit grid re-pulls **only** on the plain
DOM event `quam:state-changed` (dispatched by `_swapPendingTray`, which an
HX-Trigger header can never produce). Nothing bridged them: after Load State /
stage / revert, `/bulk` kept showing pre-stage values indefinitely. Combined
with 1a this produced the full illusion of "Load State does nothing".

Bonus defect: the `stateRestored` listener closed the inspector
unconditionally — for dataset **Load State** that erased the very pane (and
confirmation message) the user had just clicked in.

### 1c. Two-press Apply buttons

- **Plot-apply popup**: the success branch never closed the popup — it marked
  rows "✓ applied" and stayed open; the only success path that closed was the
  *second* click's nothing-left-to-apply early return. Deterministic
  two-press by construction.
- **Bulk/pair grid "Apply all"**: mousedown → the focused cell's `focusout`
  fired the click-away **row commit** (the `relatedTarget` guard misses
  whenever the browser doesn't focus buttons on mousedown), the commit
  cleared the dirty set and `_refreshGlobal` **disabled the button before
  mouseup** — the browser then never delivers the click. The second press
  found no dirty rows and silently no-oped (the blur-commit had already
  applied one row, which is why it "seemed to work").

## 2. Fixes

### Server (`routes.py /state/sync`)

```python
if ctx.get("working_dirty") and not ctx.get("pending_reapply"):
    if mode == "apply":
        return _sync_pull_apply_to_live(ctx, None, pulled_other_changes=False)
    if request.values.get("force") != "1":
        return jsonify({"status": "needs_confirm", ...})
```

- `apply` + dirty-no-stash ⇒ **delegate straight to the save+push path** (the
  same machinery as `/state/apply-to-live`: persists any change-log edits on
  top of the staged base, pre-apply snapshot, `apply_to_live(force=False)`
  with the staleness gate). Staged base + fresh edits both land.
- `discard`/`reapply` + dirty-no-stash ⇒ `needs_confirm` until `force=1`.
- **The stash carve-out** keeps conflict resolution working: after an apply
  conflict the edits live in `pending_reapply` and the working files are
  expendable — pull+replay is the designed resolution there; short-circuiting
  it would retry the same stale push forever.

**Honest staleness conflict for staged content**: both `StaleLiveError`
returns pass `staged_conflict = working_dirty and not pending_reapply` to
`_state_apply_conflict.html`; when set, the meaningless "Pull & (re-)apply my
edits" buttons (the stash is empty — they'd replay nothing and destroy the
staged content) are replaced by the two honest choices: **Apply my working
state (overwrite live)** and **Pull latest & discard mine** (confirm-gated).

### Client (`app.js`, `bulk-edit.js`, `pair-edit.js`, `all-values.js`)

- `doStateSync(mode, forced)`: a `needs_confirm` response turns into ONE
  `confirm()`; accept re-posts with `force=1` on a macrotask (the in-flight
  guard has been cleared by then). The review modal's "Discard & pull live"
  passes `forced=true` (it already confirms — no double prompt).
- **`stateRestored` bridge**: the listener now also calls
  `_softRefreshLiveSurface()` (the same refresh a sync pull uses) so /bulk,
  /qubits, /pairs, /pulses… re-render the staged values immediately — and it
  no longer closes the inspector when it hosts a dataset detail
  (`#inspector-pane #ds-detail-root`).
- **Plot popup**: `_closePlotPopupIfDone()` — once every row is applied
  (Apply All, or the last per-row Apply) the popup closes and a toast reports
  the count + where the values went (working state → Review to push).
- **Toolbar press stamp**: the Reset-only `pointerdown` stamp generalizes to
  `_toolbarPressTs` covering Reset + Apply all + Apply&sync in BOTH grids —
  the focusout row-commit skips while a toolbar press is in flight, so the
  button can't be disabled between mousedown and mouseup. (Same pattern
  all-values.js already used.)
- `_bulkSelfEdit` set/clear wrapped in try/finally at all 8 sites (a throw
  used to latch it and silently kill cross-surface refresh for the session).
- The tray's "Revert last apply" 409 confirm fragment now renders into
  `#status-bar` (htmx beforeSwap whitelist, narrowed to `/state-history/*`).

## 3. The user workflow, after

1. Dataset → State tab → **Load State** → 200: working copy = the run's
   state, tray flips to "Working state · not applied" + **↑ Apply to live
   chip**, `stateRestored` refreshes Live Edit (staged values visible), the
   dataset pane stays open with the confirmation.
2. Any apply entry point — tray button, review modal, `doStateSync('apply')`
   — **pushes the staged state to live** (never pulls it away).
3. **Revert last apply** behaves identically (it is the same stage machinery).
4. Pull-flavored buttons on a staged copy ask first; forced pull works.
5. Plot popup Apply/Apply All: one press, popup closes, toast confirms.
6. Bulk Apply all: one press commits everything (no lost click).

## 4. Tests

`tests/test_state_roundtrip.py` (12): saved-edits push, staged+unsaved both
land, pure change-log merge unchanged, needs_confirm gates (staged;
discard/reapply; forced pull restores), staged `doStateSync('apply')` pushes
the snapshot, dataset Load State → sync apply lands on live (the exact user
workflow, incl. tray OOB + `stateRestored` header), revert→apply restores
pre-apply live, honest staged conflict tray + confirm-gated pull resolution,
stash carve-out keeps the replay flow, + the node runner for
`tests/state_sync_selfcheck.cjs` (15 jsdom checks over the real shipped JS:
needs_confirm decline/accept+force, stateRestored bridge + dataset-inspector
exemption, popup closes after ONE apply (both paths), toolbar stamp stops the
lost-click race with an expired-stamp control).

Regression: state battery (state_coherence, replay_ops, persistence_staleness,
live_replace, live_drift, dataset_load_state, state_history) 122 green;
web slice (test_web, predelivery_audit_fixes, all_values_route,
column_history) 544 green; ctrlz/tab-focus/bulk-search/all-values selfchecks
green.

## Amendment (2026-08-03, r16 ⑥ — apply-to-live hardening)

Customer report: "apply to live sometimes doesn't reflect on the chip." A
ranked audit of every silent-failure path, and what changed:

| # | path | verdict |
|---|------|---------|
| a | `needs_confirm` declined/suppressed → silent return | **FIXED** — explicit "Cancelled — nothing was changed." toast |
| b | stale tray `data-*` → "Nothing to apply" WITHOUT asking the server | **FIXED** — `applyEditsToLive` re-checks via the new `GET /state/tray` (server truth), re-routes once, only then no-op-toasts (one recheck, no loop) |
| c | no post-apply verification (applied_hash computed from SOURCE bytes) | **FIXED** — `working_copy.apply_to_live` re-reads the live pair after the write and compares content; a mismatch raises an honest `LiveFileError` ("another program wrote during the apply / path redirected — re-sync; your edits are still in the working copy") and the synced state is NOT advanced |
| d | bookkeeping raced a concurrent `/load`: `_set_working_dirty(False)` / post-apply snapshot / cache invalidate hit the ACTIVE ctx, not the captured one → wrong chip's dirty flag cleared, wrong chip snapshotted | **FIXED** — all five `_set_working_dirty` call sites, both post-apply `check_and_snapshot` sites (+ the post-sync one) and the replay `_invalidate_engine_cache` are ctx-pinned |
| e | pull+replay drops an edit (`failed` list) | already surfaced (warning toast + summary); unchanged |
| f | deliberate no-refresh after a clean apply | unchanged (the blink/freeze trade-off stands; replay-failed / pulled-other still refresh) |
| g | meta-write fails AFTER live was written → "Apply failed" but live IS updated | messages already distinct; unexpected exception classes now answer honestly (`except Exception` → "Apply to live failed unexpectedly: <type>: <msg>") instead of a raw 500 htmx drops |
| h | staleness gates (unreadable live ⇒ proceed; diverged-banner suppression) | documented, unchanged this round — the (c) verification now backstops the worst case |
| i | user-error pattern: editing while an experiment loops saves every few seconds | not an SM bug — the (c) verification now REPORTS the race instead of claiming success |

Pinned by `tests/test_working_copy.py` (misdirected-write verification raises
+ synced state frozen; clean-write verify passes),
`tests/test_web.py::TestApplyHardeningR16` (`/state/tray`, ctx-pin spy,
honest unexpected-failure answer) and `tests/apply_ux_selfcheck.cjs` (A1–A3)
+ driver.
