# 315 Node simulate (cloud simulator)

"Simulate a node" builds the QUA program of one calibration node on a copy of the chip and simulates it on the QM SaaS
cloud simulator, with no hardware and no live write. It is the Simulate button and panel (`/nodesim`). P1 is the cold
click: one child process per click that logs in, spawns an instance and closes it. P2 is the warm path: one worker per
(env, library, chip) that keeps its imports, plus one broker per user that owns the SaaS instances. The decisions are in
`D:/work_laptop/study/2026-11-20_node-simulate-feasibility/decisions.md` (ids `D-n`, cited here, not restated);
the P2 spec is `.../p2/spec.md` (cited `P2 s.n`); the hard rules for builders are `.../briefs/SAFETY.md`. Code facts
below are from branch `feat/nodesim-p2-int` (worktree `_wt/nodesim_int`, commit a745ef18); line numbers drift, so
grep the names.

Files. SM side: `core/nodesim_click.py` (lines, answers, cache, `Manager`), `core/nodesim_pool.py` (worker client,
broker client, `WarmPool`), `core/nodesim_spawn.py` (pinned env, scratch), `core/nodesim_oracle.py`,
`web/nodesim_routes.py`, `web/static/nodesim.js`, `web/templates/nodesim.html`. Lab side (run in the chip's env, never
imported by SM except the stdlib-only `nodesim_proto.py`): `generator/nodesim_click.py` (cold child),
`nodesim_warm.py` (warm worker), `nodesim_broker.py`, `nodesim_proto.py`, `nodesim_guard.py`, `nodesim_warm_guard.py`,
`nodesim_build.py`, `nodesim_oracle.py`.

## 1. What the user sees

- **Page and button.** `/nodesim` (nodesim_routes.py:218) renders `nodesim.html`, a `[data-nodesim]` mount that
  `nodesim.js` fills: node picker, targets, window (10 / 20 / 50 / 100 us, default 20, `WINDOWS_US` in
  nodesim_click.py), the Simulate button, a phase strip, then the result. The report is drawn by SM's own timeline
  (D-23) with no samples (D-20): "Report only, no samples".
- **Every answer is `{state, line, ...}`.** `line` is built on the server (`line`, nodesim_click.py:155; `answer`,
  :226) and shown verbatim by the page (nodesim.js header: the only client-worded lines are NO_ANSWER, REFUSED_PICK,
  NO_READOUT). Never re-word a line in JS; add the state to `line()` and to `SHOW` in nodesim.js. The full table of
  P2 lines is P2 s.1; the tests pin the text.
- **Honest lines.** `refused` ("This node is not simulated here: <reason>", D-1), `blocked` (qm-saas older than 1.2.0
  or missing in the chosen env, with the upgrade command; SM never installs, D-16), `chip_changed` ("chip changed since
  - re-simulate", D-26, never a silent swap), `too_long`, `guard` ("Stopped by the safety guard: ..."), `timeout` (the
  instance was closed).
- **Warm states.** `warming` ("Preparing the node simulator in env <env> (about 5 s)."), `starting` with the broker's
  count ("Starting a cloud simulator instance (about 62 s); n of 3 instances of this account in use on this PC."),
  `instance` strip (close time, "closes after 10 min without a click"), `no_slot`, `spawn_failed`, `broker_failed`,
  `needs_instance` + "Start new instance" (cluster hash changed, D-13), `renew_offer` + "Renew simulator", `renewing`,
  `expired_retry`, `worker_crashed`, `worker_restart` ("The cloud instance is kept."), `busy_chip`, `lab_busy`,
  `build_watchdog`, `stopped` / `stopped_kept` / `stopped_spawning`, `idle_closed`, `cold_fallback` note.
- **Counts are local.** "n of 3 in use on this PC" counts only lease-table rows; other computers' instances are
  invisible (P2 s.1, C-8). A server refusal is `spawn_failed`, never `no_slot` (P2-7).
- **Phases.** Warm click: `copy lock build lease connect submit wait report` (`PHASES`, nodesim_proto.py:35).
  Cold click: `copy build login spawn connect submit wait report close` (nodesim.js `PHASES`). A cache hit shows
  `copy`, `cache` and never reaches a worker (D-22; `cache` is not a worker phase).
