# docs/253 -- Arming is scoped to a plan (agent campaign D-06, C-20, C-05, C-22, D-09, D-16)

## Report

The 2026-10-03 agent validation campaign (rigs under `D:\work\sm_qa_rigs\agent`,
findings in `FINDINGS.md`) found that rule 0 held only at the first click.
Rule 0: hardware starts only by a person's click. After that click, nothing
bounded it:

- **D-06 / C-20.** One Start armed the chip open-ended. After the plan
  ended, an unplanned node still ran. The arming survived the plan failing,
  End session and an SM restart. `plan_id` accepted nodes that were not in
  the plan. `step=0` was read as "no step", so a run reported into whichever
  step looked alike first.
- **C-05.** There was no Arm button when no session file existed. A stale
  session file's Arm armed any agent that called next, with no confirm. A
  terminal `claude` run was recorded under a dead in-app session's id and
  looked like an in-app run.
- **C-22.** After End session the strip still offered Arm and Stop. It said
  "today 0 events" while approvals waited.
- **D-09.** A terminal agent proposed a plan and a person pressed Start. SM
  then also started (or told) its in-app agent to run the same plan. Two
  agents drove one plan, and the person got two contradictory reports.
- **D-16.** `stop_by` was compared with today only, so a 06:00 night stop
  refused the whole evening before it.

The rule behind every fix: **arming is a grant for one plan, to one driving
agent, and it ends with a journal line.** "One explicit run" is a one-step
plan: a `/run` line, or a `plan_propose` with one step. Its card shows the
node, the targets and the params before the click.

## The arming model now

The grant lives in the chip's session file (`agent_session`):

- `start_token`: the truth `check_gates` reads, unchanged.
- `grant`: the scope.
- `last_grant`: how the last grant ended.

The logic lives in the new `core/agent_grant.py`.

