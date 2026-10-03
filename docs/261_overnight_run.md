# docs/261 -- The overnight run: one envelope, a stop-loss read from the plan, an alert for every end, one morning page (agent campaign D-14)

## Report

The 2026-10-03 agent validation campaign (`D:\work\sm_qa_rigs\agent\FINDINGS.md`)
found, as **D-14**, that the night half of the agent did not exist:

- `stoploss_target` (3) and `stoploss_plan` (8) were in `limits.DEFAULTS` and
  read by no code.
- The `plan_done` webhook never fired for an agent plan.
- An ask-all wait sent no `needs_human`.
- A person's Stop sent `agent_failure` (measured on the stream D rig:
  `classification: cancelled`, "stopped now by human:tester").
- There was no place to read a night in the morning.

docs/253 left the hook points ("Overnight: the envelope, and the hook points
left for later"). This doc builds the cluster on them.

## The envelope (what the user agreed)

A person who wants the agent to calibrate all night approves ONE envelope
before leaving, not every write:

| part | where it lives | enforced by |
|---|---|---|
| the plan (nodes, targets, params) | the plan card | `agent_grant.covers` (docs/253): only its pending steps, exactly as shown |
| mode `auto` | the card's mode | `agent_runs.run_mode` |
| `stop_by` | Limits | `limits.stop_deadline` from the Start click (docs/253), and now a watch (below) |
| `max_writes_per_plan` | Limits | `agent_runs.limits_hold` |
| `max_delta` per family | Limits | `agent_runs.limits_hold` |
| stop-loss: `stoploss_target`, `stoploss_plan` | Limits | `agent_overnight.stoploss` (new) |

Inside the envelope a write that passes the gates applies on its own, through
the one door: a pre-apply version (the door's own backup), a journal line,
and an undo unit. Outside it the write is NOT applied: it is held for the
morning, and that target stops while the others go on. Too many gate fails
halt the whole plan. Changing a limit stays person-only (docs/252's window
cookie); nothing here loosens that.

## What a gate fail is

Read from what the run path already has. No new judgement is made.

| the step's run | verdict per target | counts toward the stop-loss |
|---|---|---|
| failed, by its own class (docs/249): `host_unreachable`, `hardware_contention`, `timeout`, `node_error`, `interrupted` | `fail` on every target it ran on | yes |
| finished, and the node's OWN outcome for that target says it failed (`node.json` `outcomes`, which `_attribute` already reads; the word "fail", as `story._outcome` reads it) | `fail` on that target only | yes |
| finished in mode **auto** and its writes were HELD: `max_delta`, `max_writes_per_plan`, a write that touches a target whose fit failed (new, below), a resized list / too many leaves, or the door refusing the apply | `held` on every target of the step | yes, and the target halts at once |
| finished, writes applied or none | `pass` | resets the target's run |
| cancelled by a person's Stop, or skipped | none | no |
| held by the MODE itself (ask-writes, ask-all, Dry run) | `pass` | no: that is what the mode was chosen for |

Each verdict is stored on the plan step (`step.gate = {target: {v, why}}`).
Everything below is computed from the plan record, so it survives a restart.

- **Consecutive** = the gate fails at the end of a target's steps, in the
  order the steps ENDED. A pass resets it.
- **The plan's count** = every (step, target) gate fail. A step on three
  targets that cannot reach the host is three.
- **0 turns a stop-loss off**, as `max_writes_per_plan` 0 means no cap.

`check_fit` (the autofit gates over a saved run) is an agent tool, not
computed on the run path. It is not part of the definition; see Open.

## Root causes (lines at f46d018d)

| gap | where | cause |
|---|---|---|
| stop-loss | `core/limits.py:31-32` | the keys were stored and validated (`:79`); nothing read them |
| `plan_done` | `core/agent_runs.py:936-942` | the run driver sent `agent_failure` or `needs_human` and nothing else; no plan end announced anything |
| ask-all `needs_human` | `web/agent_api.py:1736` (`_file_run_request`) | the request was filed and journaled, nobody was told |
| Stop -> `agent_failure` | `core/agent_runs.py:936` | `if status != "done"`: a `cancelled` run is "not done" |
| run requests outlive a Stop | `core/agent_session.py:119` (`request_stop`) | the Stop ends the grant itself, without `agent_grant.end`, so docs/254's `ON_END` expiry never ran for it |
| no morning page | -- | nothing read the plan, approval and run records together |
| Start shows no envelope | `web/static/agent.js:1031` (`startPlan`) | one press posted `{}` |
| `stop_by` noticed in the morning | `core/agent_grant.py:277` (`reconcile`) | only on a read (strip, pill, an agent's call); a night with the laptop closed and the agent between steps noticed 06:00 at breakfast |

## Behaviour now

### A target halts; the others go on

After each step reports (`Registry._drive`), `agent_overnight.after_step` reads
the plan's stop-loss:

- A target whose LAST step's writes were held halts at once.
- A target with `stoploss_target` gate fails in a row halts.
- Halting (`agent_plans.halt_targets`) records `plan.halted[target] = {why, at,
  skipped}`. Every PENDING step that touches the target is skipped, with
  `halted` and the reason. One journal line:
  `stop-loss: target qA2 halted in plan `night A` -- its write is held for a person -- held: max_delta ...; its 1 remaining step(s) are skipped, the other targets continue`.
- A step on [qA1, qA2] with qA1 halted is skipped whole. A step runs as the
  person saw it or not at all.
- `run_node` for a halted target is refused `target_halted` (409) by
  `agent_grant.covers`, after the driver check. It names the target and why.
  One journal line per (plan, step, node, targets), so an agent that asks again
  does not repeat it.
- When halting leaves nothing pending, the plan ends there, with `end_code`
  `stop_loss`.
- The run's result carries `stop_loss: {halted, plan_halt}`, so the agent
  reads it in the same answer.

### The plan halts

At `stoploss_plan` gate fails, `after_step` ends the grant through the end path
docs/253 left for it: `agent_grant.end(..., why="stop-loss: 5 gate fails in this
plan (stoploss_plan 5) -- plan `P` halted", code="stop_loss")`, then
`agent_grant.close_plan(..., code="stop_loss")`. `end` writes the journal line
("disarmed: stop-loss: ..."). A later `run_node` under the plan is refused
`stop_loss`, by `covers`, from `last_grant.code`. `GATES` lists `target_halted`
and `stop_loss`.

### A write from a failed fit is held in auto

"Inside the envelope, writes that PASS the gates apply." Before this, a done
run's writes applied whatever the node's own outcome said. Now
`agent_overnight.fit_hold` holds them when one of them touches a target whose
outcome failed. "Touches" means a dot-path segment IS the target name, never a
prefix: `qA10` is not `qA1`. A run whose failed target wrote nothing applies
the other targets' writes, and that target counts a `fail`.

### The phone (D-14)

| event | when | payload (besides `event`, `chip`, `at`) |
|---|---|---|
| `plan_done` | ONCE per started plan that ends, whatever ended it (`agent_plans.ON_PLAN_END`) | `plan_id`, `title`, `status`, `reason`, `why`, `mode`, `started_at`, `started_by`, `ended_at`, `counts` {`applied` writes, `applied_runs`, `held`, `held_writes`, `failed` steps, `steps`, `done`, `skipped`, `cancelled`, `gate_fails`}, `halted_targets`, `link` |
| `needs_human` | an ask-all run request is FILED (not re-asked), or a run's writes are held | `what` (`run_request` / `held_write`), the approval, node, targets, params, `plan_id`, `step`, `why`, `halted_targets`, `link` |
| `agent_failure` | a run failed by its own class (`FAIL_CLASSES`) | as before (docs/249), plus `plan_id`, `step`, `halted_targets`, `plan_halted`, `link`; `error` cut at 300 |

- `reason` is one of `finished`, `failed`, `cancelled`, `stopped` (a person's
  Stop, Disarm or End session), `stop-loss`, `stop_by`, `restart`,
  `driver_gone`. It is read from the record SM wrote when it closed the plan
  (`plan.end_code`), never parsed from words.
- `link` is the summary's path (`/agent/summary?plan=<id>`). SM's host is the
  reader's own.
- A person's Stop sends no `agent_failure`. The plan's `plan_done` says
  `stopped`, once.
- Every send runs on a thread through `limits.notify` (the chip's own Limits
  gate), so a dead URL never stalls a request or the run driver.
- **No secrets, no state beyond the journal.** The webhook URL, the in-app
  `SM_SESSION` and the start token never appear. A state value appears only
  inside a sentence the journal already says: the held jump, as in
  "max_delta for resonator_spectroscopy: `qubits.qA2.resonator.f_01`
  7394280195 -> 7399280195.0". That is pinned by checking every number in
  every `why`/`what`/`error` against the day's journal.

**Why `plan_done` hangs on the plan's end, not on `agent_grant.ON_END`.** A
grant can end while the plan's last step still runs (`stopping`: the run
finishes and reports, and only then is the plan over). A person's Stop ends
the grant without `ON_END` (`agent_session.request_stop`). So the plan's end
is the one place every end passes exactly once. `agent_plans.ON_PLAN_END` is
called from `step_update` (the plan derived its end) and from `stop()` (Stop,
Cancel, SM's `close_plan`), only for a plan that was started, after the record
is saved. Its listener (`agent_overnight.plan_ended`) also expires the plan's
run requests. That is docs/254's rule, which a Stop used to miss.

**Why SM says why first.** `close_plan(code=...)` and the docs/249 restart
path call `agent_plans.mark_end` BEFORE failing an interrupted step. Failing
the last step ends the plan by itself, and its `plan_done` must say
`restart`, not `failed`.

### The grant watch

`agent_api.watch_grants_once` reconciles every grant THIS process armed. Its
`sm_pid` and `sm_boot` must be ours; another live window's grant is left to
it. `start_grant_watch` runs it every 30 s from `create_app`. It is off in
the test suite (`SM_DISABLE_ENV_WARMUP`) unless `SM_GRANT_WATCH_S` asks.
`stop_by` now ends the plan at 06:00 with nobody reading. On the rig,
`plan_done stop_by` arrived 3 s after the stop time with no page open.

### The morning summary

`/agent/summary`: a page, linked from the strip ("Night summary →") and from
every webhook. It shows ONE started plan, the newest or `?plan=`, "since" its
Start. It is read from records on disk: the plan file, the approvals file,
each run's `meta.json` and the undo journal sidecar. Nothing comes from memory.

- **Ended**: the reason badge, when, the why. For `stop_by` it also shows the
  stop time itself (the deadline), beside when SM noticed it.
- **Counts**: applied writes / runs, held, failed steps, gate fails, steps
  done / skipped / cancelled.
- **The envelope approved at Start** (folded). The plan keeps it:
  `plan.envelope` = the values + `deadline` + `since` (= `started_at`, the
  same instant the grant counts `stop_by` from).
- **Held for you** (n waiting, m decided): each held write with its rows
  (old, new, now), **Write to chip** and **Reject**. These are the approvals
  API, as the person's press (cookie). A pending run request shows **Allow
  run**.
- **Applied**: each run with its rows and **Undo**. Undo is the applied log's
  own compare-and-swap revert (`/auto-apply/revert`). It stages the old values
  in the review tray; Apply writes them (docs/107's covenant). The run's undo
  unit is found by its group: `undo_journal.make_unit` now records `gid`
  (`agent:<run key>`), additively. The undo state is read, not assumed:
  - `reverted`;
  - `undone` (Ctrl+Z);
  - `changed since -- no undo`: a later write moved a value, so the revert
    would be refused;
  - `no undo record`: an older sidecar.

  Each run also shows the door's pre-apply version (`result.pre_apply_ts`).
- **Failed**: when, step, target, class (`host_unreachable`, ...,
  `fit_failed`), what.
- **Halted targets**: target, when, why, steps skipped.
- **Other plans**: links.

Times are `ts-local` spans: the viewer's zone with its UTC offset (docs/244,
`SnapTime.display`). Every stored instant is epoch seconds, UTC: `started_at`,
`end_at`, `halted.at`, `envelope.deadline`, `envelope.since`. `stop_by`
stays the lab's "HH:MM", as docs/253 left it.

### Start shows the envelope

On a plan in mode `auto`, **Start** first reads `GET
/api/agent/plans/<id>/envelope`. The card then shows what the press approves:

- stop by: the time, what it means ("the first 06:00 after Start"), the
  deadline in the viewer's zone, and how far away it is;
- max writes;
- max |Δ| per family;
- the stop-loss, and what a gate fail is;
- the alerts (webhook host only, and its events);
- the steps, as the card shows them;
- a warning when Dry run is ON, because every run of an auto plan would be
  refused `simulate_on_in_auto`.

**Start -- approve this envelope** posts `{"envelope": <the values shown>}`.
`_plan_start_locked` compares them with what it would arm now
(`envelope_differences`). If anything moved since the look (a person changed
a limit in another window), the answer is 409 `envelope_changed`, naming each
field. Nothing is armed, and the card shows the new envelope. **Back** closes
it. Other modes start in one press, as before. The STARTED journal line of an
auto plan names the envelope.

## Changes

- `core/agent_overnight.py` (new):
  - `FAIL_CLASSES`, `touches`, `fit_hold`, `gate_verdicts`, `stoploss`,
    `after_step`;
  - `plan_end_reason`, `plan_counts`, `plan_done_payload`, `send`,
    `plan_ended`, `needs_human_run_request`;
  - `envelope`, `envelope_values`, `envelope_differences`, `envelope_record`;
  - `summary`.
- `core/agent_plans.py`: `ON_PLAN_END` + `_announce`, called from `step_update`
  and `stop()`; `halt_targets`; `mark_end`; `stop(code=)`.
- `core/agent_grant.py`: `covers` (`target_halted`, `stop_loss`); `_invalid`
  returns the end's code (`driver_gone` new); `close_plan(code=)`; `reconcile`
  passes the code.
- `core/agent_runs.py`: `GATES`; `_drive` (the gate verdicts on the step,
  `after_step`, which webhook); `_route_writes` (`fit_hold`, `pre_apply_ts`,
  the held write's `step`); `_scan_metas` (`mark_end` + `code="restart"`).
- `core/undo_journal.py`: `make_unit` records `gid`.
- `web/agent_api.py`:
  - `watch_grants_once`, `start_grant_watch`;
  - `_journal_halted_refusal`;
  - `_file_run_request` (needs_human);
  - `_stage_writes` (`pre_apply_ts`);
  - `_plan_start_locked` (the envelope check and record, the journal line);
  - `plan_envelope`, `summary_json`, `summary_data`, `_dry_run_on`,
    `_envelope_at`, `_envelope_line`.
- `web/app.py`: starts the grant watch.
- `web/routes.py`: `/agent/summary`.
- `templates/_agent_summary.html` (new); `base.html` (the page, the nav
  highlight, the core script).
- `static/agent-summary.js` (new): approve, reject, the undo re-read.
- `static/agent.js`: the envelope on the card, `confirmStart` / `closeEnvelope`,
  the step row's stop-loss marks, the strip's summary link.
- `static/style.css`: `.ag-envelope`, `.agent-summary`.

No existing test was edited.

## Pins

`tests/test_overnight_run.py`: 39 tests on the shipped routes. A fake node
moves `qubits.<t>.f_01` per target, or fails per target.

| class | pins |
|---|---|
| `TestWhatAGateFailIs` (9) | a failed class fails every target; Stop and skip are not failures; the node's own failed outcome fails that target only; an envelope hold is a gate fail, the mode's own hold is not (ask-writes, Dry run); consecutive in END order, reset by a pass; 0 is off; a held write halts at once; a failed fit holds only a write on that target (segment, not prefix); an undo unit names its group |
| `TestATargetHalts` (3) | three host-unreachable runs halt qA1: its pending step skipped, journal line, `target_halted`, the node never reached, qA2 runs and applies, `plan_done` names the halt; the refusal journaled once; a shared step skipped whole |
| `TestThePlanHalts` (1) | `stoploss_plan` 3: grant `last_grant.code` `stop_loss`, plan stopped with `end_code`, "disarmed: stop-loss" line, `stop_loss` refusal, one `plan_done` reason `stop-loss` |
| `TestTheEnvelopeHolds` (3) | a jump past `max_delta` held, target halted at once, the other target applies, `needs_human held_write` names the halt, `plan_done` counts; a failed fit's write held in auto; a failed fit with no write on its target is a fail, not a hold |
| `TestThePhoneHears` (11) | `plan_done` once, with counts and link; cancel says cancelled; a person's Stop now is no `agent_failure` and `plan_done` says stopped; a failure names its plan; an ask-all request alerts once; a Stop expires the plan's run requests; no secret or extra state value in any payload; `stop_by` ends it with nobody reading (the watch); the watch leaves another window's grant; a restart says `restart` even when failing the step ends the plan; so does docs/249's registry path |
| `TestStartShowsTheEnvelope` (4) | every value plus the deadline, lines, steps and the Dry run warning; the Start keeps the envelope (`since` = `started_at`) and journals it; a changed envelope arms nothing (409, `differs`); the envelope is read-only and limits stay the person's |
| `TestTheMorningSummary` (7) | the night listed, and IDENTICAL after a restart (a new app on the same instance); the page's sections and doors; undo stages the old value and reads `reverted`; a value changed since offers no undo; a held write approved from it; the newest started plan is the default, the others linked; no started plan says so |
| `test_overnight_selfcheck` | `tests/agent_overnight_selfcheck.cjs`: the real `agent.js` + `agent-summary.js` under jsdom, 42 assertions |

The selfcheck covers:

- an auto Start reads the envelope and arms nothing yet;
- every label, the deadline through `SnapTime.display` with "(in 11 h 4
  min)", the Dry run warning and the steps;
- Back;
- Start posts exactly what it showed; a 409 shows the new envelope and says
  why, and the second press approves the second look;
- ask-writes starts in one press;
- the strip link and the step row's stop-loss marks;
- the summary's approve / reject doors, the re-read, the answer kept across
  the re-read, the times localized after it (fetch and htmx paths), and the
  undo's re-read and refusal text.

## Mutation sweep

Each piece was broken on its own, its pins run, then restored. Scratch script:
`scratchpad/night/mutate_261.py`; each mutated Python file compiles.

**69 of 69 red.** The first pass had one anchor typo (M44), fixed and re-run red.

| broken piece | caught by |
|---|---|
| gate verdicts: a failed run not a fail / Stop counted / outcome ignored / any mode's hold counted / Dry run hold counted (M01-05) | `TestWhatAGateFailIs` |
| stop-loss: index order / no reset / 0 not off / held not at once / fit hold by prefix / no `gid` (M06-11) | same, and the summary pins |
| no `after_step` / no skip / shared step not skipped / no `target_halted` / refusal journaled every time / never journaled / covers after pending (M12-17, M64) | `TestATargetHalts` |
| no plan halt / end without code / no `stop_loss` refusal (M18-20) | `TestThePlanHalts` |
| fit hold dropped (M21) | `test_a_write_from_a_failed_fit_is_held_in_auto` |
| no announce from `step_update` / from `stop()` / announced twice (M22-24) | `TestThePhoneHears` |
| Stop sends `agent_failure` / no run-request alert / alert on every ask / no expiry / reason ignores `end_code` / mark-after-fail (both paths) / reconcile drops the code / watch reconciles another window / watch does nothing (M25-34) | `TestThePhoneHears` |
| envelope drops the stop-loss / skips a key / Start ignores it / not kept / not journaled / deadline from a later instant (M35-40) | `TestStartShowsTheEnvelope` |
| summary: no undo unit / reverted offered / changed offered / no failures / no halted / decided dropped / page doors (M41-48) | `TestTheMorningSummary` |
| JS: auto without envelope / nothing posted back / changed not re-shown / every mode shows it / not `SnapTime` / no strip link / wrong door / no re-read / answer lost / undo not re-read / times hidden after re-read / no step marks (M49-58, M65-66) | `agent_overnight_selfcheck.cjs` |
| webhook URL in `plan_done` / failure names no plan / held names no halt / no pre-apply version / held not counted (M59-63) | the matching pins |
| decided counted as waiting / own plan under the others / held write names no step (M67-69) | the summary pins |

## Tests

cqt env, on the final commit:

- **The agent files: 29 files, 708 passed, 0 failed.**
  - The new `test_overnight_run`.
  - `agent_runs`, `arming_scope`, `approval_is_what_was_seen`,
    `run_failure_class`.
  - `runner_p7` and `limits` (notify).
  - `agent_api`, `agent_backend`, `agent_composer_class` / `_safety`,
    `agent_firstuse`, `agent_guardrails`, `agent_history_restore`,
    `agent_may_change`, `agent_panel`, `agent_pill`, `agent_setup`,
    `agent_store_race`.
  - `bundles`, `chat_api`, `journal`, `journal_page`.
  - `mcp_bridge`, `mcp_bridge_safety`.
  - `refused_apply_changes_nothing`, `story`, `takeback_verbatim`,
    `undo_journal`.
- **Every `tests/agent_*selfcheck.cjs`: 12 files, all green, 410
  assertions.**

## Verified on the rig (the rehearsal)

**Setup:**

- Rig `D:\work\sm_qa_rigs\agent\rNight` (`make_rig.py rNight 5119`).
- SM served from this worktree on 5119 (`srv_night.bat`), with
  `USERPROFILE` / `HOME` / `CODEX_HOME` sandboxed.
- Headless Chrome on CDP 9439, real mouse clicks with `elementFromPoint`
  checked.
- A webhook receiver on `127.0.0.1:5129` (`tools/hook_listener.py`).
- The chip network was `127.0.0.1:1`, asserted before every `run_node`. No
  hardware.

**Dry run was OFF.** `check_gates` refuses every auto run while it is on
(`simulate_on_in_auto`). The network is what kept the hardware out: no QM
server can be reached at `127.0.0.1:1`.

**The runs:**

- Gate-passing and `max_delta`-violating writes came from
  `96_resonator_spectroscopy_rehearsal`. It is a fake node added to the rig's
  calibration COPY: it moves `resonator.f_01` by `step_hz`, or reports a failed
  fit, and saves like any node, so it made real run folders, outcomes and
  writes. The file is kept in `tools/`.
- One write came from a real offline replay (`03_resonator_spectroscopy_single
  load_data_id=1`).
- Failures were real `11_power_rabi` runs: `host_unreachable`.

The walk is `tools/night_walk.cjs`. Logs are in `ev/`, screenshots in
`shots/`. The plan, approval and journal records were copied to `ev/records/`.
The rig's chip, calibrations, data, instance, sandbox home and Chrome profile
were deleted after.

**Night A: stop-loss** (`stoploss_target` 2, `stoploss_plan` 5, `max_delta`
resonator 1 MHz, `stop_by` +20 min), 10 steps on qA1-qA6:

1. Start showed the envelope (`a1_envelope.png`; the first layout bug, below,
   is visible there) and was approved.
2. qA1 +200 kHz applied (#2). The real replay applied (#3).
3. qA2 +5 MHz was **held**, and qA2 halted at once. Its next step was
   refused `target_halted`.
4. qA3: `host_unreachable`, then a fit failed, so it **halted after 2**. Its
   next step was refused.
5. qA4 and qA5 were `host_unreachable`. At 5 gate fails **the plan halted**:
   "disarmed: stop-loss: 5 gate fails in this plan (stoploss_plan 5)". The
   qA6 step was refused `stop_loss` and never ran (`a3_plan_halted.png`).

**Night B: stop_by** (`stop_by` +4 min):

1. qA4 applied; qA5 was `host_unreachable`.
2. The page was then CLOSED and the agent waited, its bridge PID alive.
3. `plan_done` `reason: stop_by` arrived at 23:00:03 with no page open (the
   watch).
4. The next step was refused `past_stop_by` (`b3_after_stop_by.png`).

**Night C: a person's Stop** (ask-all):

1. The agent's ask sent `needs_human` `run_request` (`c1_run_request.png`).
2. Allow run was clicked; the allowed run started; **Stop now** was clicked.
   The run came back `cancelled` "stopped now by human".
3. `plan_done` said `stopped`. **No `agent_failure` was sent**
   (`c2_stopped.png`).

**The webhooks received** (`ev/webhook.jsonl`, 11 in all):

| time | event | key payload |
|---|---|---|
| 22:53:55 | needs_human | `what: held_write`, plan A step 2, `halted_targets: [qA2]`, why "max_delta for resonator_spectroscopy: `qubits.qA2.resonator.f_01` 7394280195 -> 7399280195.0" |
| 22:54:14, 22:54:46 | agent_failure | `host_unreachable`, "QM host unreachable at 127.0.0.1:1 (connection refused)", plan A steps 4, 7 |
| 22:55:04 | plan_done | `reason: stop-loss`, counts {applied 4, applied_runs 2, held 1, failed 3, steps 10, done 4, skipped 2, cancelled 1, gate_fails 5}, `halted_targets` qA2 / qA3 with why, `link: /agent/summary?plan=pl-806073f6` |
| 22:55:04 | agent_failure | plan A step 8, `plan_halted: true` |
| 22:57:11 | agent_failure | plan B step 1 |
| 23:00:03 | plan_done | `reason: stop_by`, "the lab's stop time (23:00) was reached during plan `night B`" |
| 23:01:12 | needs_human | `what: run_request`, plan C step 0, `params: "step_hz=10000"` |
| 23:01:21 | plan_done | `reason: stopped`, "stopped by human" |
| 23:23:39 | needs_human | night D `held_write` (the final code) |
| 23:24:03 | plan_done | night D `reason: finished` |

The `plan_done` of night A body, verbatim:

```json
{"event": "plan_done", "chip": "rNight-bcc670be", "at": 1791035704.83,
 "payload": {"plan_id": "pl-806073f6", "title": "night A: qA1-qA6 resonator tune-up", "status": "stopped",
  "reason": "stop-loss", "why": "stop-loss: 5 gate fails in this plan (stoploss_plan 5) -- plan `night A: qA1-qA6 resonator tune-up` halted",
  "mode": "auto", "started_at": 1791035597.09, "started_by": "human", "ended_at": 1791035704.80,
  "counts": {"applied": 4, "applied_runs": 2, "held": 1, "held_writes": 2, "failed": 3, "steps": 10, "done": 4,
             "skipped": 2, "cancelled": 1, "gate_fails": 5},
  "halted_targets": [{"target": "qA2", "why": "its write is held for a person -- held: max_delta for resonator_spectroscopy: `qubits.qA2.resonator.f_01` 7394280195 -> 7399280195.0"},
                     {"target": "qA3", "why": "2 gate fails in a row (stoploss_target 2); last: fit: the node's own outcome for qA3 is failed"}],
  "link": "/agent/summary?plan=pl-806073f6"}}
```

(Line 4 of `ev/webhook.jsonl`; `at`, `started_at`, `ended_at` cut to two
decimals here.)

**The morning page (real clicks):**

1. "Night summary →" opened the newest plan. "night A" in Other plans opened
   night A (`s2_summary_nightA.png`): STOP-LOSS ended 22:55:04 (UTC+9),
   applied 4 in 2 runs, held 1, failed 3 steps, gate fails 5. Run #2 read
   "changed since -- no undo" (run #3 moved the value); run #3 offered Undo.
   Failed listed qA3 `host_unreachable`, qA3 `fit_failed`, qA4, qA5. Halted
   listed qA2 and qA3 with why (`f1_summaryA_failed_halted.png`).
2. **Undo** on run #3 staged the old values, and the sync control's **↑ Apply
   2** wrote them. Live `qA1.resonator.f_01` went 7126307283.14 ->
   7126244234, the value before run #3 (`s3_`, `s4_`).
3. **Write to chip** on the held qA2 write: "Written to the chip.", live
   `qA2.resonator.f_01` = 7399280195 (`s5_held_written.png`).
4. **SM restarted** (the server process killed and started again, chip
   reloaded). `GET /api/agent/summary?plan=<A>` was compared with the copy
   saved before the restart: plan, end, counts, applied, held, failures and
   halted were all **identical** (`ev/summaryA_after_presses.json` vs
   `summaryA_after_restart.json`). The page read the same
   (`r1_summary_after_restart.png`).
5. Open -> Back -> reload: intact (`r2_back_reload.png`). A full load of
   `/agent/summary?plan=<A>` too (`r3_full_load.png`).

**Night D, on the final code:**

- the envelope through a real Start (`d1_envelope_final.png`);
- the plan A card's step marks "qA2 halted" and "fit failed: qA3"
  (`d2_planA_step_badges.png`);
- a held write naming "step 0" (`d3_summaryD.png`);
- Undo: the re-read section keeps its times visible (`d4_undo_rereads.png`),
  and Apply restored `qB3`;
- Reject: "(0 waiting, 1 decided)" (`d5_rejected.png`);
- Back -> reload intact (`d6_back_reload.png`).

**Console errors: 0 in every phase.**

**Found in the browser and fixed** (commit `299146b0`, then `6cf4175c`):

1. The envelope's parts sat side by side. Pico lays `[role=group]` out as
   `inline-flex`, measured with `getComputedStyle` (`a1_envelope.png` vs
   `a1b_envelope_fixed.png`).
2. After an htmx re-read the summary's times stayed hidden. The swap
   REPLACES `#agent-summary`, and the per-swap localizer was handed the
   detached element.
3. A finished run whose fit failed, and a step skipped for a halted target,
   said nothing on the card's step row.
4. The held count read decided items as waiting.
5. The other-plans line ended with a separator.
6. A held write's approval had `step: null`, while its alert said step 2.

## Open (not done here)

- **`check_fit` is not part of the gate.** The autofit gates (bands, feature
  check, consistency) run only when an agent calls `check_fit`. Folding them
  into the stop-loss would be a new judgement on the run path, with its own
  false-refusal risk. That is a decision for the user, not a gap this cluster
  closes.
- **`plan_done` can arrive a moment before the `agent_failure` that tripped
  it** (night A, both 22:55:04): the halt sends from `after_step`, and the run's
  failure goes after it so it can say `plan_halted`.
- **A step cancelled by Stop now keeps `running`.** The plan is stopped first,
  and `_plan_step` never moves a closed plan (docs/253). Seen as the
  night C card's ▶ row. Pre-existing.
- **A step's "2 waiting" stays after its approval is decided.** It is the
  step's `approval` field; the card does not re-read the approval.
  Pre-existing.
- **`max_writes_per_plan` resets on restart.** `Registry.plan_writes` is in
  memory. A restart ends the grant anyway, so one plan's count cannot be
  bypassed.
- **At 390 px the app's sidebar fills the screen.** The summary page itself
  has no horizontal scroll (`scrollWidth` 390), but SM's global layout keeps
  the sidebar open at phone width (`f4_summaryA_phone.png`). Pre-existing,
  app-wide.
- **The diagnostics banner covers the top of every page on the rig chip**
  (its own `qB3` IF warning). Pre-existing; it is not in the way of any
  control used here.