- **Warm setting.** `/api/nodesim/settings` (nodesim_routes.py:59), label "Keep the cloud simulator warm", off by
  default. Off: the worker starts when the panel opens (imports only), an instance is requested on the first explicit
  intent (node picked or Simulate pressed). On: the instance is requested when the panel opens (D-12). Stored as
  `{"keep_warm": bool}` by `WarmPool.setting` (nodesim_pool.py:530); anything but a boolean is refused.
- Other doors: `/api/nodesim/nodes|start|status|stop|script|save-script|intent|instances|renew|new-instance`
  (all in nodesim_routes.py). The page polls `status`; the server stops a run nobody asks about.

## 2. Process architecture

```
SM process
  routes -> click.Manager (cache, answers) -> WarmPool (core/nodesim_pool.py)
                                                 |  one WorkerClient per key, at most 2 per SM (P2 s.2.5)
                                                 |  one BrokerClient (named pipe, JSON lines)
                                                 v
                     broker (python -I -B nodesim_broker.py): credentials, login session,
                       lease table <broker dir>/leases.json, 3-instance limit, reaper
         warm worker (python -B nodesim_warm.py), key = sha256(env python, library root, chip live folder)[:16]
              guard + imports kept, scratch <tmp>/sm_nodesim_warm/<key>-<sm pid>/, chip copy in <scratch>/chip
         cold child (generator/nodesim_click.py): P1 path, one process per click, fallback only (P2 s.2.7)
```

- **Cold child (P1).** `Manager` spawns `generator/nodesim_click.py` per click with the pinned env of
  `nodesim_spawn.pinned_env` (nodesim_spawn.py:15, D-7) and reads `_status.json` / `_result.json` from the work folder.
  It logs in itself and holds the password for that one click (D-11 exception). It stays the fallback: only when the
  worker gave no `ready` within 60 s or the broker cannot start; never after `no_slot`, `login_failed`,
  `account_mismatch`, `spawn_failed`, `lab_busy` or a guard verdict (P2 s.2.7; test_nodesim_warm.py
  `test_s15_only_allowed_cold_triggers`).
- **Warm worker.** One per key, started with the chip's own env only after `known_interpreter`, the qm-saas probe
  (`probe_qm_saas`, nodesim_click.py:329) and the library-root check (D-12, D-15, D-16). It exits at once, before
  importing the library, if any `SAAS_*` variable is in its environment (`bye why saas_env`; D-11). It never logs in and
  never talks to the broker: SM relays the lease to it over stdin (D-11, P2 Q-2). One click at a time (D-18); idle exit
  after 20 min; killed above 2 GiB RSS; a lab-module edit restarts it with the lease kept (D-5).
- **Per click (P2 s.2.2).** SM rewrites `<scratch>/chip` from `chip_copy.make_chip_copies` and writes
  `<scratch>/_snapshot.json` `{seq, store_id, click_id}` (D-14) beside, never inside, the chip folder (QUAM loads
  every json in it); the worker checks all three before building. SM takes `machine_lock` on the
  worker's lock thread before sending `click` and releases it on `build_done`, with a 30 s watchdog that kills the
  worker (D-18, nodesim_pool.py:191-205). The lock thread holds no other SM lock while it waits.
- **Broker (D-17).** One per user account on the PC, started by SM with plain `subprocess.Popen` (not the lab worker
  spawn, whose kill-on-close Job would kill it with SM). It imports only `qm_saas` and the stdlib, loads
  `nodesim_proto.py` and `core/xlock.py` by file path, and takes `broker.lock` first (a second broker exits with
  `already_running`). Endpoint: a named pipe with `broker.key` as authkey; only `send_bytes` / `recv_bytes` of JSON,
  never `recv()` (it unpickles). Broker dir: `%LOCALAPPDATA%/quam-sm/saas-broker/`, tests set `QSM_SAAS_BROKER_DIR`
  (`broker_dir`, nodesim_pool.py:329). It exits after 60 s with no SM and no live / pending / closing row.
