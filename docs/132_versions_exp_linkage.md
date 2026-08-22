# 132 — Versions: EXP/MANUAL/BACKUP, run linkage, per-value take, near-real-time ingest

**Date**: 2026-08-22 · **Trigger**: customer feedback round 2 on the docs/128
Versions surfaces (8 items, one session) · **Pinned by**:
`tests/test_history.py` (`TestKindStamping`/`TestRunIngest`/`TestHistorySeq`),
`tests/test_state_versions.py` (`TestKindChips`/`TestExpRow`/
`TestChangesOnlyFilter`/`TestPanelFollowsIngest`/`TestPerValueTake`/
`TestWorkbenchTake`), `tests/test_poll_stability.py`
(`TestExpIngestOnPoll`/`TestSchedulerHookOrder`),
`tests/version_diff_selfcheck.cjs` (38 assertions, mutation-checked), plus the review-round pins: `TestRunIngestConcurrency`, `TestIngestGateHardening`, `TestTakeValueFidelity`, `TestWorkbenchTakeActiveGate`, `TestKindChips` legacy cases.

The customer's core diagnosis, verbatim intent: *"versions를 눌러도 대체 이게
왜, 언제, 누가 변경했는지는 빠져있기 때문에 meaningful information이라고
보기 힘들다"* — the list showed WHEN, never WHO/WHY. Everything below serves
that one sentence.

## The trigger truth, and the three kinds

Ground truth established first (14 `check_and_snapshot` call sites, all
file:line-verified): `save` = post-apply result records; `auto` = TWO fused
meanings (pre-apply backups AND adopted-external content); `manual` = explicit
presses AND internal forced backups; `restore` was **absent from every label
map in the app**; `experiment` fired only from the Param-History backfill.
docs/20's user-facing trigger copy predated half of these.

The user's design, adopted: **three kinds, in the user's vocabulary** —
**EXP** (a run produced this state), **MANUAL** (you changed it on purpose:
apply, pull, restore, take-snapshot, bookmark), **BACKUP** (protective copy
right before a write). Implementation:

- `SnapshotMeta.kind` (`"exp"|"manual"|"backup"`), additive — the stored
  `trigger` is untouched, old builds ignore the field via the
  `_SNAPSHOT_META_FIELDS` filter, old snapshots deserialize with `kind=None`.
- All 14 capture sites stamp it (stamping table in the plan file /
  TestKindStamping). The autofit pre-plan `snapshot(label)` closure was
  found DISCARDING its label — fixed in passing (annotate after capture).
- `kind_for(meta) → (kind, is_legacy)` is the ONE display mapping
  (history.py, also a Jinja global): explicit kind wins; legacy fallback
  `save/manual/restore→manual, experiment→exp, auto→backup`, flagged legacy —
  the chip renders dashed+muted and the tooltip says "recorded before kinds
  existed; this is the likely reading". Certainty the data doesn't hold is
  never claimed. `TRIGGER_LABELS` finally gained `restore`.

## Run linkage: "After #2923"

- EXP rows render a clickable chip **After #<run_id>** between Diff and Pull
  to Live — "after" because qualibrate saves the state copy AFTER fit updates
  land (the user's own correction). Truncated experiment name beneath the id,
  full name in the title, click opens the run's data panel via the house
  pattern (`hx-get /dataset/<uid>` → `#inspector-pane`); uid minted with
  `_uid_for_run_ref`, `GET /dataset/by-run/<id>` as the fallback. **Only
  where a run is recorded — never a time-based guess** (the user's explicit
  choice).

## Near-real-time ingest (what makes the chip exist at all)

No live capture path ever filled `experiment_name`/`run_id`; only the
backfill did. Now, when a NEW run with a `quam_state` copy appears:

- **Detection is enqueue-only.** Three enqueue sites: the
  `/datasets/changes-since` aggregation loop (dict lookups only;
  `DatasetStore.changes_since` byte-untouched, the docs/105 budget pins
  stay green), the scheduler post-node hook, and a **live-write mtime
  EDGE** on `/state/drift` (two `os.stat`; the fallback that catches
  qualibrate runs finishing while no Datasets page is polling — and it
  works on a dirty working copy too, where `live_diverged` deliberately
  never escalates). The edge enqueues a scan REQUEST; the budgeted rescan
  (`fast=True` + deadline) runs on the worker, never a request thread.
- **The worker** (daemon thread; synchronous drain under TESTING) gates each
  candidate with the `_runs_field_series` identity gate verbatim
  (extras-name definitive, else network pre-gate + fingerprint alignment)
  against every OPEN live chip, respects persisted chip decisions
  ("different"/undecided → skip; the backfill banner stays the surface that
  asks), retries mid-write runs boundedly, and calls the new
  `HistoryManager.ingest_run` — a single-entry wrapper over
  `_ingest_entries_into` that stamps `kind="exp"` + run fields, computes a
  REAL `diff_summary` (bulk-backfill zeros mean not-computed), and is
  idempotent across processes (run-derived ts key + SQLite re-read +
  content-hash dedup).
- **The scheduler hook was REORDERED**: rescan + resolve + ingest now run
  BEFORE `_reconcile_cached_quam_ctx(auto_adopt=True)`, so the adopt capture
  hash-dedups into a no-op and the surviving row is the attributed EXP one.
  Experiment kwargs are deliberately NOT threaded through the reconcile (the
  docs/87 identity choke point) — an attribution passed down would be a
  guess; content-hash identity collapses the duplicate only when the claim
  is true.
- **Both orders converge** (the user's "hours-later fit-apply" question):
  run ingested first → the user's identical apply dedups (ONE EXP row, no
  duplicate MANUAL — dedup compares against EVERY recorded hash, the A→B→A
  property). User applied first → the ingest hits the known hash and
  **enriches** the existing snapshot's meta with the run fields (kind stays
  MANUAL — the user did pull it — but the row gains the After-chip).
