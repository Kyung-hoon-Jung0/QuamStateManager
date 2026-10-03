# docs/254 -- an approval is what the person saw (agent campaign D-05, D-15/C-19, C-04, A-13/D-12, A-08, C-08, C-30, A-20 and P3s)

## Report

The 2026-10-03 agent validation campaign (findings in
`D:\work\sm_qa_rigs\agent\FINDINGS.md`) found that the cards a person presses
did not say what the press would allow, and that a press could be spent on
something else:

- **D-05 (P1).** A person allowed an ask-all run of
  `03_resonator_spectroscopy_single` with `{load_data_id: 9}`. The agent used
  that approval for `{num_shots: 100000, frequency_span_in_mhz: 500}`, and SM
  ran it.
- **D-15 / C-19.** Plan step rows and approval cards did not show params at
  all, so the two runs above looked the same on the card.
- **C-04.** After "Allow run", the in-app agent never heard it, and the plan
  sat at RUNNING 0/1.
- **A-13 / D-12 (P2).** `run_node` with `targets=[]` passed every gate,
  including the per-target approval gate. The node then ran on its own debug
  defaults.
- **A-08.** Two agents (Claude and Codex) could apply each other's staged
  rows, and both were told they succeeded.
- **C-08.** `/run <node>` with no target answered 500.
- **P3s:**
  - an unknown node was accepted onto a plan card;
  - a qubit was accepted for a pair node;
  - a `load_data_id` naming another node's run was accepted;
  - reserved params (`simulate`, `qubits`, ...) were dropped silently;
  - cancelling a finished plan wrote "cancelled" into the journal;
  - the approve line named neither the approver nor an edited value (C-30);
  - `node_not_found` listed 40 of 187 names (A-20).

The rule behind every fix: **an approval is a promise about exactly what the
person saw** -- the node, its targets and its params -- **and it is consumed
by that run only. Any difference is a new approval.** Every card says all
three.

## Root causes (lines at base 6e8c731e)

| id | where | cause |
|---|---|---|
| D-05 | `web/agent_api.py:1599` (`run_node`, ask-all branch) | The approval had to match the node and the target list. Params were stored on the record (`approvals.add(params=...)`) but never compared. |
| D-05 (dedupe) | `core/approvals.py:113` (`find_pending_run`) | "The same ask twice" used Python `==` on the params dict. `{"a": True} == {"a": 1}` is True, so two different asks became one request. Targets compared as an ordered list. |
| D-15 / C-19 | `static/agent.js` `renderApproval`, `renderPlan`, `renderRun` | No params were rendered anywhere. `_run_view` did not even send them. |
| C-04 | `web/agent_api.py:1792` | docs/247 added `_tell_agent`, but its message said "call run_node again with the same node, targets and params". It gave no `approval_id` and no params, so "the same" had to come from the model's memory. With D-05 fixed, one paraphrase files a new request and the plan stalls again. Nothing on the plan card said a request was waiting, and the toast said "run allowed" whether or not any agent heard it. |
| A-13 / D-12 | `web/agent_api.py:1541` | Empty targets went through. The `blocking` gate keys on target overlap, so writes held on q1 did not stop a run aimed at q1 by the node's defaults. `node_inject.build_node_overrides` sets the targets field only when targets are non-empty. |
| A-08 | `web/routes.py:25396` (`state_apply_to_live`), `:21860` (`_agent_undo_refusal`), `agent_api.undo_mine` | Ownership was "any `by_*` actor". docs/246 left agent-vs-agent to this finding. |
| reserved params | `core/node_inject.py:196` | `strip_reserved_overrides` drops `simulate` / `qubits` / `qubit_pairs` / `targets` at the chassis, after the request and its approval were accepted. |
| pair kind | `run_node`, `plans_add` | Targets were checked against qubits plus pairs together, never against the node's `targets_name`. The hasattr-guarded inject then never delivered them. |
| cross-node replay | `run_node` | `load_data_id` was never compared with the run it names. |
| unknown plan node | `plans_add` (structured branch) | Only the `/run` line resolved the node. A string `targets` was also checked letter by letter. |
| cancel twice | `plan_cancel` | It journaled after `agent_plans.stop`, which is a no-op on a finished plan. |
| C-30 | `approvals_decide` | The approve line had no actor. The person's edit was kept as `writes_original`, but the journal never compared it. |
| A-20 | `core/agent_runs.py:134` | `sorted(...)[:40]`. |
| C-08 | -- | Already fixed in docs/247 (`parse_run_line`). It is pinned again on the door this doc changes. |