- **Lease table.** `leases.json`, written by the broker only, under `xlock`. Row states `pending live closing ghost`;
  the row holds ids, times, pids with creation times, cluster hash; never a token, password or header (D-11; format in
  P2 s.3.2). A pending row is written before each spawn call (D-17a). The reaper (start + every 30 s) closes by id
  every row whose owner SM is gone, adopts a live row whose broker is gone, turns a pending row older than 240 s into
  a `ghost` that counts against the 3 slots until pending_at + 960 s, and drops expired rows (D-17b, P2 s.3.3).
  Close is always by id (`close_simulator`), never `close_all`, once per id; a failed close is retried every 30 s.
- **Close and renew triggers.** Idle over 10 min, expiry, cluster-hash change, worker death in a job, Stop or timeout
  in `submit` / `wait` (the instance then holds a job of unknown state), a Stop on the click that spawned it, SM exit
  (D-17c). Renewal under 150 s left spawns a replacement and swaps between clicks; else `renew_offer` (D-17d, P2 s.3.4).
  A pipe close is not an SM exit: the broker checks the owner pid and creation time before closing anything.
- **Cache (D-22).** In the SM process (`Cache`, nodesim_click.py:481, 20 entries, LRU, report + script only), keyed
  by `input_key` (nodesim_click.py:463) before the build and stored under `cache_key` (:456); it survives a worker
  restart. Results are never written to the real data root or Datasets (D-28).

## 3. Protocol (generator/nodesim_proto.py)

- **Framing.** JSON lines, `PROTO = 1` (nodesim_proto.py:9). Worker lines carry the marker `@@NODESIM1 ` (:10); SM ->
  worker is stdin; worker -> SM is a protocol fd duplicated from stdout, with fd 1 and 2 pointed at a capture file so
  node prints never reach the protocol. `decode` (:159) ignores lines without the marker and raises on marked garbage;
  an unmarked line, bad JSON or an unknown `ev` from a worker kills it (test_nodesim_warm.py
  `test_s10_unmarked_protocol_is_fatal`). Broker lines use the same schemas over the pipe.
- **One table, one validator.** `WORKER_OPS`, `BROKER_OPS`, `WORKER_EVENTS`, `BROKER_EVENTS`, `OP_FIELDS`,
  `EVENT_FIELDS` and `validate` / `encode` / `decode` (:11-:160) are the contract; every message is validated on both
  sides. A new op, event, field, `RELEASE_WHYS` / `CLOSED_WHYS` / `BYE_WHYS` value or error code goes here first, then
  into P2 s.4 and `tests/test_nodesim_proto.py` (`test_contract_table_coverage` checks the table against the spec).
- **Worker ops.** `hello cluster lease drop_lease click stop exit`; events `ready phase build_started build_done
  need_lease result guard idle bye` (P2 s.4.2-4.3). After a `guard` event the worker sends `bye` and exits: it is never
  reused after a verdict. A worker `hello` with another `proto` gets `bye why proto_mismatch`.
- **Broker ops.** `hello acquire touch in_job cancel release status bye swapped`; replies `hello_ok`, `spawning`,
  `lease`, `no_slot`, `spawn_failed`, `cancelled`, `closed`, `status`; pushes `renew_offer`, `replaced`, `closed`.
  `hello` carries the credentials once; it is the only message with a password (P2 s.4.5).
- **Request matching.** Every `acquire` carries a fresh `req` (uuid4 hex, nodesim_pool.py:690). `BrokerClient.request`
  (:454) keeps one request in flight under a lock, drops any answer whose `req` is not its own (:475; D-48), lets a
  Stop send `cancel` for the same `req`, and takes only a `status` event for a status call. Worker answers are matched
  by `click_id` and by `lease_seq`; `check_snapshot` (nodesim_proto.py:174) compares `seq`, `store_id` and `click_id`
  (D-14). Foreign answers are dropped, not trusted (test_nodesim_warm.py `test_p2_1_foreign_answers_are_dropped`).
- **Shared functions (contract 10).** `cluster_spec`, `cluster_hash`, `cluster_config` (:184-:218) are called by
  worker, cold child and broker; `is_allowed_saas_host` (:219) is the one host rule, called by broker, SM and
  `WarmGuard.replace_lease`: never port 9510 / 19510, never a non-global address; only the three documentation ranges
  are exempt (tests use them).

## 4. Safety barriers and the tests that pin them

