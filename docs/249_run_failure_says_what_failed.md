# docs/249: a failed run says what failed, and a crash leaves an honest state

2026-10-03, agent validation campaign, findings **A-12 / D-10 / C-09** (primary),
**D-13** and **A-14** (`D:\work\sm_qa_rigs\agent\FINDINGS.md`).

## Report

- **A-12 / D-10 / C-09.** The rig's chip points at `127.0.0.1:1` on purpose
  (connection refused). Every `run_node` failed after about 25-30 s and was
  classified `hardware_contention`: the journal said "hardware contention (the
  OPX is held elsewhere); not retried" and the agent was told "do NOT retry;
  tell the human". So the agent told the person the OPX was busy, when the QM
  host could not be reached at all, and it could wait for hardware that would
  never free up.
- **D-13.** After an SM crash, `run_node` refused with `queue_not_empty` and
  named SM's own dead row (`agent: 11_power_rabi (by_claude)`, status
  `running`). It told the agent a person had to clear it, and the only way out
  was `POST /api/agent/queue/clear`. Nothing was journaled and no webhook
  fired.
- **A-14.** The node process outlived the SM crash. After the restart the run
  read "interrupted" (classified `node_error`), the journal said nothing, and
  the session stayed armed with `run_key` still pointing at the dead run.

## The failure texts, measured

**(a) Host unreachable.** Measured by running
`QuantumMachinesManager(host="127.0.0.1", port=1)` in the `kriss_arbel` env
(qm-qua 1.4.1, 2.1 s) and the `cqt` env (qm-qua 1.4.0, 2.5 s). The exception
was:

    QmServerDetectionError: Failed to detect to QuantumMachines server, failed to connect to a cluster. Tried connecting to 127.0.0.1:1.

The qm logger printed the cause per target first:

    ... - qm - ERROR - Failed to detect to QuantumMachines server, ... Tried connecting to 127.0.0.1:1.
    Errors:
    127.0.0.1:1: [WinError 10061] No connection could be made because the target machine actively refused it

Source: `qm/api/server_detector.py` (`detect_server`, the message at :92-93
and the raise at :97 in 1.4.x; the same wording in every env on this machine,
qm-qua 1.2.6 to 1.4.1). Three more no-hardware variants give the same top
line, each with its own per-target reason:
- an unresolvable `qm-host.invalid:80`: `[Errno 11001] getaddrinfo failed` (0.0 s);
- a local listener that accepts and never answers: `timed out` (10.1 s with `timeout=10`);
- a local HTTP server that is not a QM gateway: `Server disconnected`.

Through a node, quam_builder wraps it. The rig run's `_result.json` error was:

    ConnectionError: Failed to connect to Quantum Machines Manager: Failed to detect to QuantumMachines server, failed to connect to cluster 'agent_rig_no_hardware'. Tried connecting to 127.0.0.1:1.

That wrapper is `quam_builder/architecture/superconducting/qpu/base_quam.py:294`:
`raise ConnectionError(f"Failed to connect to Quantum Machines Manager: {e}") from e`.

**(b) Hardware contention.** It cannot be produced without hardware held by
another quantum machine, so these are quoted from source.
`qualang_tools/multi_user/multi_user_tools.py` (`qm_session`, used by the lab's
nodes) opens with `close_other_machines=False` and matches the server's answer:
- `msg = "cannot be used because it isn't shareable in other QM."` (QOP2);
- `msg_opx1000 = "Resources already locked"` (QOP3);
- while waiting it logs `f"QOP is busy. Waiting for it to free up for {timeout}s..."`;
- when it gives up it raises `TimeoutError(f"While waiting for QOP to free, reached timeout: {timeout}s")`.

`qm/exceptions.py:233-235` adds `AnotherJobIsRunning`:
"Another job is running on the QM. Halt it first".

A plain `open_qm()` never reports contention. Its default
`close_other_machines=None` "is currently the same as `True`"
(`quantum_machines_manager.py`, `open_qm` docstring). The QM docs agree,
`Guides/multi-users.md`: "By default, opening a `qm` closes any existing
`qms`." So contention shows up only through `qm_session` (or an explicit
`close_other_machines=False`).

None of these strings matched the old `_HARDWARE_RE`. A real contention
give-up (`While waiting for QOP to free, reached timeout: 100s`) fell through
to `node_error`, because "reached timeout" is not "timed out". The old class
was wrong in both directions.

**(c) Timeout.**
- `qm/api/base_api.py:38-39`, `timeout_error_message`: "A timeout of
  {timeout} seconds was reached. The timeout value can be configured ...". It
  is raised as `QMTimeoutError` on gRPC `DEADLINE_EXCEEDED` or `TimeoutError`
  (:64, :73).
