# Current sync contracts

These rules describe 1.7.0. They take precedence over older sync descriptions,
including the three-failure merge budget in docs/187.

## Auto-Sync never asks

An armed session carries out its authorized sync operations without a prompt or
modal. Auto-Sync starts on by default; its pull, push and Replace choices still
bound what it may do. A recoverable race produces a notice or a retry, while a
permanent blocker turns the session off with a reason. Agent-staged work and
edits held for explicit review still require a person's Apply.

Pins: `tests/test_auto_sync_on_open.py`, `tests/test_auto_sync.py`,
`tests/test_autosync_merge.py`, `tests/autosync_merge_selfcheck.cjs`.

## Applied-value loss follows the predecessor chain

Within the six-hour protection window, an outside save loses a value the user
applied when it puts a predecessor back on the device. Follow the field's
predecessor chain: after A -> B -> C, a save of either A or B is a loss of C.
A value absent from that chain is a new node value, governed by the replacement
rule below. With Auto-Sync pull armed, restore the lost applied value, retain
unrelated node changes, issue a notice and stay armed. With Auto-Sync off, show
the loss in the sync panel and offer Pull & apply to recover it.

An undo that reached the device counts as an apply of the undone value. Measure
its six hours from the undo, even if the original apply is older. Preserve the
protected undone tail when a later edit truncates the redo branch. Persist the
undo cursor before the live write so another process cannot mistake that undo
for a loss; roll the cursor back if the write is refused. Automatic recovery
preserves an intact redo timeline.

Pins: `tests/test_offline3.py`, `tests/test_autosync_lost_applied.py`,
`tests/test_w2_applied_third_value.py`, `tests/test_w3_undo_lost.py`,
`tests/test_undo_tail_protect.py`, `tests/test_w3_undo_cursor_first.py`,
`tests/test_leftover_minors_walk4_redo.py`.

## Pull plus push collisions keep mine

When pull and push are armed and a node changes a field with a pending edit,
merge with the user's edit winning. Take a backup before overwriting the node
value, show a notice identifying the affected field and overwritten value, and
keep the session armed. The node value remains recoverable in History. A
push-only session still disarms when it discovers live drift.

Pins: `tests/test_autosync_merge.py` (including
`test_automatic_collision_keeps_mine_and_saves_node`),
`tests/autosync_merge_selfcheck.cjs`.

## Replace governs only unapplied edits

A Replace pull may discard unapplied work, with a backup and a notice that says
what was discarded. This includes edits arriving during preparation for that
pull. Replace does not waive protection for applied or live-undone values:
restore a predecessor loss automatically under an armed pull.

Pins: `tests/test_autosync_merge.py`, `tests/test_w5_regression_boundaries.py`,
`tests/test_w3_undo_lost.py`, `tests/test_w3_undo_cursor_first.py`.

## Adopt a new node value and offer Bring back

When a node writes a new value over an applied value, adopt the new value and
show one replacement notice per window open for that event. Use the same
server message in every window; polling must not repeat it, and later windows
or reloads must not replay it. Keep a backup and offer Bring back.

Bring back uses compare-and-swap against the replacement value, checking both
the working copy and current live state. Refuse a stale recovery without
overwriting a later change. If push is armed, Bring back writes the recovered
value live; otherwise it restores unapplied edits. Resolving the notice in one
window resolves it across the other windows too.

Pins: `tests/test_w5_followups.py`, `tests/notice_window_selfcheck.cjs`,
`tests/test_w5_regression_boundaries.py`, `tests/test_w2_applied_third_value.py`.

## Merge failures have three classes

The server classifies automatic merge failures; the client follows that class.
Only automatic merge attempts spend this budget. A held typed cell or a failed
working-copy save is not itself a failed automatic merge.