The safety is the stub (D-3) plus the guard (D-8) plus the pinned env (D-7), not the refuse-list (D-1). `Guard`
(nodesim_guard.py:34) is an audit hook plus class barriers; `WarmGuard` (nodesim_warm_guard.py:8) adds the phases
`idle` / `build` / `sim`: network only to the leased instance and only in `sim`, and `check_barriers` verifies by
identity that the barriers were not replaced (verdict `GUARD FAIL barrier <name> replaced`). The audit hook sees
Python-level calls only; the class barriers and the decoy host (D-2) carry the C-level case (D-8c).

- **Hardware.** Wiring, octave and QDAC hosts are replaced by decoys (D-2); `open_qm`, `execute`, `Quam.connect` and
  a foreign `QuantumMachinesManager` raise; socket allow-list; one host rule; `QDAC_DISABLE=1`. Pinned by
  test_nodesim_fi.py `test_guard_rows` (FI-1a, 1b, 1c, 1e, 1f), `test_real_transport`, `test_config_rows` (FI-5),
  `test_sanitizer`; test_nodesim_warm_child.py `test_w1_build_network_after_lease`, `test_w2_build_qmm`,
  `test_w11_host_refused`, `test_w14_loopback`, `test_remaining_barriers`; test_nodesim_proto.py `test_host_refused`;
  broker row K-16.
- **Live state.** Scratch chip copy only; pathless `machine.save()` refused; env pins (`QUAM_STATE_PATH`, config file,
  `TMP`) point at scratch; per-click snapshot identity. Pinned by test_nodesim_fi.py `test_guard_rows` (FI-2),
  `test_env_pins`, `test_real_localappdata_write_fails`, `test_pinned_appdata_write_builds`; test_nodesim_warm_child.py
  `test_w4_snapshot`; test_nodesim_oracle.py (decoy live folder sha unchanged).
- **Data root.** `node.save` and the stub writers raise `GuardViolation`, never a silent no-op; the scratch qualibrate
  config keeps storage under scratch; writes only under scratch. Pinned by test_nodesim_fi.py `test_guard_rows`
  (FI-3), `test_stub_writers_raise`, `test_config_rows` (FI-3-config, FI-10-*), `test_write_boundary`,
  `test_write_outside_scratch_still_fails`, `test_module_level_process_rows` (FI-11).
- **Credentials.** The password lives only in the SM -> broker `hello` and the broker; the worker starts without
  `SAAS_*`; the token travels in memory SM -> worker; every message, log and answer is scrubbed. Pinned by
  test_nodesim_broker.py (K-10 scrub, K-14, K-17, K-19), test_nodesim_warm.py `test_s11_scrubs_error_and_settings`,
  `test_s12_no_saas_environment`; test_nodesim_warm_child.py `test_w9_token`, `test_w10_env_refused`; sentinel
  checks in test_nodesim_routes.py.
- **Instance leak.** Pending row before spawn; reaper; ghost rows; close by id once; owner death closes rows; Stop,
  crash and swap release. Pinned by test_nodesim_broker.py rows K-1..K-8, K-12, K-13, K-15; test_nodesim_warm.py
  `test_s1_crash_in_wait_releases_once`, `test_s6b_stop_in_job`, `test_s7_stop_own_spawn`,
  `test_renew_failure_keeps_old_lease`; test_nodesim_p2_e2e.py (killed SM and broker leave 0 live rows);
  test_nodesim_seam.py (real worker + real broker).

Reading the pins: the broker rows `K-n` are mutation-checked (the source patches are at the top of
test_nodesim_broker.py); the fault-injection mutations in test_nodesim_fi.py are opt-in with
`SM_NODESIM_MUTATION=<row>`. Assert the verdict line, not the picture (D-10). The live variants of FI-7b and FI-8b need
the real SaaS and are a pre-release checklist item, never part of the default run.

## 5. Failure kinds and what the user sees

