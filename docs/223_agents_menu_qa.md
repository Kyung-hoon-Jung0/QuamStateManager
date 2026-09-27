# docs/223 — Agents menu QA round: a refused approve takes its save back, and the toast lift is a body class, not body:has()

2026-09-26/27, branch `w7/agentsqa` (19 commits), merged `e33e1d0` into
`integ/w7`; follow-ups on `integ/w7`: `7ff9082` (comment wording) and the
setup-Test half of `8656f2b`. Two independent verification rounds.

Scope: `/agent` (strip, doors, approval and plan cards, composer), the floating
agent popover, `/agent/setup` (every section) and `/journal` (Calibration log).
Real headless Chrome over CDP at 1366 and 1600 px, on a rig copy of a 5-qubit
customer chip plus a synthetic 30-qubit chip inflated from it (verification
round 2: big30x plus the 5-qubit copy). Pending approvals were seeded with
`seed_approvals.py` (60 writes on 30 qubits, plus one to reject).

## 1. What was measured

| | before | after | measured by |
|---|---|---|---|
| toast vs Send on /agent, 1366x900 | toast 831-870 px over Send 857-885 px | toast 721-760 px, composer textarea top 807 px | implementer; verifier 1: 5/5 runs identical |
| Calibration log fresh rows broken (dot as 8th grid cell) | 7/7 | 0/7 | implementer; verifier 1: 8/8 good, 8/8 broken with the rule deleted |
| refused approve, then Reject: unapplied values left in the working copy | 60 | 0 (6/6 runs) | verifier 1 / verifier 2 |
| Hangul name in the actor box, mouse click on Send: submit calls | 0 on `2413e73` (1 on integ/w7) | 1, formValid true, at 1366 and 1600 | verifier 1 / verifier 2 |
| open /agent after a chip switch in another window | old chip's heading after about 45 s, old approval card for 12 s or more | matched a cold reload 10/10; median 321 ms to the 5Q copy, 2460 ms to big30x | verifier 1 / verifier 2 |
| big30x /bulk, one insert + layout (`mutcost.cjs`) | with `body:has()`: 118-166 ms (verifier 2); 134.7-197.5 ms (implementer, `72f5b08`) | `ab9cc70`: 7.4-17.3 ms vs base 7.0-15.3 ms | implementer, 3 rigs sharing the CPU |
| big30x Live Edit per-key typing, keydown to painted frame | with the rule 277-278 ms (verifier 2); 364-520 ms (implementer, pre) | without the rule 149-150 ms (verifier 2); `ab9cc70` 188 / 216 / 211 ms vs base 204 / 335 / 190 ms (implementer) | as named |

The verifier's "0.1 ms without the rule" became 7-15 ms in the implementer's
re-measure because three rigs shared the CPU; the fix sits at the base level
in both. Before the fix the popover also opened slower (verifier 2 bench float
open 1786 -> 2305 ms, close 183 -> 442 ms, base -> branch); the sources
record no bench re-run after `ab9cc70`.

Approve door, 60 writes on the 30-qubit chip, until "written to the chip":
10.8-13.0 s (implementer); 11616, 9625, 10680, 12142, 12365 ms, median 11.6 s
(verifier 1). In-process profile: 11.8 s cold / 5.2 s warm, mostly
`state_apply_to_live` (history `check_and_snapshot` 6.6 s, `differ.diff`
3.0 s, lint 2.1 s, json writes 1.7 s). Verifier 2, on a heavily loaded
machine, A/B alternating: 1-write approve 45.8 s branch vs 51.7 s base, 60-write
59.3 s vs 58.6 s — no regression; absolute numbers 4-5x the implementer's.

## 2. First round: 14 root-caused fixes

- `e90466d` Back restores a LIVE Agent home, setup page and float; Connect
  never replaces a settings file it could not read. `826978d` `/api/agent/live-diff`
  reports `stale_since` only beside `live_diverged`, like /chip and /state.
- `89285b0` "values that may change" follows the x180 alias to the value the
  chip holds and counts the total, not the 60 listed. `08186c3` a long plan
  title is cut at a word, never inside a target name.
- `cf217bd` a session a person stopped is not "thinking" for the next 15
  minutes (on integ/w7 the same Stop now still said "thinking", verifier 1);
  `c6cc117` nor is SM's own chat session with a dead process.
- `2413e73` the actor box says when a typed name cannot be recorded (non-ASCII
  -> SM records "human"). `5599532` a null coupler field is not a coupler.