| state | how it starts | what `run_node` may do | how it ends (one journal line) |
|---|---|---|---|
| not armed | -- | nothing: `no_start_token`, which says "propose a plan; a person presses Start" | -- |
| armed for plan P, driver D | a person presses **Start** on P's card (`/plans/<id>/start`) | run a **pending** step of P, as the card shows it (node, targets, params), called by **D** only | see the rows below |
| -- P finishes / fails / is skipped | the last step's report (`agent_plans.step_update` -> `agent_grant.plan_ended`) | -- | `disarmed: plan `P` finished` (or `failed`, `ended with nothing run`) |
| -- P is cancelled | Cancel on the card | -- | `plan `P` cancelled by X -- disarmed` |
| -- Stop (after this run / now) | the strip or the card | the step in flight finishes (after run) or is cancelled (now) | `Stop (now) pressed by X -- disarmed (plan `P`)` |
| -- Disarm (route only; the strip offers Stop after this run instead) | `POST /session/disarm` | the step in flight finishes and reports | `disarmed by X (plan `P` stopped)` |
| -- End session (in-app driver) | End session | the step in flight finishes and reports | `claude session ended by X -- disarmed (plan `P` stopped)` |
| -- the driver's process is gone | the in-app session is no longer open in this SM, or a terminal bridge's PID exited | as above | `disarmed: <driver> that drove plan `P` is gone (...)` |
| -- SM restarted | the SM process that armed it is gone (PID dead, or same PID with another boot id) | -- (a step left running is failed as interrupted) | `disarmed: SM restarted` |
| -- `stop_by` reached | the first `stop_by` time at or after the Start | refused `past_stop_by`; a step in flight finishes | `disarmed: the lab's stop time (06:00) was reached during plan `P`` |
| legacy token (no scope; an older SM's Arm) | -- | -- | `disarmed: an arming with no plan behind it ... was withdrawn` |

**When the end is noticed.** An end caused by a press or a step report is
eager: it happens in that request or thread. A plan reaching its end through
any writer also ends the grant eagerly. Every other end is noticed by
`agent_grant.reconcile`, the lazy half, which runs:

- before every `run_node` and every Start;
- on every read the strip, the pill or an agent makes (`/now`,
  `/chat/cards`, `/chat/status`, `/session`).

A grant armed by a dead SM process is also ended at SM start
(`agent_grant.sweep`, called from `create_app`). So SM restarting ends it in
the new process before anyone opens the chip. A grant that another live SM
window armed is left to that window.

**Who drives** (`agent_grant.request_driver`):

- SM's in-app session is told apart by `SM_SESSION`. SM generates this value
  per driving session (`chat_api._build_backend`) and writes it only into
  that session's MCP config (Claude: the json file; Codex: `-c
  mcp_servers.sm.env`). The bridge sends it back as `X-SM-Session`.
- Every other caller is a terminal agent, known by the id its bridge picked
  for itself (`agent_link.bridge_session`, `t-<hex>`, one per bridge
  process). SM also records its PID, `X-SM-Bridge-Pid`, but only from a
  loopback caller.
- A caller that sends no id is known by its CLI name alone.

The value is provenance, not authentication. A process that can read SM's
instance folder can read it; caller identity is A-09, separate work. The
value is never served: `agent_grant.public` strips it from every plan, run
and session view. A pin checks six reads for it.

**Who drives a plan:**

- **The proposer drives.** A plan a terminal agent proposed is driven by that
  agent. SM's in-app agent is neither started nor told, and its `run_node`
  under that plan is refused `not_the_driver`.
- **A person's plan** (a `/run` line, or human steps) and an in-app proposal
  are driven by SM's in-app session. Start sends it to the open session, or
  starts one with the plan as its first message.
- **A terminal proposer known to have exited** (its loopback bridge's PID is
  dead -- `claude -p` exits right after `plan_propose`) hands the plan to the
  in-app session. The Start line says so: `...; by_claude in a terminal
  (bridge PID n), which proposed it, has exited`.
- **"Allow run"** (ask-all) tells the in-app agent only when the in-app agent
  drives the armed plan (`_tell_agent`).

## Root causes (lines at 6e8c731e)

| id | where | cause |
|---|---|---|
| D-06 | `agent_runs.py:147` (`check_gates`) | The only rule-0 gate was "is there a `start_token`". Nothing tied the token to a plan, a step or an agent. |
| D-06 | `agent_api.py:2264` (`_plan_start_locked`) | Start wrote a bare token and `plan_id`. Neither the plan's end (`agent_plans._derive`, `stop`) nor End session (`chat_api.py:487`) nor a restart took it back. |
| D-06 | `agent_plans.py:173` (`step_for`) | A run with `step=i` was mapped to step i whatever node, targets or params it ran. |
| D-06 | `agent_api.py:1572` | `str(data.get("step") or "")` read `step: 0` as no step. |
| C-20 | as D-06 | A failed plan is just one more end that never reached the token. |
| C-05 | `agent_api.py:1671` (`session_arm`) | Arm wrote a token into whatever session file existed, stale or not, for any caller. |
| C-05 | `agent_api.py:1571` (`run_node`) | The run's `session_id` was the session FILE's, whoever called. A terminal run inherited a dead in-app session's id. |
| C-05 | `agent.js:626` | Arm was drawn only when the file had an `owner` (a chat session), so a terminal agent's "press Arm" pointed at nothing. |
| C-22 | `agent.js:628` | Stop was offered for any session file with an owner, alive or not. |
| C-22 | `agent.js:615` | "today N events" was printed even when N was 0. |
| D-09 | `agent_api.py:2288` | Start always sent the plan to the in-app session, or started one, whoever had proposed it. |
| D-16 | `limits.py:153` (`past_stop_by`) | `(now.hour, now.minute) >= (hh, mm)`, against today. |
| (found here) | `agent_session.py:39`, `agent_plans.py` `load` | Reads used plain `read_text` without the writers' lock. A read landing inside another thread's `ReplaceFileW` (the in-app session's Init event writes the file right after Start) saw NO file. `run_node` then read an armed chip as not armed: about one run in ten of this file's tests. |
| P3 | `agent_api.py:1115` (`session_get`) | The record was looked up by display NAME, but it is keyed by chip KEY, so this always answered null. |

## Behaviour now

**D-06 / C-20.** `run_node` reconciles first. Then it asks
`agent_grant.covers(session, plan, driver, node, targets, params, step)`.
Covered means all of these hold:

- the grant names this plan;
- the plan is `running`;
- the caller is its driver;
- a **pending** step matches exactly: the resolved node name, the targets and
  the params; with `step`, that step only. `agent_grant.same_terms` compares
  them the way docs/254 (`fix/approvals-bound`) compares an approval:
  - targets are a set;
  - params are canonical: `100.0` is `100`, `True` is not `1`, `"100"` is
    not `100`.

  **At integration, its body becomes a call to `run_terms`**, one function
  for both.

A run that is not covered reaches `check_gates` with no token. Earlier gates
keep their order (`no_env`, `node_not_found`, `stopped_by_human`, ...). At
the token gate the refusal is the specific one:

| refusal | when |
|---|---|
| `no_start_token` | no plan named. The text says: propose this run, a person presses Start. When a different plan is armed, it says which. |
| `no_start_token` + `plan_status` | the plan is draft, ended or not armed. The text includes why it ended (`last_grant`). |
| `not_in_plan` | not a pending step. The answer lists `asked` and `pending`, each step with its params. |
| `not_the_driver` | another agent drives the plan. The answer names the driver. |
| `past_stop_by` | the plan's arming ended at `stop_by`. |

The covered step's index becomes `req.step`, so the card reports into the
step that matched. Before this, a run carrying step 1's params moved step 0
(measured, `test_without_a_step_index_the_matching_step_is_the_one_that_moves`).

**C-05.** `POST /session/arm` answers 409 `refused: arm_is_per_plan`. The
person's door is the plan card's Start. The strip has no Arm button. A token
from an older SM's Arm (no scope) is withdrawn on the first read, with a
journal line. A run's record names the agent that asked:

- `session_id` is the in-app CLI session's id only for that session's own
  bridge, and `terminal:<bridge id>` otherwise;
- `meta.driver` is `{kind, actor, id/pid | backend}`;
- the run card says "via terminal".

**C-22.** The strip offers only doors that do something:

| door | offered while |
|---|---|
| Stop after this run | a plan is armed, or a run is in flight |
| Stop now | the in-app process is alive, a plan is armed, or a run is in flight |
| End session | a conversation is open |
| Arm, Disarm | never |

A zero event count is not printed. "waiting N" stands alone.

The strip shows:

- "armed: <plan> · by_claude, terminal" (or "· in-app claude"), with the full
  sentence in the title;
- "not armed", with a title saying when and why the last grant ended.

The plan card says who drives it and, when SM closed it, why
("ended 20:41 by SM (SM restarted)").

**D-09.** Covered in "Who drives" above. The terminal agent learns that it is
the driver from `plan_propose`'s answer: "When it is started you drive it --
SM's in-app agent will not: call plan_status ... then run_node step by step".

**D-16.** `limits.stop_deadline(limits, since)` is the first `stop_by`
wall-clock time at or after `since`, as epoch seconds, on SM's own clock:

- "06:00" armed at 18:00 is tomorrow 06:00;
- armed at 05:59, it is one minute later.

`past_stop_by(..., since=armed_at)` is what `check_gates` asks. With nothing
armed, `since` is now, so the refusal is the missing click, never a stop time
the agent never had. The grant itself ends at `stop_by`, with its journal
line, and its plan closes after the run in flight. A later `run_node` under
that plan is refused `past_stop_by`.

**Reads that see what was written.** `agent_session.load` and
`agent_plans.load` take the writers' lock. Readers in this process wait out a
replace. `agent_session.read_record` opens with share-delete and retries a
transient failure. Only an absent file reads as absent.

## How this composes with docs/249 (A-14 / D-13)

docs/249 (`fix/run-failure-class`, `agent_runs.py`, not touched here) owns
the run-level restart story:

- a run in flight at a restart is classified `interrupted`;
- the session it was armed for is disarmed (`_announce_interrupted`), with
  one journal line and one webhook;
- SM's own dead queue rows are swept.

This branch owns the grant-level story, and the two meet like this:

- **This branch's sweep runs first** (in `create_app`; the run registry is
  created lazily). It ends the grant with `disarmed: SM restarted`, fails a
  step left running ("interrupted: SM restarted while this step ran") and
  closes the plan. docs/249's announcement then finds no token and writes
  only its run line (`✗ ran X interrupted ...`), without its "the session was
  disarmed" suffix. Two lines, two facts, no duplicate.
- **When docs/249 clears the token first** (a registry created before any
  reconcile), it clears only `start_token`. `reconcile` then sees a scope
  with no token. It tidies the scope silently, because docs/249 already said
  it. If the arming process is gone, it also closes the plan the dead process
  left `running`, so that plan never blocks the next Start.
- docs/249's line says "a person arms it again". In this model that means
  presses Start on a plan. Reworded at integration (docs/254, "Reconciled
  with docs/253").

## Overnight: the envelope, and the hook points left for later

A person can arm ONE plan before leaving and have it run all night:

- mode `auto` on the card;
- `stop_by` (say 06:00);
- `max_writes_per_plan` and `max_delta` (unchanged; `limits_hold` keys
  writes by `plan_id`).

The grant lasts the plan's whole run, through midnight. It ends only:

- when the plan finishes, fails or is cancelled;
- at `stop_by`;
- at End session or Stop;
- on a restart;
- when the driving process is gone.

Pinned by `test_an_overnight_plan_is_armed_through_midnight_and_ends_at_stop_by`
(armed 18:00, still armed 00:30, ended 06:00).

Not built here, with their hook points:

- **`plan_done` / `needs_human` webhooks and the morning summary.** Use
  `agent_grant.ON_END`, a list of `cb(instance_path, key, grant, why)`.
  Each is called once per grant that ends, after it ended, whatever ended it.
  `last_grant` (with `code`: `past_stop_by` / `restart` / None) is the record
  a summary reads.
- **Stop-loss enforcement.** End the grant through
  `agent_grant.end(..., why="stop-loss: ...", code="stop_loss")` and close the
  plan with `agent_grant.close_plan`. The `covers` refusal then explains it
  from `last_grant`.
- **Times.** Every new field is an instant in epoch seconds:
  - `grant.at`;
  - `last_grant.ended_at`;
  - the value `stop_deadline` returns.

  `stop_by` itself stays the lab's "HH:MM" on SM's clock. The UTC step only
  has to change `stop_deadline`.

## Not done here

- **`agent_runs.check_gates`' own `no_start_token` text** said "press Arm",
  and `GATES` did not list `not_in_plan` / `not_the_driver`. Both were fixed
  at integration (docs/254, "Reconciled with docs/253").
- **The MCP tool descriptions** (`mcp.py`) do not mention the plan scope.
  The refusals do. The p1a branch is editing `mcp.py`.
- **Identity.** A caller can still claim a person (`X-SM-Actor` without
  `X-SM-Agent`) and press Start. That is A-09, on `fix/agent-guardrails`.
- **A plan whose step failed** cannot retry that step under the same grant.
  A new plan, so a new click, is needed. That is deliberate: rule 0 per run,
  and a failed run is a person's call.
- **The beforeunload prompt** while an in-app turn is open blocked the walk's
  headless `location.reload()` (pre-existing, unrelated).
- **C-13 / docs/251.** The diagnostics banner covered the composer's left
  half at 1500x950 on the rig. The walk clicked the right half.

## Verified on the rig

Rig `D:\work\sm_qa_rigs\agent\rArm`:

- port 5113, served from this worktree;
- sandbox `USERPROFILE` / `HOME` / `CODEX_HOME`;
- chip network `127.0.0.1:1`, Dry run ON;
- headless Chrome over CDP 9433, real mouse clicks.

Walk script: `arming_walk.cjs`. Evidence (the walk script, screenshots,
logs and the day's journal) in `D:\work\sm_qa_rigs\agent\ev253\`. The rig
itself was deleted after the walk.

- **Terminal driver (D-06, D-09).**
  1. A terminal agent (this script on SM's door with its own bridge id)
     proposed an offline refit (`load_data_id=1`). Its answer said "you drive
     it".
  2. The person pressed Start. The strip said "no in-app session · armed:
     offline refit qA1 (#1) · by_claude, terminal", with Stop after this run
     and Stop now. No `[Start] plan` card went to the in-app agent.
  3. A node off the plan was refused `not_in_plan`. Another agent was
     refused `not_the_driver`.
  4. The step ran (run #2). Same second: `disarmed: plan `offline refit qA1
     (#1)` finished`. The strip went to "not armed" with no Stop.
  5. A later unplanned `run_node` was refused `no_start_token`.
  6. Calibration log -> Back -> reload: intact.
- **The driver's process is gone.** The walk script that drove a second
  plan exited. On the next pill poll: `disarmed: by_claude that drove plan
  ... is gone (its SM bridge, PID 3728, exited)`, and the card went to
  `STOPPED ... ended by SM`.
- **A plan that fails (C-20).** `11_power_rabi` on qA1 with no hardware
  failed to connect. Same second: `disarmed: plan `rabi qA1 ...` failed`.
- **SM's in-app agent, the real Claude CLI.**
  - A typed `/run 03_resonator_spectroscopy_single qA1 load_data_id=1`, then
    Start: "armed ... · in-app claude".
  - Real Claude called `run_node` through its bridge with `SM_SESSION` and
    was accepted as the driver (run #3). The plan finished and the grant
    ended with it.
- **End session (D-06, C-22).**
  1. A second `/run` (qA2) was started, then End session was pressed.
  2. Journal: `claude session ended by human -- disarmed (plan `...qA2`
     stopped)`. The card: `ended 20:30 by SM (the in-app claude session was
     ended by human)`.
  3. Claude, finishing its turn, called `run_node`. It was refused
     `no_start_token` and told the person why.
  4. A fresh open, a reload and Back all showed "claude · human · not armed
     · waiting 1" with **no** Arm, Stop or End.
- **SM restart.**
  1. A terminal plan was armed.
  2. The SM process was killed and started again.
  3. Before any request, the session file already read `start_token: null`,
     `last_grant.why: SM restarted` (the sweep).
  4. After the chip was loaded: "not armed", journal `disarmed: SM
     restarted`, card `STOPPED ... ended by SM (SM restarted)`. `run_node`
     under the plan was refused, naming the restart.

Console errors: 0 in every phase.

## Tests

`tests/test_arming_scope.py` (new, 29 tests) reproduced every finding first:
17 of the first 17 were red on 6e8c731e, each for the reason the finding
names.

Updated for the new model:

- `test_agent_runs.py`: `_arm` presses Start on a plan of the pins' run;
  `_run` names the armed plan. The gate-order pin uses the Stop route and a
  `stop_by` counted from the arming. The simulate pin arms a plan whose mode
  is auto. The Arm pin now asserts the blank Arm is refused.
- `test_agent_panel.py`: an agent-proposed plan is driven by its proposer;
  the in-app "first message" path is a `/run` line.
- `agent_panel_selfcheck.cjs`: no blank Arm; the doors per state; the armed
  plan and its driver named; no "0 events"; "not armed" carries why.

The 30 test files that touch the agent API, plans, sessions, chat, limits,
the bridge, the journal or the strip (cqt env), on the final commit: 804
passed, 6 skipped, 0 failed.

Trial merges (`git merge-tree`) against `fix/agent-guardrails`,
`fix/approvals-bound`, `fix/run-failure-class` and `integ/batch2` are all
clean. To get there, the bridge headers and the reconcile call moved off
those branches' hunks.

## Mutation sweep

Each fix piece was reverted on its own, its pins run, then restored.
**43 of 43 went red** (scratch script: `mutate.py`, one revert at a time, restored after each).

| reverted piece | caught by |
|---|---|
| plan end -> grant end, eager (`agent_plans.step_update` hook) | `test_an_unplanned_node_after_the_plan_finished_is_refused` (reads the FILE before any route reconciles) |
| lazy: an ended plan ends its grant | `test_a_plan_closed_by_another_hand_disarms` |
| restart: boot / armer check | `test_a_restart_disarms` |
| startup sweep in `create_app` | `test_a_restart_disarms` (the file, before any request) |
| in-app process gone | `test_the_in_app_sessions_exit_disarms` |
| terminal bridge PID gone | `test_the_driving_processs_exit_disarms` |
| `stop_by` ends the grant | `test_an_overnight_plan_is_armed_through_midnight_and_ends_at_stop_by` |
| legacy token withdrawn | `test_a_token_from_before_is_withdrawn` |
| uncovered run still reaches the gate with its token | `test_a_node_outside_the_plan_is_refused_under_its_plan_id` |
| params / targets not compared (each) | same |
| params canonical (a bool is not a number) | `test_the_same_run_is_compared_the_way_an_approval_compares_it` |
| only PENDING steps | `test_a_step_index_names_the_step_even_when_it_is_0` |
| driver check | `test_a_terminal_agents_plan_is_driven_by_it_alone` |
| armed + no plan_id names the armed plan | `test_without_a_step_index_the_matching_step_is_the_one_that_moves` |
| `req.step` from the matched step | same |
| `step=0` parsed | `test_a_step_index_names_the_step_even_when_it_is_0` |
| blank Arm refused | `test_a_stale_session_files_arm_arms_nothing` |
| run `session_id` = the asker's / run meta names the driver (each) | `test_a_terminal_run_is_never_filed_under_the_in_app_sessions_id` |
| session door by key | `test_the_session_door_reads_the_open_chips_record` |
| plan records its proposer / Start: terminal proposer drives (each) | `test_a_terminal_agents_plan_is_driven_by_it_alone` |
| a gone proposer hands off | `test_a_gone_proposer_hands_the_plan_to_the_in_app_agent` |
| in-app driver recognised by `SM_SESSION` | `test_an_unplanned_node_after_the_plan_finished_is_refused` |
| the in-app value never served | `test_the_in_app_secret_is_never_served` |
| End session ends the in-app grant | `test_end_session_disarms_and_closes_the_plan` |
| cancel line / Stop line say "disarmed" (each) | `test_cancel_disarms_in_one_line`, `test_stop_says_it_disarmed_in_its_one_line` |
| `stop_deadline` next occurrence | `test_a_night_stop_counts_from_the_start` |
| gate passes `since` / unarmed falls back to now (each) | `test_the_gate_reads_it_from_the_grant`, `test_unarmed_the_refusal_is_the_missing_click_not_the_stop_time` |
| `past_stop_by` refusal after the stop ended the grant | `test_an_overnight_plan_...` |
| `ON_END` listeners | `test_a_grant_end_is_announced_to_listeners_once` |
| readers take the writers' lock | `test_a_reader_never_sees_an_armed_session_as_absent` |
| "Allow run" tells only an in-app driver | `test_allow_run_tells_the_in_app_agent_only_when_it_drives` |
| `close_plan` on a lazy end | `test_a_restart_disarms` |
| strip: no blank Arm / Stop only where it acts / no zero count / names plan + driver (each) | `agent_panel_selfcheck.cjs` via `test_agent_panel_selfcheck` |
| the bridge sends its id + PID | `test_a_terminal_bridge_names_itself_and_its_process` |
| the in-app MCP config carries `SM_SESSION` | `test_the_in_app_sessions_bridge_carries_its_value` |

The first pass found five vacuous pins (green under their mutation):

- The eager-end pin read the key through a route, and the route's
  reconcile ended the grant first.
- The targets case also differed in params.
- The "armed, no plan_id" branch differed only in its text.
- `step=0` was masked by the look-alike match.
- The blank-Arm mutation was undone by the legacy-token withdrawal.

Each pin was tightened and re-swept red. The pins whose test changed were
then re-run against their mutations (6/6 red).