- `qm/api/v2/job_api/job_api.py:299`: "Job {id} did not reach any state of
  {state} within {timeout} seconds".
- SM's own: the scheduler's "timed out after {n}s" and run_node's
  "timed out after {n}s (run_node timeout_s)".
- Any other gRPC error is `base_api.py:66` "Encountered connection error from
  QOP: details: ..., status: ...". That is a dropped link, so it is now read
  as unreachable, not as a timeout.

## Behaviour now

**One classifier, read in a fixed order** (`agent_runs.classify`). Contention
comes first (its own give-up is a `TimeoutError`), then `host_unreachable`,
then `timeout`, then `node_error`. Every literal is quoted, with its file, in
the comment above the regex.

| class | matches (the run's own words) | `retry` | agent / person text |
|---|---|---|---|
| `hardware_contention` | `isn't shareable in other QM`, `Resources already locked`, `While waiting for QOP to free`, `Another job is running on the QM`; a `QOP is busy. Waiting` with no `Opening QM` after it; old wording kept: `another job/program/qm is (already) running/open`, `qm is closed`, `quantum machine … closed`, `job queue is full`, `opx … busy`, `controller/resource(s) … in use` | `after_it_ends` | "the hardware is busy: another quantum machine or job holds what this node needs (waited 100s for it to free)"; how: "… do NOT retry now; tell the human" |
| `host_unreachable` (new) | `Failed to detect to QuantumMachines server`, `Failed to connect to Quantum Machines Manager`, `Encountered connection error from QOP`, `StatusCode.UNAVAILABLE`, socket causes (refused, WinError 10061/10060/10065/10051, Errno 111, getaddrinfo, name resolution, no route, unreachable network, httpx/httpcore Connect*); old wording moved here: `failed to connect to qm`, `connection refused`, `could not connect to qop/host`, `grpc … unavailable` | `no` | "QM host unreachable at 127.0.0.1:1 (connection refused)"; how: "the node could not reach the QM server at 127.0.0.1:1, cluster '…' (connection refused), so it never reached the hardware and nothing was applied. Check the network and the chip's network config (host, port, cluster_name). Do NOT retry until that is fixed …; tell the human" |
| `timeout` | SM's `timed out after N`, qm's `A timeout of N seconds was reached`, `did not reach any state of … within`, `deadline exceeded`; old wording moved here: `timed out waiting for the opx/job/qm` | `after_check` | the error's first line; how: read the log tail before running it again |
| `interrupted` (new) | a restart found the run in flight | `after_check` | see A-14 |
| `node_error` | anything else | — | (no advice; the error and log tail as before) |

Every old `_HARDWARE_RE` alternative is accounted for. A connect failure moved
to `host_unreachable`, a timeout wording moved to `timeout`, and every real
contention wording stayed `hardware_contention`.

**The target and the cause come only from the text.**
- `unreachable_detail` takes the target from `Tried connecting to <host:port>`
  (or a urllib3 `host='…', port=N`) and the cluster from `cluster '…'`.
- It takes the cause from the detector's own per-target line, which is
  mapped to one of: connection refused / the host name did not resolve / no
  network route to the host / no answer, timed out / something answered
  there, but not a QM server.
- A cause the text does not state is left out, never guessed.
- When the text names neither a target nor a cause (for example a credentials
  failure inside the quam_builder wrapper), the line quotes the error instead
  of claiming an address.
- A mid-run drop (`Encountered connection error from QOP`) reads "lost the
  connection to the QM server … dropped while the node ran", never "never
  reached the hardware".

**SM's own time limit during a busy wait is contention.** If `qm_session`
logged `QOP is busy` and never `Opening QM`, a run ended by run_node's
`timeout_s` is classified `hardware_contention`, not `timeout`.

**Where the words go.**
- `result["failure"] = {what, how, retry, target?, cluster?, cause?}` is set
  on every failed run. The agent reads it through `run_node` / `run_wait`
  today.
- The journal line ends "— QM host unreachable at 127.0.0.1:1 (connection
  refused); check the network / host config; not retried".
- The `agent_failure` webhook payload gains `what`.
- The run card (`agent.js`) shows `failure.what` in bold above the raw error.

**D-13: SM's own leftover rows.**
- `leftover_rows(queue_state)` finds queue rows whose `state_path` is a
  run_node scratch (`<instance>/agent_runs/<key>/quam_state`, created only by
  `make_scratch`) and whose run meta beside it is no longer
  `starting`/`running`. A restart's scan marks it `interrupted`.
- **Orphan**: the row is `running` and the persisted worker PID is alive.
  `check_gates` now refuses `orphan_running` and names the row, the PID and
  "Wait until it ends -- this clears itself then". Before, it said
  `queue_not_empty`, which this was not.
