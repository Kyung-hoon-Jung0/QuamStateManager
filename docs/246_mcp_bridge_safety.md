# docs/246 -- MCP bridge safety (agent campaign A-03..A-07, D-11)

## Report

The 2026-10-03 agent validation campaign (rig `D:\work\sm_qa_rigs\agent\rA`,
findings in `FINDINGS.md`) drove the MCP bridge as a real client. Five P0
defects let an agent's press do something other than what the agent saw:

- **A-03** `state_edit(fsp_ack="comp")` changed the port power with no
  amplitude compensation.
- **A-04** `undo` on an empty tray walked the cross-save journal and rewrote
  the LIVE chip, reverting a person's applied value.
- **A-05** `undo` removed a person's staged row.
- **A-06** the `SM_CHIP` pin matched the folder name, so a second fridge in a
  folder of the same name passed.
- **A-07** after a chip switch, an unpinned bridge applied the other chip's
  tray because the counts matched.

The rule behind every fix: an agent's press means what the agent saw, on the
chip it saw it on, and touches only rows the agent staged. Where a rule
protects a person's work, the **server** enforces it for any request carrying
`X-SM-Agent`, not only the bridge.

## Root causes (lines at origin/main 8bfbf6bf)

| id | where | cause |
|---|---|---|
| A-03 | `web/routes.py:9772` (`field_edit`) | `fsp_ack` only skipped the 409 offer. Compensation existed only on `/field/edit-batch`, which the popup calls. The bridge (and the history panel's revert, `app.js` `revertTo`) answer the offer on `/field/edit`, so "comp" committed the FSP alone. |
| A-03 | `web/routes.py:12164` (`_field_edit_batch_impl`) | batch rows were never stamped with an actor, so an agent's bundle read as `human`. |
| A-04 | `web/routes.py:21909` (`undo`) + `mcp.py:178` (`t_undo`) | the bridge's `undo` is a person's Ctrl+Z. With an empty tray it runs `_undo_journal_step`, which since docs/160 writes the live chip. |
| A-05 | `web/routes.py:21887` (`undo`) | `modifier.undo_group()` pops whatever group is on top, whoever staged it. |
| A-06 | `mcp.py:46` (`_chip_facts`) | the pin was compared with `name`, the display name, which is the folder name for a folder like `chip`. |
| A-07 | `mcp.py:200-204` (`t_apply_to_live`) | the bridge re-read the chip token and the tray at press time. `/state/apply-to-live` (`routes.py:25231`) ignored `expect_chip` anyway. The only gate left was `seen_changes`, a count, and two chips with one staged edit each have equal counts. |
| D-11 | `mcp.py:152` (`t_state_edit`) | every 409 was framed as `needs_answer` with an ack hint, including the run lock. |

## Behaviour now

**A-03.** On `/field/edit`, `fsp_ack=comp` with a compensation plan calls
the batch door itself: `_field_edit_batch_impl(_fsp_comp_payload(...))`. The
payload is the FSP row plus `fsp_comp_updates(plan)`, the server twin of
`window._fspCompUpdates`; amplitudes go as text, as the popup sends them, so
they are parsed on the same path. The result is what the popup's comp button
commits: FSP plus every compensated amplitude, one gid, one Ctrl+Z.
`/field/edit-batch` stamps `actor` on its rows, as `/field/edit` always did.
The bridge journals every row of the bundle and returns them as `entries`.
`fsp_ack=solo` is unchanged.

**A-04 / A-05.** An agent's `/undo` touches only its own staged group.
`_agent_undo_refusal` runs under the same lock as the pop:

| top of the tray | agent `/undo` |
|---|---|
| the agent's own group (`by_*` on every row) | undone (one group; `?n=` is ignored) |
| a group with any person's row | 409 `refused: human_group`, paths named |
| empty, or a staged journal step (`jrn:`) | 409 `refused: journal`; the journal is never walked |

`/redo` with `X-SM-Agent` is 409 `refused: redo` (redo can walk the journal
forward onto the live chip). A person's Ctrl+Z and Ctrl+Shift+Z are
unchanged. The bridge returns a refusal as data (`undone: false`, `refused`,
`how`) and points the agent at `undo_mine`. Ownership is "any `by_*` actor",
the same rule as `/api/agent/undo-mine` (agent-vs-agent attribution is A-08).

**A-06.** `/api/agent/chip` also returns `declared_name`
(`extras.chip_name`) and `pin`, the value `SM_CHIP` should hold: the chip_key
(`<parent>-<path hash>`, one per folder). `sm_status` shows both. The pin
matches by identity:

- a pin equal to the chip_key always matches, on that folder only;
- a pin equal to the declared name or the display name matches, then
  **latches** the chip_key it matched. Any other chip is refused for the life
  of the bridge, even one with the same name.

The display name stays accepted because SM's own in-app session writes it
(`chat_api._build_backend`: `SM_CHIP = _chip_name()`). A refusal names
`pinned_chip_key` and `pin_for_open_chip`.

**A-07, both sides.**

- `/api/agent/tray` returns `chip_key`, `chip_token` and `seen_sig` (the
  docs/179 change-set signature) with the entries. A `/load` landing while the
  tray is read is a 409 `chip_switched`, never one chip's rows under another
  chip's key.
- The bridge keeps that picture. `apply_to_live` refuses locally when the open
  chip_key differs from the seen one. Otherwise it sends `seen_changes`,
  `seen_sig`, `expect_chip=<seen token>` and `expect_chip_key=<seen key>`. It
  no longer re-reads the tray for `wrote`. A server `chip_mismatch` comes back
  as a chip switch, not as "a human edited the chip". An `undo_mine` whose new
  picture cannot be read leaves the bridge with no picture (call `tray`).
- `/state/apply-to-live` (`_apply_chip_refusal`) refuses with 409
  `conflict: chip_mismatch` when `expect_chip_key` is not the open chip's
  key, and when `expect_chip` fails the token gate every edit door has. A
  caller that sends neither (the window's own Apply) is unchanged.
- SM's own apply of an agent run's writes (`agent_api._stage_writes`) names
  the chip it staged on (`expect_chip_key`), so a `/load` between its check
  and the door cannot push the other chip's tray.

The key is load-bearing. On the rig, `rA\chip` and `rA\alt\chip` have the
same fingerprint token (`87dbe2808b14fa88`), and with the same path staged on
both, the same count and signature (`eba7cd718969`).

**D-11.** Only `fsp_compensation` and `type_fix` are offers. Any other 409
on `state_edit` (the run lock, a chip switch, a pulse-structure refusal)
comes back as `refused`, with no ack suggested.

## Not done here

- **D-11, the lock order.** `_scheduler_lock_guard` (`routes.py`, a
  `before_request` hook) refuses the agent's own edit while its node runs, so
  the `X-SM-Agent` exemption in `_agent_edit_lock_refusal` never fires.
  Whether an agent may stage while its own node runs depends on how the run's
  writes are diffed (D-02), which `fix/run-node-isolation` is changing.
- **A-11** (a `stale_live` refusal at apply has already saved the tray):
  the apply door's save-before-check order, a separate change.
- The server does not *require* an agent's apply to name its chip; a client
  that declares nothing gets the count/signature gate only.
- The in-app session still pins the display name (`chat_api.py`, owned by
  `fix/agent-permissions`); the latch covers it.

## Verified on the rig

Rig rA, port 5094, server and every bridge on this worktree
(`PYTHONPATH=D:\work\sm-mcpfix`), stream-A helpers (`rA\tools\smh.py`).
Start hashes (SHA-256): `rA\chip\state.json` `b3b7f8f6...`, `wiring.json`
`92316ec9...`, `alt\chip\state.json` `63fdb647...`.

- **A-03:** `state_edit` FSP `con1/1/2` 0 -> 3 dBm returned the offer (10
  amps). With `fsp_ack="comp"` it staged 11 rows, one group `grp0`, all
  `by_claude`. After `apply_to_live`, live `x180_DragCosine.amplitude` went
  0.3518333041 -> 0.2490789045 (= old x 10^(-3/20), exact). Live state
  `b3b7f8f6...` -> `1eea75d3...`.
- **A-05:** agent row, then Kim's row on top. The bridge's `undo` and a raw
  `X-SM-Agent` `/undo` were both refused `human_group` naming
  `qubits.qD2.T1`; the tray and every file hash were unchanged.
- **A-04:** Kim staged and applied `qD3.T1` = 2.66e-05 (live `1eea75d3...`
  -> `09368f7d...`). The bridge's `undo`, a raw agent `/undo` and a raw agent
  `/redo` were refused (`journal`, `journal`, `redo`); live stayed
  `09368f7d...`. Kim's own Ctrl+Z still walked the journal: live back to
  `1eea75d3...`.
- **A-06:** `sm_status` on rA: `name: chip`, `declared_name: arbel`,
  `pin: rA-28e9ddf7`. Pins `rA-28e9ddf7`, `arbel` and `chip` worked;
  `otherfridge` was refused. A bridge pinned `chip` latched `rA-28e9ddf7`;
  after the window opened `alt\chip` (also a folder named `chip`) its
  `state_get` and `state_edit` were refused, naming `alt-5b5972f5` as
  `pin_for_open_chip`; the alt file hash stayed `63fdb647...`. Back on rA the
  same bridge worked again.
- **A-07:** the bridge saw rA's tray (1 row, key `rA-28e9ddf7`, sig
  `eba7cd718969`). The window switched to alt and alt got one staged row on the
  same path: same count, signature and token. The bridge's `apply_to_live` was
  refused `chip_mismatch` before the door; the raw door with rA's picture got
  409 `chip_mismatch`. Both live files were unchanged; alt kept its row.

At the end both chips were restored to the start hashes, SM took live, and
the server was stopped.

Tests (cqt env): the 139 test files that touch the bridge, the agent API,
undo/redo, FSP compensation, the edit and apply doors or chip identity ran
with 4820 passed, 112 skipped, 0 failed.

## Pins

`tests/test_mcp_bridge_safety.py` (23 tests): server rules on a real Flask
app with synthetic chips, bridge rules against the FakeLink of
`tests/test_mcp_bridge.py`. In `tests/test_mcp_bridge.py`: the fake 409 uses
the server's real `type_fix` key, a D-11 pin, and the fixture resets the
bridge's new module state between tests.

Mutation sweep: each fix piece reverted on its own, both files run, then
restored. 26 of 26 went red:

| reverted piece | caught by |
|---|---|
| `/field/edit` comp -> batch | `test_comp_on_field_edit_stages_exactly_what_the_popup_stages` |
| batch actor stamp | same |
| twin sends text | `test_the_rows_go_as_text_like_the_popup` |
| agent undo refusal (whole) | `test_a_persons_staged_row_is_refused_and_kept`, `test_an_empty_tray_never_walks_the_journal_onto_the_live_chip`, `test_a_staged_journal_step_on_top_is_refused` |
| human-row / empty-log / `jrn:` check (each) | the one matching test above |
| one group per agent press | `test_a_burst_is_one_press_for_an_agent` |
| agent redo refusal | `test_redo_is_a_persons_key` |
| door: key gate / token gate / call | `test_an_apply_seen_on_another_chip_is_refused`, `test_a_token_from_another_chip_is_refused_without_a_key`, `test_the_internal_door_names_the_chip_it_staged_on` |
| chip `declared_name`, tray `chip_key` | `test_chip_and_tray_say_who_the_chip_is` |
| tray mid-read switch | `test_a_tray_read_across_a_chip_switch_is_refused` |
| internal door names its chip | `test_the_internal_door_names_the_chip_it_staged_on` |
| pin latch / pin by identity / `pin_for_open_chip` | `test_a_folder_name_pin_latches_the_first_chip`, `test_the_display_name_pin_sm_writes_itself_still_works_and_latches`, `test_the_declared_name_and_the_chip_key_both_match` |
| bridge pre-check / seen token | `test_a_chip_switch_after_looking_is_refused_before_the_door`, `test_the_press_carries_the_seen_chip_not_a_fresh_read` |
| D-11 framing | `test_a_409_that_is_not_an_offer_is_a_refusal_not_a_question` |
| chip refusal wording / blind `undo_mine` count | `test_a_server_chip_refusal_is_said_as_a_chip_switch`, `test_undo_mine_without_a_new_picture_must_look_again` |
| bridge undo refusal as data / bundle journaling | `test_a_server_refusal_is_returned_as_data`, `test_a_comp_answer_returns_and_journals_every_row_of_its_group` |

## Follow-up at integration: the in-app session pins the key too

Fix F and this fix were merged together, so SM's own in-app session (`chat_api._build_backend`) now pins `SM_CHIP` to the open chip's KEY. Before, it pinned the display name, which latched on first match. The key also names the session's MCP config file (`agent_mcp/<key>-<cli>.json`), so two folders that share a display name never share one.

**Pins:** `TestAsk` (2 tests) assert `SM_CHIP == chip_key != display name`; the mutation back to the name turns both red.

## D-11, settled at integration: the agent waits; the run's own writes land

D-11 read the `X-SM-Agent` exemption in `_agent_edit_lock_refusal` as dead code, because `_scheduler_lock_guard` (before_request) refuses every mutator while the agent's node runs. It is not dead.
- `agent_api` stages and applies the RUN's writes by calling these routes inside a `test_request_context`, which does not run before_request hooks. That is where the exemption acts: removing it turns the `TestRun` auto-apply and attribution tests red (measured).
- An agent's own HTTP `state_edit` during its run is refused by the guard, so the agent waits too. This is pinned explicitly ("the agent waits too").

A Codex attempt let agent requests through the guard. It then hit `scheduler_running`, because `run_node` drives the scheduler chassis, and it broke that pin; it was discarded. A coordinator "cleanup" that removed the exemption broke the run's own writes; it was reverted.

**Outcome:** behaviour unchanged, and the docstring now explains both paths.