- `a53490a`, `6d44311` Calibration log: a loose-lines-only day no longer says
  it has none; the author select keeps room for its arrow; the "new since your
  last visit" dot (`summary::before`) is absolutely positioned instead of
  becoming an 8th cell of the 7-column run grid. `194bcc6` the wiring strip's
  help circle sits on its row.
- `b38afc5`, `82b60c1` the toast sits above the composer. `agent.js` `toast()`
  goes through the app's `window.showToast` (`#status-bar`), so the first lift
  aimed at `.ag-toast` never applied; `#status-bar` moves to bottom 6.5rem only
  while an agent composer is on screen. Lesson: measure the element actually
  on screen.
- `7909beb`, `82b60c1` an approval press in flight says so and hides Reject,
  also after a poll or focusout re-render; Cancel on Reject's note prompt no
  longer rejects.

## 3. Verification round 1 (verdict DEFECTS) and the fixes

- **P0 — a refused approve wrote a REJECTED approval to the chip** (pre-existing
  on e74b8ce). The door saves the approval's values into the working copy, then
  writes the chip; when the live write failed (live `state.json` held open, as
  an experiment reader does) the values stayed with no owner, a Reject left
  them, and the next approve of any approval pushed them live (q1 x180
  amplitude 0.320126 -> 0.323327). 1 of 6 real-Chrome approval runs hit it with
  no planted lock. `b092c6f`: `agent_api._stage_writes` takes the save back
  (`_take_back_saved`: old leaf values written back and saved, the undo-journal
  unit dropped via new `undo_journal.drop_units`, `working_dirty` and the
  re-apply stash restored). If the take-back itself fails, the old "saved in
  SM's working copy" message is kept and says why.
- **P2 — the real reason was hidden as "HTTP 500".** Apply-to-live answers an
  HTML `_status` fragment; `_status_text` now reads it, so the card says
  "Apply to live failed: ... [WinError 32] ... -- nothing was written; the
  approval's values were taken back out of SM" (`b092c6f`).
- **P1 — regression from `2413e73`.** `setCustomValidity` on `.ag-actor` made
  the whole composer form invalid, so a mouse click on Send did nothing (Enter
  still sent). `6347a17`: an inline note (role=status) plus `ag-actor-bad` and
  aria-invalid.
- **P3 — chip switch in another window.** `1c00077`: the server bumps
  `agent_seq` when the active chip's PATH changes (`_set_active_context`; two
  chips can share a registry name), and `absorb()` starts the feed over when
  `chip_key` changes, dropping a reply fetched with the old cursor. While
  measuring it the implementer found a shared LiveWake defect: a "saturated"
  answer carried the server's current tick/`agent_seq` and the client adopted
  them, losing any change made while the wait was refused (1 of 3 switches
  never followed within 20 s). `72f5b08`: a saturated answer leaves the cursors
  alone.
- **P3 — the approval's "now" column showed the proposal's `old`.** `1c00077`:
  the feed sends each write's `now`, read through the pointer alias on every
  poll; a leaf that moved is flagged "proposed from <old>".
- **P4 — the "plan card ready" toast covered the new card's Start.** `72f5b08`
  removes that success toast (errors still toast).

Verifier 2 confirmed all six fixed in real Chrome and on a real server. A
background tab followed a switch only once brought to the front (3 of 7 had not
after 16 s, then 10-1083 ms): LiveWake pauses while hidden, by design.

## 4. Verification round 2 (verdict FAIL): the body:has() regression

**P1 — `82b60c1`'s lift was `body:has(#table-pane > .agent-home)
#status-bar, body:has(#agent-popover:not(.agent-hidden)) #status-bar`.** A
`:has()` on body makes Chrome re-match against the whole subtree on every DOM
mutation anywhere: on big30x /bulk (~247,000 elements) it doubled Live Edit
typing latency (§1). `ab9cc70`: one rule `body.ag-composer-on #status-bar {
bottom: 6.5rem; }`; `agent.js` `syncComposerClass()` sets the class only while
`#table-pane > .agent-home` exists or `#agent-popover` is not `.agent-hidden`,
called from `init()` (DOMContentLoaded, htmx:afterSwap, htmx:historyRestore,
plus once a frame later), from a `finally` around `toggleFloat`, and from an
attribute-only MutationObserver on the popover's class. No body/html/:root
`:has()` remains in any .css. Toast positions unchanged (implementer: 721-760
px vs Send 857-885 at 1366, 713-753 vs 856-885 at 1600; bottom back to 20-21 px
off the agent surfaces; class off after an htmx nav to /qubits, on again after
Back).

## 5. Integration