- **Dead**: the row is queued, or running with its process gone. It no longer
  blocks. Before the run queues its own row, `sweep_leftover_rows` removes it
  under the queue lock, re-deciding there, and drops the dead process's run
  claim. One `sm` journal line says so: "removed 1 leftover row(s) from the run
  queue (`…`): SM's own, from run(s) whose driver ended with a previous SM
  process".
- The sweep matters as much as the gate. `scheduler.start()` re-queues a
  stale `running` row, so without the sweep the dead row would RUN FIRST under
  the agent's click.
- These are never touched: a person's rows, a row whose meta another driver
  still holds, anything while this process's runner is live, and anything
  while another live SM window owns the run (`foreign_owner`).

**A-14: the interrupted run is said once.**
- `_drive` keeps the worker PID in the run's meta.
- `Registry.start` keeps `chip_name` (the journal's name) and `scope` in the
  meta.
- On restart the scan rewrites the meta as `interrupted` FIRST: class
  `interrupted`, an error that names a still-alive node process ("its node
  process (PID N) was still running at the restart and may still be driving
  the OPX -- SM will not collect what it writes"), and `failure`. Then it does
  three things, once:
  1. If the session's `run_key` is still this run, it clears `run_key` /
     `claimed_by_tool` / `worker_pid`. It disarms only if the arm predates the
     run, so a person who re-armed since keeps the arm.
  2. It writes one `sm` journal line: "✗ ran `node` on targets interrupted:
     …; nothing was applied; the session was disarmed -- a person arms it
     again".
  3. It sends one `agent_failure` webhook, from a thread so a dead URL never
     stalls the request.
- A second restart repeats none of it, because the meta no longer says
  `running`.
- No journal name in the meta means no journal line. SM never guesses a name;
  the chip key or the folder name could be a different journal.

## Verified

**Real path, isolated rig.** Rig `D:\work\sm_qa_rigs\agent\rRC` (made by
`make_rig.py rRC 5113`), served on port 5113 from this worktree, with its own
instance. Before every `run_node`, the network was asserted to be
`127.0.0.1:1`. The `kriss_arbel` env ran the real chassis, the real
`run_experiment.py` and a real qualibrate node. Evidence is in
`rRC\ev\trace.jsonl`, with the scripts beside it.
- `01_time_of_flight_mw_fem` on qA1 took 27 s. It came back
  `host_unreachable` with what "QM host unreachable at 127.0.0.1:1 (connection
  refused)", cause taken from the real log tail. The journal line is as above.
- **Crash.** The SM server process was killed 1 s into a run, after its
  command line was verified. Its node process (PID 28992) survived, and the
  queue kept the row `running` with that worker PID.
- **After the restart:**
  - the meta said `interrupted` and named PID 28992 as possibly still driving
    the OPX;
  - the session was disarmed (`start_token`/`run_key`/`claimed_by_tool` null);
  - the next `run_node`, after a re-arm, was refused `orphan_running`, naming
    the row and PID 28992 (before the fix: `queue_not_empty`);
  - once that process exited, `run_node` swept the row (journal: "removed 1
    leftover row(s) …"), ran, and came back `host_unreachable`;
  - the queue ended empty.
- **The two `agent_api.py` edits below,** applied at runtime by
  `rRC\srv_shim.py` (no file edited):
  - the interrupted line appeared exactly once: "✗ ran
    `01_time_of_flight_mw_fem` on qA1 interrupted: SM restarted while this run
    was in flight; its node process (PID 28992) was still running … nothing
    was applied; the session was disarmed -- a person arms it again";
  - a finished run's top-level `how` was the host_unreachable sentence.
  - Without the shim, the journal line is skipped, because no name reaches the
    meta.

**Pins.**
- `tests/test_run_failure_class.py` (36): the measured texts as a table, the
  detail extraction, the connect-vs-drop wording, run_node end to end (class,
  failure, journal, index, webhook), the busy wait under SM's time limit, the
  worker PID kept, the D-13 rows (dead running/queued, live orphan, a row
  another driver holds, a live foreign owner, a person's row), and A-14 (once,
  re-arm kept, live PID named, no guessed journal name, start keeps the name).
- `tests/test_agent_runs.py`: `test_classify` and
  `test_hardware_contention_is_named_and_not_retried` now use the real
  wording.
- `tests/agent_panel_selfcheck.cjs`: the card shows what failed above the raw
  error (+2 assertions).
- **Mutations: 28/28 red** (`rRC\ev\mutate_249.py`; every mutated source
  compiles). The mutations covered: unreachable filed as contention;
  busy-wait not read; an opened QM still read as busy; the SM time limit
  overriding contention; qm_session's give-up / QOP3 lock not contention; the
  target not extracted; a mid-run drop read as never connected; no `failure`
  on the result; no journal naming; no `what` in the webhook; a dead row still
  blocking; the dead row not swept (it ran first); a live orphan read as dead;
  a driven row read as dead; a live foreign owner ignored; the interruption
  not announced; a re-arm taken back; not once; a live PID not named;
  `interrupted` filed as `node_error`; the worker PID not kept; the journal
  name not kept; a guessed journal name; no webhook; an invented cause; an
  address claimed when the text names none; the card not showing what failed.
- Related suites (`cqt`): agent_backend, agent_may_change, agent_panel,
  agent_runs, agent_setup, autofit_realbackend, baseline_normalize, chat_api,
  limits, multi_instance, natural_order_rest, newrun_path,
  predelivery_audit_fixes, run_failure_class, run_node_isolation, runner_p4,
  runner_p8, scheduler, scheduler_queue, scheduler_scope, agent_api, journal,
  hook, agent_pill, agent_firstuse, node_persist, plus the lab-name and
  English-only guards (knowledge_pack, cust_0930_ui). Result: 725 passed, 2
  skipped, 1 failed. The failure is
  `test_scheduler.py::TestSettings::test_defaults_when_absent`; it is
  pre-existing and order-dependent, see Open.

## Edits still needed in files this fix may not touch

These are for the coordinator after merging the in-flight fix. Line numbers
are as of origin/main 4bde3837.

1. `quam_state_manager/web/agent_api.py`, `_run_view` (:1480-1481). Before:

       elif res.get("classification") == "hardware_contention":
           out["how"] = "the OPX is held elsewhere (hardware contention): do NOT retry; tell the human"

   After:

       elif (res.get("failure") or {}).get("how"):
           # docs/249: the run's own failure class says what failed and what to do
           # (host_unreachable / hardware_contention / timeout / interrupted)
           out["how"] = res["failure"]["how"]

   Without it, a `host_unreachable` or `interrupted` run has no top-level
   `how` (the text is in `result.failure.how`). Contention keeps "do NOT
   retry".
2. `quam_state_manager/web/agent_api.py`, `_run_adapter`, the `return
   agent_runs.RunAdapter(...)` (:1467-1470): add `chip_name=name` (the local
   `name = _chip_name()` at :1395). Without it, A-14's journal line is skipped.
   The disarm, the webhook and the honest meta work without it.
3. `quam_state_manager/mcp.py`, the `run_node` tool description (:365). Before:
   `"run_wait with the key. Never retry on hardware_contention."`. After:
   `"run_wait with the key. Never retry on hardware_contention or host_unreachable; the result's failure.how says what to tell the human."`
4. `quam_state_manager/web/routes.py`: nothing. It does not enumerate the
   classes.

## Open

- A failed unreachable run still costs about 25-30 s (27 s measured). The
  QMM detection is about 2 s; the rest is the env's imports and the node's
  setup. A pre-flight probe of `network.host:port` before spawning would save
  that. It is not done here: a custom/cloud QMM (`qmm_class` + `qmm_settings`)
  has no plain host:port to probe, and a probe that is wrong for such a chip
  would be a new false refusal.
- The contention texts are quoted from client source, not reproduced. That
  needs hardware held by a second quantum machine. The server-side physical
  error that `qm_session` matches (`Resources already locked`) was not
  observed.
- `/api/agent/queue/clear` still has no button. With the sweep, SM's own rows
  no longer need it; a person's leftover rows still do.
- The restart scan still flips every in-flight meta to `interrupted`, including
  one another LIVE SM window on the same instance is driving (pre-existing).
  The D-13 sweep is guarded against that case by `foreign_owner` once that
  window has claimed the run. A row between `add_item` and `start()` in the
  other window is a sub-second race, left as is.
- Pre-existing, not from this change:
  `test_scheduler.py::TestSettings::test_defaults_when_absent` fails when it
  runs after `test_scheduler_queue.py` in one session. Both write and read
  `_shared.json` at `Path(scope).parent`, which for a `tmp_path` scope is the
  session's shared basetemp. It reproduces with those two files alone, and
  neither file nor `scheduler.py` is touched here.

## Follow-up at integration: the agent-facing edits

The three edits this fix could not make (another fix owned those files) were applied at integration:
- `agent_api._run_view`: a failed run's top-level `how` is now the run's own `failure.how`. It used to be the contention advice for one class and nothing for the rest.
- `agent_api._run_adapter`: passes `chip_name`, so an interrupted run is journaled.
- `mcp.py` `run_node`: the description now says never to retry on `hardware_contention` or `host_unreachable`, and that `failure.how` says what to tell the person.

**Pin:** `test_an_unreachable_host_is_not_called_contention_at_the_top_level_either`. Dropping either `agent_api` edit turns it red (2/2).