## Behaviour now

### One reading of a run: `core/run_terms.py`

The ask-all check, the dedupe, the mismatch report and the journal all go
through the same functions:

- **targets** are a SET (order and repeats do not change what runs), in
  natural order;
- **params** have sorted keys;
- an integral float equals its int (`100.0 == 100`, one value to the node's
  parameter model);
- a bool is never a number (`True != 1`);
- a string is never a number (`"100" != 100`);
- list order counts;
- the **plan** the run belongs to is part of the run.

### D-05: an approval covers exactly the run the person allowed

- `run_node` in ask-all still refuses an approval that is missing, not
  approved, not a run request, or already used. The old wording is unchanged.
- Then `approvals.run_differences` compares the approved node, targets,
  params and plan with what was asked.
- Any difference gives 409 `awaiting_approval` with `differs` (one row per
  field or param, each with both values) and `not_covered_by` (the approval).
  A **new** request is filed for what was asked (`approval`).
- The allowed approval stays unused. It still covers exactly its own run.
- `approvals.file_run_request` does the find-or-add under the file lock, so
  two identical asks at once are one request.
- The request records its plan step (`step`). It is the request's `step`, or
  the running plan step that `step_for` matches.

### D-15 / C-19: every card and plan step shows its params

- `approvals.summary` carries `params`, `step` and `decided_by`.
- `_run_view` carries `params`.
- `agent.js` renders `paramsHtml` on:
  - the approval card head, for both run requests and writes approvals;
  - every plan step row;
  - every run card.
- Values are shown as sent (`num_shots=100000`, never `100 k`). A value
  longer than 40 characters is cut on the card and shown whole in the title.
  No overrides reads *node defaults*.
- A run request also names its plan and step, and says "Allow covers exactly
  this run".

### C-04: Allow tells the agent the exact call, and the card says whether it did

- The message `_tell_agent` sends is now built by `_allow_run_message`. It
  contains the call as JSON: `node`, `targets`, `params`, `approval_id`, and
  `plan_id` / `step` when set.
- The toast says "the agent was told to run it" when `agent_told`.
- When no in-app conversation is open (a terminal agent asked), the toast
  says "no agent conversation is open in SM; the agent that asked runs it with
  approval ap-…", as a warning.
- A pending step's row says **run request waiting for Allow**. Once allowed
  and not yet run, it says **allowed by <who> -- the agent runs it next**.
  This is `_step_requests`, read from the approvals store; it is not a field
  on the plan.
- A run request posts no `writes` on Allow. It posted `[]`, which wrote a
  `writes_original` onto a run record.

The designed path is the agent's: docs/173 has the agent file the request
and call `run_node` again with the id. SM does not run it in the agent's
place. The message is what makes that path reliable.

### A-13 / D-12: an empty target list is refused

- `run_node` answers 400 `refused: no_targets` with the chip's known names.
  It does so after the agent-door check, so a person's request still gets
  its 403 first.
- **Why refuse rather than "ask for its own approval":** nothing can show a
  person what an empty list means. It is the node file's own debug default,
  which SM does not read, and it differs per node. The plan door
  (docs/191 B01) and the `/run` line (docs/247 C-08) already require a
  target. This makes the third door the same.

### The request is what a card can show (P3s)

`run_terms.request_refusal` runs in `run_node` before any gate, for every
step of a proposed plan and for a `/run` line. Each refusal is 400 and comes
back as data:

- **`reserved_params`**: the request names `simulate`, `qubits`,
  `qubit_pairs`, `targets` or the node's own targets field. They are SM's to
  set, and they would have been dropped after the person allowed a card
  naming them.
- **`wrong_target_kind`**: a qubit for a `qubit_pairs` node, or the reverse.
  This is refused even when SM's `targets_name` guess is wrong, because the
  targets would not have been delivered either way.
- **`replay_other_node`**: `load_data_id` names a run whose experiment is
  another node's. The check uses `run_terms.same_node`, the rule run
  attribution (`agent_runs._attribute`) now also uses.
- **`bad_load_data_id`**: the value is not an integer.

An unknown id, or a chip with no dataset store, proves nothing and passes.
The node reports it itself.

### Plans, cancel, journal, node list

- **Plans.** A plan's steps must name a node in the calibrations folder. The
  step keeps the folder's own spelling (`05_power` becomes `05_power_rabi`).
  A string `targets` is read as the targets it names.
- **Cancel.** Cancelling a plan that already ended is 409 "plan is
  cancelled; there is nothing to cancel" and writes no journal line. The
  status read and the stop run under `_lock_for("plan")`, so two cancels at
  once write one line.
