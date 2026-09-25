# docs/209 — Twenty correctness fixes, then one status control

2026-09-25, branch `fix/qa6-sync` (20 commits from `6f44dfc`), merged
`01922f6`. Two things happened on this branch: a correctness sweep over the
sync/undo/history surfaces the QA campaign (docs/205) found broken, and —
mid-branch, on the user's explicit decision — a redesign of the sync UI
itself from ten inconsistent surfaces down to one.

## 1. Twenty correctness fixes

Grouped by what they touch (each is its own commit on `qa/integration`,
`71a24ea`..`439e65c`):

- **Safe writes / backups**: `71a24ea` (a failed state replace no longer
  leaves `wiring.json.<pid>.tmp` behind), `2f74b68` (Keep mine refuses to
  overwrite a live chip it could not back up first), `f595d45` (a refused
  Apply records no BACKUP version and keeps its conflict in ctx).
- **Revert last apply**: `14d54d7` (finds the pre-apply backup after an
  apply changed the chip's identity — blocker), `a140690` (a restore-to-live
  re-anchors it on its own backup).
- **Keep mine / Take live correctness**: `882db55` (held to the live values
  its confirm named; a write landing meanwhile is asked about), `2a52695`
  (a pull never adopts a pair caught between QUAlibrate's state.json and
  wiring.json writes), `175d082` ("Revert this session" names the live-chip
  values it also rolls back and never promises a review it cannot give).
- **Undo/redo**: `8d59335` (a drifted Ctrl+Z says it once, with the delta
  from the value on screen), `d62ce3f` (Keep mine is journaled, so Ctrl+Z
  right after it undoes it).
- **Cross-window**: `f000ea2` (Take live asks before discarding another
  window's unseen edit), `493472b` (a replayed edit never follows a link an
  outside writer added after it), `4f4cfea` (the Json Tree View follows
  another window's edit and apply in place), `4b06986` (the tray ↶ names
  the live journal step it walks, and the other window is told).
- **Misc**: `9950cc2` (the F5 backup deferral never turns a missing live
  pair into a bare 500).

## 2. The redesign: one status control + one sync panel (`ef07a90`)

User decision, 2026-09-25 (full context: memory
`sync-ux-decisions-2026-09-25.md`). The sync UI said the same thing in up to
ten places, differently per page, window and F5 reload. Per the decision it
now says it once:

- **ONE status control**, a one-row (48 px) top bar, rendered from one
  verdict (`core/sync_status.py`): In sync / N unapplied edits / Staged
  version / Live chip changed / N unapplied · live changed / N fields
  changed on both sides / Apply wrote nothing / Can't read the live chip /
  Archive. Its one attached action never loses anything (↑ Apply, ↓ Take
  live with no edits, ⇄ Pull & apply with no overlap, Resolve…, Retry).
- **ONE sync panel** (popover, never a timer): differences grouped as
  changed on both sides (per-field Keep mine / Use live — ⇄ Pull & apply
  stays disabled until every field is picked, never a silent mine-wins),
  live chip changed, your unapplied edits (✕, each naming what it loses);
  History holds Revert last apply, the applied log and ↶ Bring back. Trio
  wording kept verbatim (docs/97); Keep mine is last, error-tinted, never
  the attached action (docs/86).
- The drift banner and Chip Status' own "changed on disk" banner are gone;
  cells stale against the live chip show a blue rule + "live now …".
- Take live snapshots first (a History version + replayable edits; "↶ Bring
  back" restores them as unapplied edits) — decision 4.
- Auto-Sync's first-arm default: take live changes, keep edits on top;
  replace and push start unticked — decision 3.
- Folder/project badges move to the sidebar; multi-instance becomes a "⧉ N"
  marker; success toasts become a 4 s "✓ …" on the control; the workbench no
  longer auto-opens the review.

The same commit folds in six QA signalling fixes (SE-01/02/05/06/07/08,
SU-03/04/06, SYNCEXP-04/09) that the redesign's new surfaces made newly
relevant. Pins: `tests/test_sync_one_control.py` +
`tests/sync_control_selfcheck.cjs`, 21/21 mutations red.

**Contracts kept** (per the decision record): a live write still needs one
explicit act or an armed session (docs/117/160); SM never swaps what you
are looking at (docs/87/144); the direction trio ↓ Take live / ↑ Keep mine
— overwrite live / ⇄ Pull & apply (merge) (docs/97); pull and push are not
symmetric (docs/86).

## 3. Found by review, fixed same day

- `c4df677` — pinned Revert last apply's entry into the sync panel's
  History (reviewer P1).
- `00b8141` — Keep mine now declares the SET its own screen showed;
  `force=1` alone is not consent to another window's edits (docs/120/179).
- `439e65c` — the sync panel's diff table never splits a value mid-digit or
  a path mid-word (reviewer P1).

## 4. Follow-ups after the merge

- `75eac5d` — the merge dropped the drift poll's call into a banner the
  redesign retired (`onDivergedBanner`, `986fd19`), leaving a dead hook
  and a pin still asserting a fetch to the retired
  `/state/diverged-banner`. Re-scoped to the real poll: an outside write
  re-renders the status control, is not re-fetched once shown, lowers when
  the verdict returns clean, waits during an in-flight apply. 4/4 mutations
  on dropping the new hook, 1/1 on restoring the old one.
- `cc71779` — QA liveedit-r2-16: an FSP compensation bundle's ✕ moved from
  the tray into the panel's "Your unapplied edits" group but lost its
  bundle wording — the server still discarded all four changes (FSP + its
  compensated amplitudes) on one press, but the button read "Discard this
  edit". The template now uses the `fsp_bundle_gids` the route already
  passed. 2/2 mutations.
- `18b0483` — QA F13: the redesign kept the archive's "← Back to \<chip\>"
  button but dropped its else-branch, so an archive opened with NO chip
  before said nothing about how to edit again. Both the top bar and the
  panel now restate the way forward.
- `5a5cd06` — three pins still read the unapplied-edit list from the old
  tray location or the Pulses page; re-scoped to the panel's
  `sp-group-mine` rows (same guarantees). 3/3 mutations on dropping
  `data-path` from those rows.

## 5. Open questions (not settled by this branch)

- The Auto pill is not merged into the status control/panel — it still
  renders as its own element.
- "Apply to live now" is kept as a separate action, not folded into the one
  control's attached action.
- There is no "being written…" busy state on the control while a live
  write is in flight.
- Live write latency is still 5–9 s.
