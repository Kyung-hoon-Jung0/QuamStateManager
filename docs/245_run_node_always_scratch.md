# docs/245: an agent's run_node is always pinned to its scratch, and proposes only the node's own writes

2026-10-03, agent validation campaign, P0 findings **A-01 / D-01** and **D-02 / A-02**
(`D:\work\sm_qa_rigs\agent\FINDINGS.md`).

## Report

- **D-01.** An offline replay through MCP `run_node` saved into the LIVE chip.
  The node log said "Saving machine to active path <rig>\chip". Live got 34
  leaves, including `network.host = 10.1.1.6` (a real cloud) and a
  Customer_Codes `data_folder`, with no approval. The agent was told "297 writes
  wait for approval". Any `node.save()` did the same; it was not replay-specific.
- **D-02.** The same replay proposed the stored run's whole snapshot as the
  node's writes: 297 writes, of which the node made 2. In auto mode with Dry run
  OFF a crafted run applied `network.host` to live.

## Root cause

- **D-01.** `core/scheduler.py:1623-1625` (origin/main) passed `--config-file`
  only from `settings.effective_config`. That setting stays empty until someone
  presses "Read config". Only that flag made `generator/run_experiment.py:201-213`
  repoint the qualibrate config's `[quam] state_path` at the scratch. The
  framework's save takes `[quam] state_path` over `QUAM_STATE_PATH`, so without
  the flag it saved to the chip the env's config names: the live chip. Isolation
  depended on a cached UI setting.
- **D-02.** `run_experiment._persist_node_state` (origin/main :316-354) called
  `machine.save()` on whatever machine the node held. `agent_runs` then
  leaf-diffed the scratch against `before/` (`agent_runs.py:542`). After
  `load_from_id`, `node.machine` is the stored run's machine, so the diff was
  that whole snapshot. In this lab's data, node.json has no `patches`, so the
  node's own update has to be captured in-process.

## Behaviour now

**D-01: pinned, or refused.**
- A queue item with its own `state_path` (only `agent_runs` creates one) is
  spawned with `--isolate` (and `--replay` when `load_data_id` is set). This does
  not depend on `effective_config`.
- `--isolate` sets `MPLBACKEND=Agg` first. The pin imports qualibrate, which
  imports matplotlib, and the rig hung in tkinter when this order was reversed.
- `_pin_config_strict` then writes `qualibrate_config.scratch.toml` next to the
  scratch. Its source is the verified config when there is one, otherwise the
  config this env resolves itself (`QUALIBRATE_CONFIG_FILE`, then
  `~/.qualibrate`). The copy is project-merged, so storage and library settings
  from a project overlay survive. Its `[quam] state_path` is set to the scratch.
- The copy is then re-read through qualibrate's own resolver. If the resolver
  does not name the scratch, the run is refused before the node file is
  imported: `isolation refused: ...` goes into `_result.json`, and the run
  fails with that reason. A missing config is refused the same way.

**D-02: the scratch is its pre-run bytes plus the node's own writes.**
- The run records the scratch's bytes before the node starts.
- An in-process recorder wraps two `QualibrationNode` members for the run and
  restores them afterwards:
  - `record_state_updates`: the machine dict before and after each block.
  - the `machine` property setter: both the constructor's
    `machine=Quam.load()` and `load_from_id` assign through it.
- After the run, the pre-run bytes are restored and exactly ONE source of writes
  is applied on top:
  1. **Any `record_state_updates` block** (plus `node.state_updates` in the
     `interactive_only=False` style): the node's declared update. Changes the
     node made to its machine outside every block (measurement scaffolding)
     are not proposed. They are named in the log: "N change(s) ... OUTSIDE its
     record_state_updates block(s) are NOT proposed: <paths>".
  2. **No block**: every change the node made to its machine since that machine
     was last assigned. 134 of this lab's 186 nodes use a block. Nodes like 17d
     (`node.machine.flux_crosstalk_dc = ...`) and 15e do not. Their writes are
     still proposed, and in a replay the swapped-in snapshot is the baseline,
     so it is never counted.
  3. **Nothing observable** (qualibrate not importable, or the machine was not
     set through `node.machine`):
     - a normal run falls back to the docs/173 S9 save plus the docs/174
       phantom-root strip, because its machine came from this scratch;
     - a replay proposes **nothing**, and says so in the log, because its
       machine is another run's.
- A key the node removed is a delete. For example, a pulse replaced by another
  class drops the old class's fields; keeping them would make the state
  unloadable. A resized list is one whole-list write, as before.
