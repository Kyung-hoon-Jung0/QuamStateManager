# docs/224 — The w7 integration: one mutation recorder, one lazy search index, one foreground rule

2026-09-26/27, branch `integ/w7` (base `e74b8ce`, head `5caf722`), worktree
`D:\work\sm-w7`. Two integration rounds; sources `out_integ1.json` (round 1,
head `6541800`) and `out_integ2.json` (round 2, head `5caf722`). Not pushed.

## 1. What was merged

| round | branch | merge | branch head | doc |
|---|---|---|---|---|
| 1 | `w7/smallui` | `16e3acd` | `89f4533` | docs/222 |
| 1 | `w7/zline` | `7ff16b2` | `9848d9f` | docs/219 |
| 1 | `w7/scroll` | `2190813` | `6507cdd` | docs/221 |
| 1 | `w7/pulses` | `dead90b` | `64fe0dc` | docs/217 |
| 1 | `w7/coldopen` | `27766e5` | `1fec054` | docs/216 |
| 1 | `w7/livewrite` | `a8c5fbe` | `fc3dfd7` | docs/212 |
| 1 | `w7/liveedit` | `5f55603` | `021fff5` | docs/213 |
| 1 | `w7/datasets` | `c0201d4` | `72014ec` | docs/215 |
| 1 | `w7/trends` | `fff2c8e` | `820f5ca` | docs/214 |
| 2 | `w7/csmeta` | `9dfc0f2` | `afabe15` | docs/220 |
| 2 | `w7/agentsqa` | `e33e1d0` | `ab9cc70` | docs/223 |

**Not merged:** `w7/pulsecreate` (which carries `w7/adaptive`) — still in its
own fix rounds when integration round 2 closed; see docs/217 part B and
docs/218.

## 2. Three mechanisms that existed twice became one

The branches were built in parallel, so three of them solved the same
problem independently. Keeping both copies would have meant two sources of
truth that drift; each pair was collapsed to one.

### 2.1 ONE mutation recorder — `store_revs.note` (pulses × liveedit)

`w7/pulses` recorded every store mutation in `QuamStore._mut_journal`
(`journal_mutation` / `mutations_since`, value-only flag =
`is_value_only_write`); `w7/liveedit` recorded the same mutations through
`store_revs.note` / `changes_since` (flag = plain, which also admits
None↔value flips). Both were called at the same five sites.
`store_revs.note(store, kind, path, old, new)` is now the single call site —
`modifier.py`'s set/create/delete/undo and `loader.reload()` (`note('reload')`)
call only it — and it also feeds the pulses journal with the stricter
`is_value_only_write` flag, so `PulseIndex.mutations_since` keeps its exact
semantics. `store_revs` imports `is_value_only_write` from `loader` (no cycle:
`loader` imports `store_revs` lazily). The lint and env-analysis memos
(`diagnostics.lint_state`, `state_env_validate.analysis_for_store`) keep
coldopen's re-check-under-the-lock, keyed on liveedit's
`store_revs.seq_token(store)`.

Auto-merge artefact found by the suite: liveedit's `only=` owner filter was
referenced inside a body pulses had moved into `_list_pulses_with`, unbound
there — a `NameError` on every cold `PulseIndex` build, 31 tests red. Fixed by
threading `only` through as a keyword (`1a8f2c1`).

### 2.2 ONE `LazySearchIndex` (coldopen × livewrite)

Both branches added `class LazySearchIndex` to `core/search_index.py`.
Coldopen's is kept (the superset: optional `wiring_keys`,
`built`/`__getattr__`/`__repr__`, mutation-seq-guarded snapshot build,
prewarm + pacing); livewrite's duplicate is dropped. Livewrite's
`LazySearchIndex(store, keys)` calls stay valid; routes use one spelling,
`LazySearchIndex(store)`.

### 2.3 ONE foreground rule — `activity.is_foreground(path)` (coldopen × datasets)

Coldopen's `core/activity` (`begin`/`end`/`busy`/`wait_quiet`; drives the
search-index prewarm, unbounded wait with a supersede stop) and datasets'
`run_ingest.Foreground` (`enter`/`exit`/`wait_idle`, bounded; drives the
run-watch tick precompute) each had their own `before_request`/teardown hook
and their own exemption list. `activity.is_foreground(path)` is now the single
rule, over one exemption list (`LONG_POLL_PATHS` + `EXEMPT_PREFIXES`, the
union of both, including datasets' `/api/agent/run/*` and
`/api/agent/run-node` 3,600 s node waits, which coldopen's prewarm needed just
as much). One app hook feeds both counters; each consumer keeps its own wait
semantics. `bg_gate` (livewrite: opt-in, bounded live-write critical
sections) answers a different question and is kept as-is; the prewarm pacer
already pauses for an in-flight write request.

Round 2 extended the list rather than adding a new one: a scan of every
agentsqa blueprint for `.wait`/sleep/deadline loops found one more held
request, `POST /api/agent/setup/test` (polls the agent process, default
90 s), now in `LONG_POLL_PATHS`. Pinned by
`tests/test_newrun_path.py::test_the_held_routes_are_one_list` (exemption
dropped → red). **Contract: any future held-open route is added to this list
and to that pin, never to a new list.**

## 3. Other seams resolved