| Failure | Kind / state | User sees |
|---|---|---|
| Node has no sim path or executes | `refused` (D-1) | "This node is not simulated here: <reason>" |
| qm-saas missing or < 1.2.0 | `blocked` (D-16) | card with the installed version and the upgrade command |
| Node code raised in build | `node_error` | "The node stopped while building its program: ..." |
| qm cannot serialize the program | `serializer` | "qm could not turn this program into a script: ..." |
| Guard verdict | `guard` | "Stopped by the safety guard: <GUARD FAIL line>"; worker exits |
| Cluster hash changed | `needs_instance` (D-13) | needs a new instance (about 62 s), button |
| Instance expired in a click | `expired` -> `expired_retry` | one retry on a fresh instance, then the failure |
| No free slot (local count 3) | `no_slot` | which count, earliest close time |
| Server refused or errored the spawn | `spawn_failed` | first line of the reason, secrets removed |
| Broker could not start, proto mismatch | `broker_failed` | "The cloud instance manager could not start: ..." |
| Worker died | `worker_crashed` | exit code; in `submit` / `wait` the instance is closed |
| Build over 30 s | `build_watchdog` | worker stopped, lab lock released |
| Lab lock busy 30 s | `lab_busy` | try again; no click sent |
| Stop / timeout | `stopped*`, `timeout` | whether the instance was closed or kept |
| Chip moved after the snapshot | `chip_changed` | old result shown, "chip changed since - re-simulate" |

Unknown states still show their `line`. A new failure needs a `line()` branch, a `SHOW` row in nodesim.js, a row in
P2 s.1 and a test that checks the text.

## 6. Running the tests

```bash
PYTHONUTF8=1 PYTHONPATH=. NODE_PATH=D:/work_laptop/statemanager-cfb/node_modules \
  C:/ProgramData/miniconda3/envs/qsm_official/python.exe -m pytest tests/test_nodesim_*.py -q -p no:cacheprovider
```

- Every suite uses fakes (tests/fakes/fake_qm_saas, fake_nodesim_broker.py, fake_nodesim_worker.py) and decoy
  endpoints (TEST-NET 192.0.2.x, unused loopback ports). None reaches a cloud simulator, a lab host or a live chip.
- `test_nodesim_selfcheck.py` drives `tests/nodesim_selfcheck.cjs` and needs node + jsdom; it skips loudly without them
  and otherwise requires exit 0 and an assertion count above a pinned floor.
- `test_nodesim_oracle.py` runs a real node only in a guarded child and only with `SM_NODESIM_ORACLE=1` plus
  `SM_NODESIM_LAB_PY` and the library variables (nodesim_oracle.py:17-26); a plain run skips it (D-6).
- The seam and P2 e2e suites start real worker and broker processes; stop leftovers by PID if a run is killed.

## 7. Pitfalls for changers

- **Never run a node's own path.** Some nodes ignore `simulate=True` and execute; `scan_node_file` executes the node.
  Only the stub harness builds (D-3, SAFETY). No test or tool may reach `execute` / `open_qm` on anything.
- **Never touch live state or the data root from a test or a tool.** Use copies; decoys for hosts and "live" folders.
- **Credentials.** Do not add a path that carries the password or the token anywhere but the SM -> broker `hello` and
  the in-memory lease relay. Do not log headers; scrub new messages with both secrets. Re-run the credential rows after
  any change to error text, logs or answers.
- **Protocol drift.** Edit nodesim_proto.py first; the worker, broker and SM load the same module. A fake that drifts
  from the real piece hides a seam: extend test_nodesim_seam.py when a message changes.
- **Instance accounting.** Any new close or spawn trigger must write the row first and close by id once; test it with
  the broker rows and the e2e kill test. Never call `close_all`. The slot count is local; do not claim a global one.
- **Locks.** The lock thread must not take any other SM lock while it holds or waits for `machine_lock`, and must
  release on the thread that took it (P2 s.2.4).
- **One definition per concept.** Host rule, cluster hash and spec, scrub and the lines live in one place each;
  do not copy them (contract 10).
- **A guard verdict ends the worker.** Do not make a worker reusable after `guard`.
- **Numbers.** "about 62 s" (spawn) and "about 5 s" (imports) are measured estimates used on every surface; change them
  in one place and in P2 s.1 together. Amounts beyond 50 us are extrapolations until D-31 is measured.
- **Constants and measured times** in nodesim_click.py (`COLD_S`, window fit) come from measurements; edit them only
  with a new measurement.