- `7ff9082`: two comments (coupler detection in `core/agent_setup.py`, a
  plan-card docstring in `web/agent_api.py`) named a customer's chip;
  `test_knowledge_pack.py::TestNoCustomerNamesShipped::test_shipped_code_carries_no_lab_name`
  was red on the branch alone and in the integ full suite. Reworded, no
  behaviour change; the branch owner should carry the same two lines.
- `routes.py` conflict: coldopen's prewarm helpers and agentsqa's
  `_set_active_context` kept side by side. `POST /api/agent/setup/test` (polls
  the agent process up to 90 s) joined `activity.LONG_POLL_PATHS` in
  `8656f2b`, pinned by `test_newrun_path.py::test_the_held_routes_are_one_list`
  (exemption dropped -> red). See docs/224.

## 6. Pins

- `tests/test_agent_runs.py` — `test_a_failed_approve_leaves_nothing_behind_for_a_reject_to_miss`
  (forced PermissionError): no take-back, stash not restored, `working_dirty`
  not restored, journal unit not dropped, `_status_text` removed — each red
  (no take-back fails on 6253000000.0 != 6250000000.0, the P0 itself).
  `test_a_chip_switch_moves_the_agent_clock`, `test_an_approval_card_says_what_sm_holds_now`,
  `test_live_diff_and_stale_since` — each red under its mutation.
- `tests/test_agent_may_change.py` — `test_the_count_is_the_total_not_the_capped_rows`,
  `test_rows_follow_the_alias_to_the_value_the_chip_holds`: red under
  verifier 1's mutations. `tests/test_agent_pill.py::TestAStoppedSessionIsNotThinking`: red.
- `tests/test_journal_page.py::test_the_new_since_last_visit_dot_is_not_a_grid_cell`
  + journey `agent_journal_layout.cjs`: rule deleted -> both red.
- `tests/test_agent_composer_class.py::test_no_body_or_html_level_has` —
  body:has rule re-added -> red. `tests/agent_composer_class_selfcheck.cjs`
  (10): sync in init dropped 1 red, popover observer dropped 1 red, `finally`
  dropped 2 red, home selector widened 1 red.
  `test_agent_history_restore.py::test_the_app_toast_sits_above_an_agent_composer`
  now matches the class rule.
- Selfchecks: `agent_actor_box` (8; setCustomValidity re-added -> red),
  `agent_approval_busy` (15; four guards, 1/3/1/1 fails), `agent_chip_switch`
  (11; chip-key reset 3 red, heading refresh 1 red, "now" from `old` 1 red),
  `live_wake` (saturated cursors adopted -> 2 red), plus `agent_history_restore`
  (13), `agent_pill` (27), `agent_plan_count` (6), `agent_setup` (38),
  `journal_page` (11).
- Journeys (`tests/browser/journeys/`): `agent_approval_locked`,
  `agent_chip_switch`, `agent_plan_toast` (new), `agent_controls` (clicks Send
  with a Hangul name), `agent_approval`, `agent_back`, `agent_enum`,
  `agent_press`, `agent_plan`, `agent_setup_back`, `agent_setup_ctx`,
  `agent_setup_walk`, `agent_journal_layout`.
- cqt: 21 touched files, 456 passed, 0 failed (implementer 184.8 s; verifier 2
  215.7 s); after `ab9cc70`, 8 files 667 passed, 19 skipped. The full suite was
  not run on the branch; the integ round-2 full suite (at `8656f2b`) found the
  lab-name pin above. The first 11 commits' pins were mutation-checked per
  their commit messages, not re-run in the resume.

## 7. Open / not done

- Approving 60 writes on the 30-qubit chip still takes about 11 s (11.4 s in
  the fix round): the time is in the shared apply core, not the agent code.
- The tray's undo button shows after an approve (and after a refused press) in
  the open page but not after a reload.
- During a refused approve's retry window (~20 s on verifier 2's loaded
  machine) the top bar shows 60 unapplied edits; an Apply press there would race
  the take-back (by design, not new).
- The LiveWake fix is shared infrastructure; only the agent pages were walked
  in a browser.
- Product question: the journal credits approved writes to the proposer
  (`by_claude`), not the person who pressed Write to chip.
- Not pressed on purpose: setup's Connect claude/codex and the hook/allow-rule
  writes target the real `~/.claude.json`, `~/.claude/settings.json` and
  `~/.codex/config.toml`; only preview was exercised.
- A lifted toast can briefly cover a scrollable card's action button in the
  popover (judged acceptable; clicks pass through). `lateSync` (the one-frame
  re-check) is not pinned separately.