- Failures are silent-but-logged everywhere: a skipped ingest is recoverable
  (backfill remains the catch-all); a noisy 5s poll is not.

## Panel freshness across processes

`HistoryManager.history_seq_for(path)` (a stat of the chip's history dir;
the identity-ladder resolution behind it is TTL-memoized — see the review
section) rides the every-page `/state/drift` poll as `hist_seq`; the JS turns a
movement into `stateHistoryChanged` on body — the topbar chip already
listened, and the open panel now does too (debounced, ticked rows preserved).
A moved seq also drops the per-process snapshot-list cache, **healing the
pre-existing two-window staleness** (window B never saw window A's captures).

## Changes-only filter + counts honesty

- `/state/versions?changes=only|all`, server-side, **default only** ("유저는
  diff가 없는건 관심없거든"): hide iff capture-time `diff_summary.total == 0`
  ∧ not pinned ∧ kind ≠ exp ∧ not current ∧ no label/note. The count is
  stated ("N unchanged copies hidden — show all"); the browser's choice
  persists (`quam_versions_changes`) and rides every refetch.
- The footer states retention truth: "All N versions are kept — full list in
  State History" (prune fires at 100,000; no chip approaches it).
- `snapshot_ts_for_current_content`'s O(N) hash scan — the named 1,000+
  scaling risk — became an O(1) lookup via a hash→newest-ts map keyed by the
  cached snapshot list OBJECT's identity (same lifetime, no new invalidation
  surface).

## Per-value take (feedback #7)

`StateVersions.take(btn)` — ONE implementation for all three compare
surfaces — stages a single value into the WORKING copy through
`/field/edit-batch` (JSON `updates[] + create`), exactly the sync review's ✓
door; the response's `tray_html` flows through `_swapPendingTray`. It
reaches the live chip only via Apply — or automatically inside an armed
Auto-Sync push session (docs/117; the UI copy says so, review-corrected).
`expect_chip` rides along — unlike the older `reviewAccept` (docs/120).

- **Version-diff overlay**: ✓ per row stages the VERSION's value; `removed`
  rows use `create:true`; `added` (now-only) rows offer an honest dash —
  taking there would mean deleting, which this door cannot do.
- **N-way table**: hover-revealed ✓ per present cell, `create:true`.
- **Diff workbench list**: "Use A"/"Use B" — gated to exactly-one-`working:`-
  side AND chip-state tabs (state/wiring), via `CompareSource.origin`.

The selfcheck caught a real bug the day it was written: `take()`'s error
paths guarded `window.showToast` but CALLED bare `showToast` — the exact
bare-call trap CLAUDE.md's standing harness rule describes. Fixed; the
harness also gained a `history` bridge after the sidebar assertions were
found testing nothing (compare()'s pushState was silently throwing on the
unbridged global inside its try/catch).

## Polish (the smaller five)

- Row ordinals `#1..#N` (newest = #1) + the quick-diff header now says
  **"#2 → #1"** so it is anchored to visible rows (with the filter on,
  hidden rows are content-identical, so #2→#1 still equals the true diff).
- Checkbox vertically centered (`.sv-pick` baseline→center).
- Diff hover = the same inversion trigger as Pull to Live, in primary blue
  (paired-selector specificity, the documented trap).
- Sidebar: `/diff/*` entry routes map onto the `/diff` Compare item;
  `StateVersions.compare` calls `syncSidebarNavActive` after its manual
  pushState (no htmx history event fires there); the active item scrolls
  into view (`block:'nearest'`, sidebar-only).
- Dataset delta poll 15s → **5s** (the guards that make it safe pre-exist:
  server budget + partial/cursor-hold, client abort + backoff, in-flight
  guard, hidden-tab skip); the new-run popup gained ✕ / Esc / the backdrop
  click it already had.

## Heavy review, same day — 27 confirmed findings + 4 from the critic, all fixed

The docs/128-pattern red team (6 lenses, per-finding adversarial refutation,
completeness critic; 35 agents) ran against the implementation commit. One
rejection in 28 — the findings were almost all real. The load-bearing ones:

- **CRITICAL — the double-drain race.** The scheduler hook enqueues (waking
  the worker) then drains on its own thread; with no drain serialization and
  no claim step, both threads ingested the same candidate and the trailer's
  hash-dedup branch `rmtree`'d the LEADER's completed snapshot — leaving the
  hash registered, the SQLite rows orphaned, and re-ingest permanently
  blocked, so the state transition was recorded NOWHERE (executed proof).
  Fix: per-app `drain_lock` + claim-before-process (`popitem` then re-park on
  retry), and `ingest_run` now runs under the manager lock like
  `check_and_snapshot` always has. Pinned by an executed two-thread race
  test.
- **CRITICAL — take staged `""` for every string value.** Flask's `tojson`
  escapes `<>&'` but NOT double quotes (its contract is single-quoted
  attributes — the house pattern everywhere else); all three take surfaces
  used double quotes, so a string value's own JSON quotes terminated the
  attribute and the ✓ silently staged an empty string. Single-quoted now,
  round-trip pinned.
- **CRITICAL — local wall-clock in the UTC timestamp space.** Run folders
  carry local time; captures are stamped UTC; one lexically-sorted namespace
  mixed both, floating a fresh EXP row hours "into the future" on any
  non-UTC machine (panel mis-ordered, times wrong by the offset).
  `_entry_timestamp` + `_run_ts_stamp` now convert LOCAL→UTC together
  (parity kept; re-keyed re-ingests are hash-dedup-safe).
- **The stamping claim was false**: the first 14-site patch script validated
  per-edit but only wrote the file at the end — its assertion failure
  discarded the first seven edits SILENTLY, and every surface those sites
  mint was rendering "recorded before kinds existed" about snapshots created
  seconds ago. All 14 verified stamped now by an audit loop, not a claim.
- **Wrong-chip ingest, three ways, all closed**: the chip-decision layer was
  dead when the loaded chip's path had no `data/` segment (the canonical
  qualibrate layout — mirror the backfill's rule exactly, legacy chip key
  included); the gate matched the WORKING copy but ingest routed by the LIVE
  files' identity (mid-divergence those differ — now routes by
  `resolve_chip_dir_for_content` of the matched content); and the workbench
  take rendered for ANY loaded chip's `working:` ref while the write lands
  on the ACTIVE chip (now gated `take_active_ok`; `expect_chip` structurally
  cannot catch that one — the token names the chip being written).
- **The drift fallback was neither an edge nor budgeted**: it re-ran an
  unbudgeted rescan on the request thread every 10s while divergence
  persisted (5.7ms poll → 535ms measured), and was DEAD whenever the working
  copy held any pending edit (docs/87 keeps `live_diverged` from escalating
  on a dirty ctx — including an edit staged by this feature's own ✓).
  Replaced with the mtime edge described above.
- **Mid-pair-write mint**: state.json lands before wiring.json (`'s' < 'w'`);
  the ingest minted a PERMANENT empty-wiring EXP snapshot the run-derived
  key then protected forever. The gate now requires the pair (bounded retry;
  the final attempt falls back to the live wiring like the backfill), and a
  torn state.json parks for retry instead of dropping the run.
- Also fixed: per-app ingest state (module globals crossed Flask apps — one
  app's parked candidate ingested into another app's history, reproduced);
  the panel refresh collapsing "Show more" and dropping Compare ticks
  beyond page one; the filter hiding the chip's FIRST snapshot as an
  "unchanged copy" (zeros mean nothing-earlier); enrich leaving the SQLite
  rows unattributed (Versions said "After #N" while the 🕘 popover
  disagreed); the covenant copy ("nothing here writes") being false inside
  an armed Auto-Sync push session — reworded honestly on all four spots;
  the create-branch error prescribing "use Pull" where Pull cannot help;
  NaN rows offering a ✓ that could never succeed; the dark-theme After-chip
  hover at 2.6:1 contrast; the N-way ✓ being mouse-only
  (visibility→opacity, focus rules); Esc on the new-run popup also closing
  the modal beneath (same-node capture listeners need
  `stopImmediatePropagation`); the spurious per-ingest WARNING about its
  own half-written snapshot; and `hist_seq`'s cost understatement (the
  identity-ladder resolution is now TTL-memoized and the docstring tells
  the truth).

## Known limits, stated

- EXP coverage needs a signal: the delta poll (Datasets page open), the
  scheduler hook (SM-launched runs), or the live-write mtime edge (outside
  runs while SM is on any page — dirty working copies included). A run
  finishing while NO SM window exists is the backfill's job, as before.
- Legacy `auto` rows stay ambiguous forever — the chip says so rather than
  guessing confidently.
- The live-write edge's scan collects only the newest 5 runs per folder per
  debounce window (a write edge means "just now"; older runs are the
  backfill's), under a 2s budget.
- `ingest_run`'s diff_summary is computed against the chronologically
  previous snapshot; a backdated import with nothing earlier keeps honest
  zeros.