- **Two read-connection pools on `index.sqlite`** (datasets'
  `param_history_ram` × trends' `chip_trends_ram`) key different caches and
  compose — except that datasets' WAL give-back premise ("no connection open
  ⇒ no pin") stopped holding once trends' readers pin the same file.
  `param_history_ram.settle_wal` now also asks `chip_trends_ram.has_conn`
  (new) and checkpoints through its own connection when either pool holds a
  reader.
- **Trends' post-index-commit prewarm** (`install_trends_prewarm`) now waits,
  bounded, on `run_ingest.FOREGROUND` like the tick's precompute, so a
  capture's page render is not raced by it. Livewrite's deferred index insert
  already calls `_fire_indexed` after its commit, so trends' listener fires on
  deferred captures too (auto-merged, verified).
- **Capture diff summary** (`core/history.py`): livewrite's write-once
  folder-diff memo (`_diff_snapshot_dirs`, shared with the drift poll's
  `diff_cache`, then served by `/prev-state-diff`) is kept and
  `Differ.summary(entries)` reads off it; datasets' capture-site flat-map
  recall (`_LAST_FLAT`/`_remember_flat`/`_recall_flat`) was dropped — it
  re-saved a parse the doc cache already avoids and would have left the first
  `/prev-state-diff` to diff on the request. In `core/differ.py`, livewrite's
  tree diff is kept with `_diff_flat` as the parity-pinned reference, whose
  body is now datasets' one-pass `_classify`.
- **Loader** (`_load(docs)`): livewrite's handed-over-docs path is kept (skips
  the second parse); coldopen's RAM P10 `file_digest` is still recorded on
  that path via the new `_pair_bytes_digest` (bytes bracketed by the pair
  fingerprint, no parse; `None` on drift ⇒ the store simply cannot be parked
  until a file load).
- **csmeta × trends**: csmeta's `origin` metadata (oldest, truncated,
  `ts_list`) is computed on the same connection right before the shared
  `leaf_series_on` read, so the truncated/oldest verdict and the series
  describe the same index state. csmeta opens no persistent connection (open
  and close per read), so persistent readers per `index.sqlite` stay at
  datasets' 1 + trends' 2 (bounded, 4 files each, closed by `close_all`).
- **csmeta × smallui** (`chip-status.js`): csmeta's tile jump attributes are
  kept together with smallui's rule that no Chip Status title carries a bare
  arrow; csmeta's `metaInfo.toggleHtml` sits beside the density control.
- **agentsqa × coldopen** (`routes.py`): agentsqa's two activation sites call
  `_set_active_context` (agent-clock bump on a chip switch); coldopen's
  park/unpark path lives inside `_activate_quam`, so the bump still covers it.

## 4. Full suite

Command: `PYTHONUTF8=1 cqt python -m pytest tests/ -q --timeout=900
--timeout-method=thread -p no:cacheprovider --deselect
tests/test_main.py::TestWaitForServer` (run by the integrator).

| round | at | passed | failed | skipped | deselected | duration |
|---|---|---|---|---|---|---|
| 1 | `aadb757` | 11,317 | 4 | 250 | 2 | 3,375 s |
| 2 | `8656f2b` | 11,367 | 2 | 250 | 2 | 3,025 s |

Round 1's four failures: three integration-only pins that encoded one side's
spelling (`test_chip_prewarm_diag.py` ×2 keyed on the bare `mutation_seq` /
an `analyze_state` stand-in without the `_chunks` kw; `test_topbar_twerk.py`
requiring `<body class="app-shell">` exactly while smallui adds
`exp-list-compact`) — pins adapted, subject unchanged, `6541800` — plus
`test_newrun_path.py::TestForegroundYield::test_a_started_worker_yields_and_run_once_does_not`,
order-dependent on `w7/datasets` alone as well (reproduced there at `72014ec`
in the same order). `aadb757` adapted `test_param_history_ram`'s flat-seam pin
to drive `Differ._diff_flat` directly (test-only).

Round 2's two failures: `test_knowledge_pack.py::TestNoCustomerNamesShipped::test_shipped_code_carries_no_lab_name`
— two agentsqa comments named a customer's chip, branch-own, fixed on integ
by rewording (`7ff9082`, no behaviour change); and the yield pin again, now
hardened in `8656f2b` (the gate reports when the worker reaches it; "held
while in flight" asserted from that state) yet failing once in the full
suite while passing alone and at the end of a 228-file range run whose
per-test check found `FOREGROUND.active` and `activity` inflight at 0 after
every test. `5caf722` makes the pin judge a fresh `Foreground` instance
(`run_once` reads the module global), since the process-wide counter is fed
by every app the suite created before; the app-hook feed stays pinned by
`test_the_app_counts_page_requests_and_not_held_polls`. Mutation sweep all
red after the change.

Round 2 also carried `13458ea` (smallui re-verify P3a:
`tests/json_panel_height_selfcheck.cjs` + `tests/test_json_panel_height.py`,
a throwing `localStorage` getter raises nothing) and, in `8656f2b`, a one-line
inline script first in `<body>` that drops `exp-list-compact` when
`localStorage` holds `'0'` (no reverse flash for users who chose full names),
pinned server-side and in `exp_list_compact_selfcheck.cjs`; mutation sweep
all red.

## 5. Open

- The last fixes of each round (`6541800`; `7ff9082`, `5caf722`) were
  verified by running their files alone and by mutation sweeps, **not by a
  further full-suite run**.
- `w7/agentsqa` alone still carries the two customer-named comments; its
  owner should take `7ff9082`'s two lines. `w7/datasets` alone still carries
  the pre-hardening yield pin; its owner should take `8656f2b` + `5caf722`'s
  test rewrite.
- `Differ.summary_between` (datasets) is unused in production now; kept
  because `tests/test_param_history_ram.py` pins it.
- `w7/pulsecreate` is not merged; any held-open route it adds goes into
  `activity.LONG_POLL_PATHS`/`EXEMPT_PREFIXES` and
  `test_the_held_routes_are_one_list`.