- **Journal lines (C-30):**
  - `human:Kim allowed the run of `node` on qA1 (load_data_id=1)`
  - `human:Kim approved 2 write(s) from `node` #3 -- applied; edited before writing: `qubits.qA1.f_01` proposed 6251000000.0 -> written 6300000000.0`
  - `asked to run `node` on qA1 (load_data_id=1) -- waiting for approval (mode ask-all)`
- **Node list (A-20).** `node_not_found` lists every name, plus `closest`.
  The `/run` line answers the same way.
- **Held-chain refusal.** The "writes on these targets wait" refusal names
  the run's real mode. It said "(mode ask-writes)" on an ask-all chip
  (measured on the rig).

### A-08: each agent presses for its own rows

`routes._owns_row(presser, entry)` is the one rule. A person's press (Apply,
Ctrl+Z) speaks for every row, as before. An agent owns only rows staged
under its own actor.

- **`/state/apply-to-live`.** For `X-SM-Agent`, after `human_groups`: 409
  `conflict: agent_groups`, with `actors` and `paths`. The message reads
  "rows staged by codex".
- **`/undo` (agent).** Another agent's group on top is 409
  `refused: agent_group`.
- **`/api/agent/undo-mine`.** It stops at the first row that is not the
  presser's own.
- **SM's own auto-mode push** (`_stage_writes`). It meets the same door, so
  its writes are parked as an approval ("apply refused: the tray holds rows
  staged by codex …").
- **The bridge.** `agent_groups` is said as another agent's rows, never "a
  human edited the chip". When the rows an agent saw have left the tray by
  another press, it says so (`seen`) instead of "nothing staged". It forgets
  that picture after its own apply.

**Why refuse rather than attribute:** docs/246's rule is "touches only rows
the agent staged". Applying another agent's row and recording it under the
presser's name would put a wrong author on that value in the journal and the
undo journal. Never showing wrong provenance outranks convenience. The owner,
or a person, can always apply it.

**The limit, said plainly:** the actor is the identity SM records (`by_claude`,
`by_codex`, ...). Two sessions of one CLI share it, so they share their rows.
Telling them apart needs a per-session identity, which is A-09's work.

## Changes

- `core/run_terms.py` (new):
  - `canon`, `terms`, `key`, `differences`, `params_text`;
  - `same_node`, `available_names`, `reserved_params`, `request_refusal`.
- `core/approvals.py`:
  - `_new` (records `step`);
  - `find_pending_run` / `_find_pending_run` through `run_terms.key`
    (plan-aware);
  - `file_run_request`, `run_differences`;
  - `summary` adds `params`, `step` and `decided_by`.
- `core/agent_runs.py`:
  - `check_gates`: `node_not_found` lists every name plus `closest`; the
    held-chain refusal names the real mode;
  - `_attribute` uses `run_terms.same_node`.
- `web/agent_api.py`:
  - `run_node`: the empty-targets refusal, `_request_refusal`, the request
    filing through `_file_run_request`, and the D-05 binding through
    `_approval_mismatch`;
  - new helpers: `_no_targets_refusal`, `_request_refusal`,
    `_file_run_request`, `_approval_mismatch`, `_allow_run_message`,
    `_edited_text`, `_step_requests`, `_plan_steps_refusal`;
  - also changed: `approvals_decide` (journal lines and the tell message),
    `undo_mine` (own rows), `_run_view` (params), `_plan_view`
    (step requests), `plans_add` (run line and structured steps), and
    `plan_cancel`.
- `web/routes.py`:
  - `_owns_row`;
  - `_agent_undo_refusal(store, presser)`;
  - the `agent_groups` gate in `state_apply_to_live`;
  - the undo call site.
- `mcp.py`: `t_apply_to_live` (the `agent_groups` wording, rows another press
  took, the reset after apply) and the `approval_id` schema text.
- `static/agent.js`: `paramVal` and `paramsHtml`; the step request; the
  approval card's plan and step; the run card's params; the Allow toast; a
  run request posts no writes.
- `static/style.css`: `.ag-params`, `.ag-params-none` and `.ag-step-req`.

The fix touches only the approval, plan-step and run-gate code. The new logic
sits in new functions and a new module, so the hunks in `run_node` and
`approvals_decide` stay a few lines each.

## Pins

**`tests/test_approval_is_what_was_seen.py` (30 tests).** These use
test_agent_runs's fixtures: the real chassis and routes, with a fake node
subprocess.

| class | what it pins |
|---|---|
| `TestRunTerms` | the normalization, `differences`, `params_text`, and the dedupe reading (`True` vs `1`) |
| `TestAnAllowCoversExactlyOneRun` | D-05: another params set, a dropped param, another plan and `True`-for-`9` are each a new request; the allowed approval stays unspent and runs once, exactly; a held chain names ask-all; a spent approval cannot run again; params appear on the approval, recent and run views |
| `TestAllowTellsTheExactCall` | the tell message parses to the exact call and that call runs; journal lines name the approver and the params; an edited value is named, an unedited one is not |
| `TestThePlanStepSaysWhatItWaitsOn` | `request` is pending, then approved, then gone once the run took it |
| `TestTheRequestIsWhatACardCanShow` | empty targets; reserved params; pair vs qubit kind; cross-node replay and a bad id; every node name plus closest; plan nodes and steps; cancelling twice; C-08 |
| `TestEachAgentPressesForItsOwnRows` | apply both ways, own rows still apply, undo / undo_mine, and SM's auto push parks |
| `TestTheBridgeSaysWhoseRowsTheyWere` | `agent_groups` wording, rows another press took, the reset after apply |
| `test_every_card_shows_its_params_selfcheck` | drives `tests/agent_params_selfcheck.cjs`: real `agent.js` under jsdom, 16 assertions |

**Reproduced on the base first.** The new pins were run with the fix stashed
(`git stash` of the source files): 19 of 25 failed. D-05 failed on the
behaviour itself: `run_node` with the `{num_averages: 9}` approval and
`{num_shots: 100000}` returned `status: done`. A-13, the reserved params, the
pair kind, the replay, the plan node, cancel and A-08 failed with
200 ≠ 400/409. A-20 failed with 40 < 52.

**Other files changed:** `tests/test_agent_composer_safety.py`. Its source
pin `available=sorted(` now looks for `available=names` and
`run_terms.available_names(` (A-20 sends every name).

**Mutation sweep: 47 of 47 red.** Each fix piece was reverted on its own, its
pins run, then restored:

- **the run reading:**
  - the binding check; the mismatch filing;
  - canon (float/int, bool); target set; `plan_id`; the dedupe;
  - `step` recorded; summary params.
- **the request:**
  - empty targets; the request checks;
  - reserved, kind, replay and bad id;
  - the 40-name cap; closest;
  - plan steps; the run line; raw plan targets; cancel twice.
- **A-08:**
  - the door gate; `_owns_row`; undo; `undo_mine`;
  - the actor wording.
- **the tell message and the journal:**
  - the call missing; `approval_id` missing;
  - the approver; the edited value (both ways);
  - params in the allow and asked lines.
- **views:**
  - run view params; step requests;
  - the held-chain mode text.
- **the bridge, three pieces:**
  - the `agent_groups` wording;
  - rows another press took;
  - the reset after apply.
- **JS, eight pieces:**
  - no params; an uncut long value; the step request;
  - the approval's plan and step; run card params; step params;
  - the toast ignoring `agent_told`;
  - a run request posting writes.

**Related files.** The 25 test files that touch the agent API, the bridge,
plans, approvals, the apply/undo doors and the panel ran in the `cqt` env:
**582 passed, 0 failed**. All 11 `tests/agent_*selfcheck.cjs` files are
green.

## Verified on the rig

**Setup:**

- Rig `D:\work\sm_qa_rigs\agent\rQ` was made by `make_rig.py` and served on
  port 5115 from this worktree (`srv_p1b2.bat`). It used a sandbox
  `USERPROFILE` / `HOME` / `CODEX_HOME`.
- The chip network was `127.0.0.1:1`. `/scheduler/effective-config` named
  `rQ\chip` as `state_path`.
- Dry run was ON. The only node runs were offline replays of rig run #1.
- Headless Chrome ran on CDP 9435. Every person press was a real mouse click,
  hit-tested with `elementFromPoint`.

**The live chip was unchanged throughout:** `state.json` stayed
`8f65e290cebe8e18…` and `wiring.json` stayed `92316ec9c1544eee…`.

Drivers are in `rQ\tools`; logs are in `rQ\ev`.

**Cards and the binding** (`journey.cjs`, `ev\journey.jsonl`):

1. The agent asked for `03_resonator_spectroscopy_single qA1
   {load_data_id: 1}` and got request `ap-ff15fd9295`. The card reads
   `qA1 load_data_id=1 … Allow covers exactly this run`
   (`shots\01_run_request_shows_params.png`).
2. Allow run was clicked. The toast read "run allowed — no agent conversation
   is open in SM; the agent that asked runs it with approval ap-ff15fd9295".
   No in-app agent was open in this step.
3. The agent spent `ap-ff15fd9295` on `{num_shots: 100000}`. SM answered 409
   with `differs` (`params.load_data_id: allowed 1`,
   `params.num_shots: asked 100000`) and filed a new request `ap-6b636f6f2f`.
   With the params dropped, it filed `ap-0762a918a5`. Both appear as their
   own cards, reading `num_shots=100000` and *node defaults*
   (`shots\03_other_params_is_a_new_request.png`).
4. The exact run ran once: `status done`, classification `ok`, run #2. The
   run card reads `load_data_id=1 SIMULATED #2`. The record says
   `used_by_run: 20261003-195930-30cd85`
   (`shots\04_run_card_params_and_consumed.png`).
5. Later, both spent approvals were refused: "approval_id must name an
   APPROVED, not yet used, run request" (`ev\reuse.jsonl`).
6. The two unallowed requests were rejected by click; the note prompt was
   answered.
7. A plan's step rows read `qA1 load_data_id=1` and `qA2 node defaults`
   (`shots\05_plan_steps_show_params.png`).
8. The request checks on the rig:
   - a plan step replaying run #1 under `11_power_rabi` gave 400
     `replay_other_node`;
   - `targets: []` gave 400 `no_targets`;
   - `simulate` in params gave 400 `reserved_params`;
   - `/run <node>` gave 400 "names no target";
   - cancelling the cancelled plan gave 409.

**C-04 with the real in-app agent** (claude 2.1.288, sandbox credentials;
`journey2.cjs`, `ev\journey2.jsonl`):

1. The agent proposed a one-step ask-all plan (replay of #1), and Start was
   clicked.
2. Twelve seconds later the in-app Claude's `run_node` was held. The step
   row read **run request waiting for Allow**, and the agent said it was
   waiting for `ap-6badee9578` (`shots\07_plan_step_waits_on_run_request.png`).
3. Allow run was clicked. The toast read "run allowed — the agent was told to
   run it", and the step read **allowed by human:tester — the agent runs it
   next** (`shots\08_allowed_agent_told.png`).
4. The agent called `sm.run_node {"node": …, "params": {"load_data_id": 1},
   "approval_id": "ap-6badee9578", "plan_id": …}` exactly as told.
5. The plan was **done 1/1** 29 s after Allow (run #3). The request was
   `used_by_run: 20261003-200250-a663e2` (`shots\09_plan_done_after_allow.png`).
6. The journal reads "human:tester allowed the run of
   `03_resonator_spectroscopy_single` on qA1 (load_data_id=1)".
7. The session was ended afterwards (the CLI process is gone).

**A-08 on the rig** (`ev\a08.jsonl`):

1. Claude and Codex each staged a T1. Claude's apply got 409
   `agent_groups ["by_codex"]`, and Codex's got 409 `["by_claude"]`.
2. Claude's `/undo` got 409 `agent_group`. Claude's undo-mine reverted
   nothing and stopped at Codex's row.
3. Each agent's undo-mine took back exactly its own row. The live chip was
   unchanged.

**Open → away → Back → reload:** each journey showed the same cards every
time (3 cards in the first, 14 in the second), with 0 console errors (`shots\06_after_back_reload.png`,
`shots\10_after_back_reload.png`).

**Clean-up:** the server, Chrome, the in-app CLI, and the rig's chip,
calibrations, data, inst, sandbox home and Chrome profile copies were all
removed. `rQ\shots`, `rQ\ev` and `rQ\tools` are kept as evidence.

## Not done here

- **Two sessions of one CLI** share an actor, so they share rows (A-08's
  limit above). The fix needs the per-session identity of A-09.
- **A plan Start is also an approval.** A `run_node` under a running plan's
  `plan_id` with other params than the step the person saw is not checked
  here. That is D-06 ("`plan_id` accepts nodes not in the plan"), in the
  arming-lifecycle cluster. It should reuse `run_terms.differences` against
  the step.
- **Unknown param names** (a typo such as `num_shot`) are still dropped by
  the node's hasattr-guarded inject. SM does not read a node's `Parameters`
  class. The card at least shows the name as typed.
- **An approved run request does not expire.** An Allow from yesterday is
  spendable today, on exactly its own run.
- **A terminal agent** is told by nothing. It has to read `approvals`. The
  toast says so.
- **The session stays armed after the plan ended** (seen in shot 09). That is
  C-20 / D-06, the arming cluster.