- Anything else the node's own save left in the scratch is discarded and
  counted in the log, with up to 8 paths: "rewrote N other leaf(s) ...
  discarded, NOT proposed". That covers serializer defaults, a replay's stored
  snapshot, and a second Quam object saved by hand. A write that bypassed
  `node.machine` is therefore never dropped silently. The agent sees the log in
  `log_tail`, and the same report is in `_result.json["isolation"]`.
- A node that raises restores the pre-run bytes, so nothing is proposed. A
  node that calls `sys.exit(0)` counts as success (main's contract), and its
  writes are kept.
- Phantom roots no longer reach an isolated scratch, because only deltas are
  applied onto the original bytes.

**A person's plain Runner run is unchanged: it writes the chip, by contract.**
An item without its own `state_path` runs against `settings.quam_state_path`
with no `--isolate`. With a verified config, the docs/174 repoint applies as
before. Without one, the node's own `node.save()` lands wherever the env's
qualibrate config points: normally the chip the person chose to run on. Only
the agent path (`run_node`) carries the always-scratch guarantee.

## Verified

Rig `D:\work\sm_qa_rigs\agent\rD`, port 5097, served from `D:\work\sm-runfix`
by `rD\srv_fix.bat`. The instance dir is `rD\inst_fix`, so stream D's `rD\inst`
evidence stays untouched. The log goes to `srv_fix.log`. Setup:
- `POST /load folder=rD\chip`;
- `POST /scheduler/settings {env_python, calibrations_folder}`, without reading
  the effective config;
- arm without the agent header;
- MCP bridge with `PYTHONPATH=D:\work\sm-runfix`.

Before every `run_node`, the driver asserted that `wiring.json` has
`network.host == 127.0.0.1`, port 1.

Live SHA-256 before and after both runs:
- `state.json` `4cd7bafdb8b21dc0b667b7bff6822ec881e61a2f33526ea9ae86ee6d800b344e`
- `wiring.json` `92316ec9c1544eee97b69b7c8cb8db51ac058e47d77e31da4c46035e19bd4a21`

Both were unchanged after both runs, and a 0.3 s poll saw only one
(mtime, size) state for the live files.

- **Replay**: `run_node 03_resonator_spectroscopy_single qA1
  params={"load_data_id":1}`, mode ask-writes.
  - Status `done`. Qualibrate logged "Saving machine to active path
    ...\inst_fix\agent_runs\20261003-172655-7a72bf\quam_state", the scratch.
  - **2 writes** were proposed and held for approval:
    - `qubits.qA1.resonator.f_01` 7126044234 -> 7126307283.14
    - `qubits.qA1.resonator.RF_frequency` 7126043000 -> 7126307283.14
  - The log says "the node's own save rewrote 281 other leaf(s) ... discarded".
  - The scratch `wiring.json` is byte-identical to `before/`
    (`92316ec9…`), so network is untouched.
  - Before: 297 writes, including network.
- **Non-replay**: the same node with no params, after the approval was
  rejected.
  - It failed at QM connect: "Tried connecting to 127.0.0.1:1".
  - 0 writes, live unchanged.
  - The scratch is restored byte-identical to `before/` (`4cd7bafd…`,
    `92316ec9…`).

Evidence: `rD\ev\p0_evidence\docs245\`.

## Pins

`tests/test_run_node_isolation.py`, 23 tests, using the real qualibrate and
qualibrate_config resolvers of the cqt env. Every one of these 15 mutations was
caught:
- `--isolate` never passed;
- no strict pin;
- the pin not verified through the resolver;
- `MPLBACKEND` set after the pin;
- the whole-machine save restored as the proposal;
- machine assignment not observed;
- no deep copy of the machine dict, so `to_dict` aliasing hides changes;
- removed keys not deleted;
- a bypassing write not disclosed;
- `sys.exit(0)` treated as failure;
- the recorder not uninstalled;
- outside-block changes not named;
- a failed node not restored;
- `node.state_updates` ignored;
- an unobservable replay falling back to the whole save.

Related suites still pass: agent_runs, baseline_normalize, node_persist,
scheduler, scheduler_queue, scheduler_scope, runner_p0/p8, agent_api,
agent_panel, actor_plumbing, agent_backend, limits, chat_api, agent_setup,
agent_firstuse and agent_may_change.

## Not covered

- A config with relative paths: the copied config lives next to the scratch,
  so relative paths in it would resolve differently. No lab config seen uses
  them.
- A node that changes state through something other than `node.machine` (a
  second Quam object saved by hand) is reported, not proposed. Proposing it
  would mean trusting the save's footprint, and that footprint is the D-02 bug.
