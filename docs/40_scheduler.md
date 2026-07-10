# 40. Experiment Scheduler — Implementation Plan

> Status: **Phase 0 + 1 + 2 BUILT** (2026-06-14, on branch `feat/scheduler`). Phase 0 = config +
> pre-flight; Phase 1 = scan + single-node queue + sequential dry-run runner + status/log; Phase
> 2a = live-lock (server 409) + badge + browser-close heartbeat + post-run chip refresh +
> Chip-Status/Datasets live; Phase 2b = `run_experiment.py --mode scan` (qualibrate-inspection
> full parameter schemas, cached) + per-node parameter-editor modal + HTML5 drag-reorder.
> Each phase adversarially red-team/perf reviewed; all real findings fixed (Phase 1: 21; Phase 2a:
> 11; Phase 2b: 7 — incl. **param overrides can never set the reserved `simulate`/targets keys**,
> a param-cache, a focus-clobber guard). Full suite 1643 pass / 96 skip / 0 fail. LabB
> `--report-config` + `--mode scan` (77 runnables) verified live.
> Phase 3 = graphs RUN (splice the graph-level targets; binding-aware so the bound top-level
> Parameters is spliced, never a sibling `RetuneParameters` — verified live on the real
> `1Q_999`/`cz_999`). A graph is still refused while Dry-run is on (no per-graph simulate) and
> refused if the env's `calibration_library.folder` ≠ the chosen folder (member nodes resolve
> from it). kind/has_hook are re-derived server-side at add time (never trust the client label).
> Phase 3 red-team: 9 findings, all real ones fixed.
> Phase 4 = the §7 **worker-side post-node refresh** (the Flask-free worker calls an injected hook
> that, under an app context, reconciles the RUN's chip folder + rescans datasets + captures the
> finished run's dataset ref onto the item + bumps `run.chip_rev`). This closes the audit's
> integration gaps: works **headless** (no tab needed), targets the run's chip (nav-away safe),
> runs before the queue unlocks, and collapses the N-tab refresh stampede (the client poll now just
> re-renders on the server-broadcast `chip_rev`). Queue rows get a ↗ **run→Datasets deep link**.
> A pre-Phase-4 multi-perspective audit (perf/personas/integration/spaghetti, 39 findings) drove
> UX/safety/perf fixes (live dry-run indicator, LIVE-run confirm, overnight warning, library
> filter, leader-elected then worker-side refresh).
> A **post-Phase-4 re-audit** (perf/personas/integration/spaghetti, 26 confirmed: 0 high / 5 med / 21 low)
> confirmed no Phase-4 regressions and drove the MED fixes now in tree: (1) the ↗ deep link is
> attributed **only to a successful item** and only when a genuinely newer run appeared
> (`run.last_assigned_run_id` monotonic dedup — a failed / dry-run / no-output node never gets a
> wrong ↗); (2) **Strict preflight is now enforced at Start** server-side (`/scheduler/start` re-runs
> `build_preflight` and refuses with the failing checks unless `force`); (3) the running row + badge
> show the **current node + live elapsed + N/total**; (4) `DatasetStore` rescans are serialised on a
> `_scan_lock` so the worker's post-node rescan can't race the live Datasets delta-poll; (5) the
> failure-stop now keeps the UI lock on **through** the post-node refresh (status flips to paused only
> after). The now-superseded `POST /scheduler/refresh-chip` route was removed (dead since Phase 4).
> A **LOW-polish pass** (multi-lens catalog of 38 + 7 items, then an adversarial verify) then landed:
> all scheduler status colours moved off hard-coded hexes to theme tokens (TEXT→`--color-*-text`
> which adapts to dark, borders/dots→`--color-*-border`; new `--sched-graph-accent` for the graph
> badge); a11y (icon-button `aria-label`s, `role=dialog`/`aria-modal` + `trapFocus` Escape/restore +
> discard-confirm on the param modal, `aria-live` run-state + badge, input labels, reduced-motion
> dot); robustness (`fetchJSON` resolves a `{__neterr}` sentinel instead of rejecting — guarded in
> `renderQueue` so a dropped poll never blanks the live queue; page poll skips when the tab's hidden +
> clears its orphaned interval; `HEARTBEAT_TIMEOUT_S` 30→90 to survive a backgrounded tab's ~60s timer
> clamp; heartbeat-pause clears `current_id`; refresh-hook `content_hash` reads under `store._lock`;
> array-param coercion rejects scalars); and dead-code/stale-marker cleanup. A guided browser
> verification checklist is in **`docs/41_scheduler_verification.md`**.
> A **debugging + production UI/UX overhaul** then landed (planned, then implemented + an intense
> 4-lens red-team with adversarial verification): (1) PERF — the `node_scan` scan cache above makes
> warm scans stat-only and "+ Add" instant (was a `/mnt/d` read per click that serialised into a
> visible freeze), made **display-only** with a run-time re-derive backstop so the cache can never
> feed a stale `kind` to the run path; (2) UI — the queue is now a **dense ~30px aligned table**
> (fixed the action-cluster overflow via `minmax(0,1fr)`+ellipsis on the name), long names truncate
> with an **instant Pico `data-tooltip`**, targets is a **click-to-edit chip**, row actions are
> `⚙ ↗ ✕` inline + a `⋯` overflow menu (outside-click/Esc close, flips up near the pane edge), and
> setup cards 1 & 5 collapse (`<details>`, Pico summary chevron/form-styling reset). Red-team caught
> + fixed a HIGH (the stat-only cache key) before commit; a focused re-audit then confirmed the
> fix airtight (cache is display-only, run path fail-closed) and drove a perf merge-on-persist +
> small UX hardenings (⋯ menu survives a poll rebuild, ResizeObserver-driven tooltips).
> Full suite 1663 pass / 96 skip / 0 fail.
> **Deliberately deferred** (high-risk/low-value for a single-user local app, documented not built):
> detached runs (separate runner process surviving app close) and hookless-node hook synthesis —
> the 4 hookless utility nodes still run verbatim / are refused under Dry-run. Restart recovery is
> the existing orphan-reconcile (a crashed run's item → interrupted; remaining queue resumes on Start).

## 0. Goal

A **Scheduler** main-menu section that lets a user queue qualibrate experiment `.py` files
(single calibration **nodes** AND user-authored **graph** files like `1Q_999_*.py`) and run
them **sequentially, overnight, on chosen qubits with chosen parameters** — "set it and walk
away." Reorder by drag, toggle on/off, duplicate, repeat-N, and explode one experiment into
one-per-qubit. Results land in the qualibrate dataset folder the State Manager (SM) already
indexes, so the queue's progress is watchable live in Chip Status + Datasets.

SM's role is deliberately narrow: **read** the experiment list + their parameters, **confirm**
the env/chip/config identity, then **run** the prepared `.py` one at a time. SM never builds
graphs or drives qualibrate's orchestrator (graphs are authored in qualibrate and exported as
`.py`); SM never imports the QM stack in-process (it shells out, exactly like the
Generate-Config wizard).

## 1. Chosen execution mechanism — `custom_param` injection on a temp copy

Every node ships a framework-intended terminal-run hook:

```python
@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    # node.parameters.qubits = ["q1", "q2"]
    pass
```

`@node.run_action` is **define-and-execute**: it runs *immediately at import*, in source
order, unless `skip_if` is true. `custom_param` is the **first** action, before
`node.machine = Quam.load()`, and every consumer reads `node.parameters.X` **live at call
time** on the same mutable Pydantic object. So:

- Running `python file.py` directly (`external=False`) → `custom_param` runs → injected
  `node.parameters.X = v` overrides **qubits AND all scalar params** for the whole run.
- GUI / graph / `run_node` (`external=True`) → `custom_param` is skipped → the injection is
  invisible there (harmless on return to qualibrate).

There is **no** env-var / params-JSON / CLI hook for a bare run, and nodes run at import (no
post-import seam), so injection must happen *in the file*. To keep the original untouched, SM
writes a **temp copy in the same folder** and runs that.

### Why same-folder temp copy is safe (verified)
- Node files derive paths from `Path(__file__)` parent math (`os.path.join(dirname(__file__),
  "../../../..")` — a dead string here anyway; the **editable `.pth` is the real import
  resolver**) → a same-folder copy resolves identically.
- `QualibrationNode(name="…")` is **always a hardcoded string literal** → the copy's filename
  never leaks into node identity, dataset path, `.rb_cache`, or `.sync_hook`.
- `_sched_<uuid>_<orig>.py` copies are git-untracked → delete in a `finally`. **Never** leave
  one inside a configured `calibration_library.folder` (the scanner would register it under
  the original node's name and overwrite the real one).

### Node coverage (68 nodes in `1Q_2Q_calibrations`)
- **64/68** carry the exact `custom_param` hook → full param override.
- **4 hookless** (`1Q_00_close_other_qms`, `1Q_04_twpa_calibration`, `1Q_28_rb_success_exit`,
  `cz_38_cz_rb_success_exit`) → MVP: run as-is with a "params not overridable" badge; v2 may
  synthesize a hook.
- **10 pre-filled** custom_param → dedup injection on the assignment LHS.

### Graphs (9 files)
One graph-level targets field fans out to every node at run time
(`qualibration_graph.py:586-592`). Override = splice the `Parameters` field default, branching
on `targets_name`:
- **dict-style** (`1Q_80/81/90/91` → `qubits`; `cz_99/100/101` → `qubit_pairs`): list-literal
  default; **top-level `g.run()` with NO `__main__` guard → importing the file RUNS hardware.
  Scan must be `ast.parse` only, never import.**
- **builder-style** (`1Q_999`, `cz_999`): `None` default + `__main__` guard; do NOT splice
  their secondary `RetuneParameters` class.
- Per-node graph params = v2; MVP overrides only the graph-level targets.

## 2. Run environment (LabB, Windows conda)

- Interpreter: `<qm-env>/python` (Python 3.11.15; qualibrate
  1.3.0, quam 0.5.0a3, qm-qua). Dev is WSL but the **run env is Windows** — spawn/cancel code
  must branch on `os.name`.
- LabB's editable `.pth` already points at the new `<qualibration-graphs>/superconducting`
  tree, and LabB's qualibrate config is correct **via project override** — so LabB is good.
  (`LabA-env` is a separate, mis-pointed env: its `.pth` + config.toml still target the old
  `<work-root>\LabA\…` tree, which has drifted — do not use it.)

### qualibrate config is PROJECT-MERGED (critical)
`~/.qualibrate/config.toml` is deep-merged with `projects/<[qualibrate].project>/config.toml`,
**project wins** (`qualibrate_config/file.py:73`). SM must read the **effective merged** values,
never raw top-level. LabB effective:

| key | effective value | raw top-level (stale — ignore) |
|---|---|---|
| `[qualibrate].project` | `example_project` | |
| `[quam].state_path` | `<quam-states>/example_lab` | (not overridden) |
| `[qualibrate.storage].location` | `<dataset-root>/example_lab` | `<dataset-root>/example_lab2` |
| `[qualibrate.calibration_library].folder` | `…\ExampleVendor\…\1Q_2Q_calibrations` | `…\temp\…\CZ_calibrations` |

**Robust read:** shell the chosen env's python and call
`qualibrate_config.resolvers.get_qualibrate_config(get_qualibrate_config_path())` — the env
reports its own truth (a `run_experiment.py --report-config` mode). Do NOT re-implement TOML
merge in SM.

- For a single node run **by direct path**, only `state_path` + `storage.location` matter;
  `calibration_library.folder` is graph/library discovery only.
- Per-run forcing: set env `QUALIBRATE_CONFIG_FILE` (file or dir) + `QUAM_STATE_PATH`. Beware a
  stray ambient `QUAM_STATE_PATH` silently redirects the chip — set it explicitly or clear it.

## 3. User configuration (what the user sets / confirms)

**Tier A — picks (3):** (1) calibrations folder, (2) python env (reuse Generate-Config
picker), (3) quam_state path (defaults to the env config's `state_path`).

**Tier B — auto-read from the env, confirmed by the user:** active config file + project,
effective `storage.location`, `state_path`, `calibration_library.folder`, editable-install
path. The Scheduler offers to register `storage.location/<project-subfolder>` as an SM dataset
root.

**Tier C — run options:** simulate/dry-run (global), failure policy (stop|continue), per-node
timeout (+ default), dirty-guard behavior.

## 4. Pre-flight / identity checks (Strict policy)

Run on **Start** AND on the Verify panel. Gathered once by `_gather_preflight()` (shared by
`POST /scheduler/preflight` and `POST /scheduler/start`). **`/scheduler/start` re-runs it
server-side and refuses (`409 {ok:false, reason:"preflight", preflight}`) if any check is
`fail`** — the client surfaces the failing checks and offers a `force:true` override. A check is
`fail`-blocking; `warn` is advisory (never blocks). Helpers in `working_copy.py`, `history.py`.

| check | how | guards against |
|---|---|---|
| chip open | `_active_ctx()` not None, `ctx['type']=='quam'` | empty-state run |
| chip ↔ quam_state path | `str(Path(x).resolve()).lower()` compare vs `wc.live_folder` | wrong chip |
| chip ↔ quam_state identity | `history.align(fingerprint_of(open), fingerprint_of(target))==ALIGNED` (network=host+cluster_name) | look-alike hardware |
| **config state_path ↔ open chip (STRICT)** | effective `[quam].state_path` == open chip; **must match to run** | config points at another chip |
| folder ↔ editable `.pth` | env `.pth` root is a parent of the calibrations folder | stale-code import (the LabA-env trap) |
| storage ↔ SM dataset root | `storage.location/<project-subfolder>` registered in `workspace_roots.json` | results don't appear in Datasets |
| env QM-stack usable | `/generate/probe` `usable=True` | missing qualang_tools/quam_builder/quam |
| chip clean | `store.change_log or ctx['working_dirty'] or ctx['pending_reapply']` empty | unsaved edits collide with experiment writes |
| targets exist | each target ∈ `store.qubit_names ∪ qubit_pair_names` | KeyError at runtime |

## 5. Architecture

### `generator/run_experiment.py` (new standalone script, runs in the chosen env)
- `--report-config` → effective qualibrate config JSON (via qualibrate resolvers).
- `--scan --folder F` → **no import**: `ast` sweep + qualibrate **inspection mode** →
  `[{name, kind, has_hook, targets_name, params_schema}]`.
- `--run --target <temp_copy.py> --state-path P --config-file C --out D` → set env
  (`QUAM_STATE_PATH`, `QUALIBRATE_CONFIG_FILE`), `runpy.run_path(target)` in try/except, write
  `_result.json {status, error, traceback, versions}` (a sentinel so cancel/crash is
  unambiguous vs returncode-only).
- Standalone, QM imports behind `main()`; shipped as PyInstaller data (no spec edit — the
  `generator/` dir is already bundled). Discover via `config_generator._script_path("run_experiment.py")`.

### `core/node_inject.py` (new, **stdlib `ast` only — no QM import**)
- `splice_node(src, overrides) -> str`: locate the `custom_param` FunctionDef (name +
  decorator contains `run_action` & `node.modes.external`), replace body `pass`/existing
  assigns with `node.parameters.X = v` (dedup on LHS). Hookless nodes → raise/flag.
- `splice_graph(src, targets) -> str`: replace the top-level `Parameters` class's
  `targets_name` field default (qubits/qubit_pairs); leave `RetuneParameters` untouched.

### `core/node_scan.py` (stdlib `ast` only) + the scan cache
- `scan_file(path) -> NodeInfo` — **always fresh** (reads + AST-parses the current bytes).
  This is the safety-critical entry; the **queue-add** (`routes.scheduler_queue_add`) and the
  **run path** (`scheduler._run_item`) call it so the queued/executed `kind/has_hook/targets_name`
  is the file's own current classification, never a cache hit and never the client label.
- `scan_folder(folder, *, instance_path) -> [NodeInfo]` — **cached** (the only cached path; feeds
  the library DISPLAY). Two tiers: an in-process LRU dict + a persisted
  `instance/scheduler_ast_scan_cache.json`, each file keyed on a cheap `(st_mtime_ns, st_size)`
  stat fingerprint, so a warm rescan is stat-only and only changed/new files are re-parsed
  (over a `/mnt/d` 9p mount this is the difference between re-reading 77 files and 77 `stat`s).
- **Cache-safety contract:** the stat fingerprint can't distinguish a mtime+size-preserving edit,
  so the cache is **display-only** — every safety path re-derives via `scan_file`, and
  `_run_item` additionally **re-classifies at run time and refuses** if the file no longer matches
  what was queued (`source changed since queued`). **Bump `_HEURISTICS_VERSION`** whenever
  `_classify`/`_has_custom_param_hook`/`_targets_name`/parse semantics change, or the disk cache
  will serve stale library classifications across restarts. Error `NodeInfo`s are never cached.

### `core/scheduler.py` (new — queue + background worker)
- **Persistence:** `instance/scheduler.json` via `safe_io.atomic_write_json`
  (mirrors `workspace_roots.json` / `chip_decisions.json`).
- **Model:**
  - `SchedulerConfig{ calibrations_folder, env_python, quam_state_path, effective_config,
    failure_policy, global_simulate, default_timeout_s, continue_without_ui }`
  - `QueueItem{ id, source_file, name, kind, has_hook, targets_name, enabled, order, targets[],
    param_overrides{}, repeat, simulate, timeout_s, status, pid, log_path, result_ref,
    started_at, ended_at }`; run state additionally tracks `last_assigned_run_id` (monotonic
    ↗-attribution dedup) + `chip_rev` (per-tab re-render signal).
  - status ∈ queued | running | done | failed | cancelled | skipped
- **Worker** (`threading.Thread(daemon=True)`, runs in the SM process; mirrors the
  param-history backfill pattern): for each enabled item in `order` →
  1. heartbeat gate: if `now - last_ui_seen > threshold` and not `continue_without_ui` → pause.
  2. `node_inject` → write `_sched_<uuid>_<orig>.py` in the same folder.
  3. `Popen([env_python, run_experiment.py, --run --target …], stdout=open(log,'wb'),
     stderr=STDOUT, **group_kwargs)`; keep handle+log in a lock-guarded registry.
  4. `wait(timeout)` watchdog; on expiry → group-kill.
  5. reap → classify from `_result.json` + returncode.
  6. **post-run refresh** (§7) via the injected hook — reconcile chip + rescan datasets, attach
     `result_ref` only for a successful item with a genuinely newer run, bump `chip_rev`. Runs
     while the UI lock is still on (status flips to paused only after, on failure-stop).
  7. delete temp copy (`finally`).
  9. apply failure policy.
- **Cancel:** `os.name=='nt'` → `taskkill /PID <pid> /T /F` (kills qm/grpc grandchildren);
  POSIX → `os.killpg(os.getpgid(pid), SIGTERM)` then `SIGKILL`. Idempotent (`poll()` first).
- **ETA:** read median `run_duration_s` per node-name from `DatasetStore` history → per-item +
  total ETA.

## 6. Web / UI

**New routes (`routes.py`):** `GET /scheduler` (page), `GET /scheduler/scan`,
`POST /scheduler/{settings,effective-config,scan-params}`,
`POST /scheduler/queue/{add,remove,reorder,toggle,duplicate,expand,params,targets}`,
`POST /scheduler/{start,pause,cancel}` (Start re-runs `build_preflight` server-side and refuses
on fail unless `force`), `GET /scheduler/status` (poll; also bumps `last_ui_seen` heartbeat),
`GET /scheduler/log`, `POST /scheduler/{preflight,register-storage}`. The post-node chip refresh
is **worker-driven** (the injected `_scheduler_refresh_hook` calls `_reconcile_cached_quam_ctx`
directly — there is no `/state/sync-folder` route; that Phase-0 sketch was superseded).
**Reuse:** `/generate/envs|probe|select-env`, `/workspace/add`, `/datasets/...changes-since`.

**Frontend** (`_scheduler.html` + `scheduler.js` + one `base.html` nav line): library (scan) ·
queue (drag-reorder queued-only, toggle, duplicate, expand-per-qubit, repeat-N) · param form
(schema-driven; read-only badge when `has_hook=false`; targets-only for graphs) · run controls
(start/pause/cancel, dry-run, failure policy) · status poll + per-run log tail · result→dataset
deep links · ETA. Drag = small Sortable vendor or HTML5 (re-init on `htmx:afterSwap`).

### Live-lock model
A per-folder "scheduler running" flag (server-side, survives browser reload). **Lock = server
409 on mutator routes + UI disable.** UI-only disable is not authoritative.

- **Locked (409) — the authoritative `_SCHEDULER_MUTATOR_ENDPOINTS` set** (keep this list and
  the code set in sync; a test asserts coverage): field editors `field_edit`, `field_edit_batch`,
  `qubit_edit`, `pair_edit` (all call `modifier.set_value`); subtree creators `qubit_add_pulse`,
  `pair_add_gate`; working-copy/live writers `save`, `state_sync`, `state_apply_to_live`, `undo`,
  `discard`, `diagnostics_apply_fix`; QM-subprocess spawners `generate_build`, `generate_allocate`,
  `generate_preview_config`, `generate_load`, `config_regenerate`, `config_preview` (a 2nd OPX
  connection during a run = hardware collision). Bulk-edit commits route through `field_edit_batch`.
- **Live (never locked, auto-refresh):** Scheduler, **Chip Status (view)**, **Datasets**,
  Param History, Trends, Compare/Diff, Explorer/Qubits/Pairs (view), Config Viewer (view),
  Wiring (view), workspace/chip navigation (incl. dataset `load-state`).
- **Pause keeps the lock:** `pause()` sets a `pause_requested` flag (not status='paused')
  immediately; the worker flips to 'paused' only *after* the current node is reaped, so the lock
  (keyed on status=='running') stays on while the subprocess is still driving the OPX.
- **Synergy:** locking mutators keeps the working copy clean, which is exactly what lets
  `reconcile_with_live` auto-pull each node's write → Chip Status stays live and correct.

**Badge:** small top-bar indicator → `/scheduler`. Label shows `Running: <current node>` +
`done/total` count; pulsing dot (running) / amber (paused). The `/scheduler` page's run-state
line carries the full live read-out — `running — <node> [targets] · <elapsed> · N/total`,
ticking every 1.5 s off the running item's `started_at`. Server-side state via the existing
base.html poll pattern.

## 7. Post-run chip refresh (cross-process)

The node runs **out-of-process** (LabB env), so SM's per-folder build RLock cannot serialize it;
cross-process safety comes from `safe_io` (`ReplaceFileW` writes + mtime-bracketed pair read).
After the node is **reaped**, the **worker** (Flask-free) calls an injected hook
(`_scheduler_refresh_hook`, registered by `/scheduler/start`) under an `app.app_context()`:
it reconciles the RUN's chip folder via `_reconcile_cached_quam_ctx` (by folder path — not the
active context, so nav-away is safe), rescans the dataset stores, and bumps `run.chip_rev` so
every open tab re-renders **once** (no client fan-out). Refresh is **non-destructive**: with
unsaved SM edits it yields a `live_diverged` banner and keeps the working copy (never clobbers)
— which is why the dirty-guard (§4) runs before Start.

**Result attribution (↗ deep link):** the hook attaches a run's dataset ref to the item **only
when the item succeeded** (`status=='done'`) **and** a run newer than `run.last_assigned_run_id`
actually appeared (monotonic dedup via `set_item_result`). So a failed / dry-run / no-output
node never inherits a stray ↗ to an unrelated dataset.

**Lock ordering:** the worker writes the item's terminal status but leaves `run.status=='running'`
(UI lock on) **through** the hook, then — on a failure-stop — flips to `paused` only afterward. So
the user can never edit stale pre-refresh state in the gap, even on a failed node.

**Dataset-store concurrency:** `DatasetStore` rescans are serialised on a reentrant `_scan_lock`
(the worker's post-node `rescan_if_stale` would otherwise race the live Datasets delta-poll's
own rescan on the same store, mutating `self.runs` mid-iteration). Hot readers
(`changes_since`, `list_runs[_compact]`, `get_trend`, `summary_stats`, `_latest_run_ref`)
snapshot `list(self.runs.values())` under that lock.

## 8. App/browser lifecycle (MVP)

- **App (SM server) must stay open** — the worker is a daemon thread in the SM process; nodes
  are its subprocesses. Closing the app stops the queue (current subprocess orphaned/killed).
- **Browser closed** (app still up): heartbeat (`last_ui_seen`, bumped by `/scheduler/status`)
  goes stale → worker **finishes the current node, then pauses the queue** (does not start the
  next, does not kill the running one). A `continue_without_ui` toggle (default OFF) allows
  true headless/tmux-style continuation.
- Detached processes + restart recovery / orphan reconciliation = **v2**.

## 9. Phased roadmap

- **Phase 0** — `--report-config` + pre-flight (§4) + setup panel (folder/env/quam_state) +
  storage auto-registration. *Config + verification only; runs nothing.*
- **Phase 1** — `--scan` + single-node queue + sequential run + **dry-run first** + status/log
  + post-run refresh.
- **Phase 2** — drag-reorder/toggle/duplicate/expand-per-qubit + param forms + live-lock +
  badge + Chip-Status/Datasets live + heartbeat browser-close handling.
- **Phase 3** — graph support (targets splice) + cancel hardening + persistence/reload + ETA.
- **Phase 4 (v2)** — detached/restart recovery, hook synthesis (the 4 hookless nodes),
  per-node graph param overrides.

## 10. Key references

- Mechanism + node/graph facts: this doc §1; verification sweeps 2026-06-13/14.
- Chip context/paths: `routes.py:191-236`, `working_copy.py:98` (`live_folder`),
  `loader.py:285` (roster), `history.py:374/414` (fingerprint/align).
- Datasets: `routes.py:3028` (`/workspace/add`), `:4872` (`_dataset_candidate_folders`),
  `:5202` (`/datasets/poll`), `:5183` (`/datasets/rescan`); `dataset.py:82` (`RunInfo`).
- Env spawn: `config_generator.py:269` (`_run_command` — blocking; replace with streaming
  Popen), `:323/353/491` (discover/probe/selected-env); `routes.py:5682/5691/5710` (env routes).
- Refresh/lock: `working_copy.py:306` (`reconcile_with_live`), `routes.py:300` (build lock),
  `:2519` (`/state/sync`), `base.html:115` (`#live-diverged-slot`), `_pending_tray.html`.