| Class | Rule |
| --- | --- |
| Transient | File locks, torn reads, live moving during a merge, a press wait and `unseen_changes` retry without disarming. Back off for 2, 5, 15, 30 and 60 seconds, then every 60 seconds. Show retrying and the reason. |
| Permanent | A staged snapshot, read-only or unwritable folder, identity mismatch, archive or foreign owner disarms on the first occurrence and names the blocker. |
| Unknown | Retry with backoff until 10 consecutive unknown failures, then disarm and name the continuing failure. |

A landed merge resets the failure state; rearming starts a fresh retry budget.

Pins: `tests/test_autosync_merge.py`, `tests/autosync_merge_selfcheck.cjs`,
`tests/test_w5_followups.py`.

## Undo waits for a push

Undo or redo waits for an in-flight live push before selecting the newest
action. Track presses by canonical live folder across handles. Read the journal
between quiet checks and reread if a press crossed the read. All retries share
one ten-second deadline; expiry returns a 409 saying the device is still being
written and to press again. Never undo an older action because the newest push
has not finished journaling.

Pins: `tests/test_undo_during_apply.py`.

## W2-1: a window showing Auto-Sync off holds its edits

Every window follows session changes through the drift signature. An edit sent
from a window that still showed Auto-Sync off is staged and held, even if another
window has armed the session. Automatic flush cannot write that edit. Explicit
Apply releases the hold; emptying the pending work clears it. An overlapping
no-op request must not release a hold protecting another request's edit.

Pins: `tests/test_w2_auto_seen_off.py`.

## Diagram add: qubits, pairs and coupler pairs

The Qubits and Pairs diagram offers an append door with Review then stage into
the working copy. Create new subtrees and active-list entries while preserving
existing values; group the addition into one undo step. Reuse the device's
structure and naming scheme, clear copied measurements, and review the new
values. Propose a free readout slot on the feedline and warn about overlap.
Pairs carry the required member operations and aliases. Coupler pairs allocate
a free LF-FEM output; QDAC and bias-tee members are refused. Refuse conflicting
operation destinations, unsupported port pointers and missing or stale device
tokens. Stage is atomic; report remaining staged paths if rollback fails. Live
writes use explicit Apply or an armed Auto-Sync session.

Pins: `tests/test_diagram_add.py`, `tests/test_diagram_add_ui.py`,
`tests/diagram_add_selfcheck.cjs`, `tests/browser/journeys/diagram_add.cjs`.

## QOP version select

Generate and Re-generate Review offer Not set and QOP 3.3 through 3.7 when
MW-FEM is present. Keep the choice in the spec and generated
`extras.qop_version`; source-value carry must not replace a newly chosen
version. Selecting a version preserves the build gates. Show applicable QOP
caveats in Review and retain them in build results for wizard, script and API
users, including when the version is unknown.

Pins: `tests/test_qop_version_field.py`, `tests/test_generate_lo_ports.py`,
`tests/generate_lo_ports_selfcheck.cjs`.

## QUAlibrate proxy 2.0 support

Support both 1.5.x cards/title bars and 2.0 runs-table rows/open-run headers with
exact-match Open in QSM links. Resolve the run folder server-side and open it
in the SM workbench pane, or reuse a named tab outside the workbench. Ambiguous
or unsafe paths are refused. An explicit Through SM choice is remembered;
otherwise use the proxy when its address check succeeds and explain a fallback
to direct access. Offer the workbench address before loading the proxy.

Address precedence is `QUALIBRATE_URL`, the verified saved address, then the
local default. UI changes require an ordinary origin-checked write and a
successful version probe. Keep the proxied page sandboxed with an opaque
origin; authenticated proxy API requests use the per-process token, while only
the bounded static-asset exception permits tokenless asset loads. WebSockets
connect directly to QUAlibrate. Refuse unsafe targets, including link-local
addresses under remote opt-in. Login-enabled upstreams remain unsupported.

Pins: `tests/test_qualibrate_proxy.py`, `tests/qb_proxy_selfcheck.cjs`,
`tests/qb_workbench_selfcheck.cjs`, `tests/test_leftover_minors_workbench.py`.
