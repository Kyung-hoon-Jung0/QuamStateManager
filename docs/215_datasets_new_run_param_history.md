# docs/215 — A new run is absorbed by the tick, alignment runs in the background, Param History reads from RAM

2026-09-26/27, branch `w7/datasets` (17 commits, head `72014ec`), merged into `integ/w7` as `c0201d4`.
Design: `D:\work\sm_qa_rigs\_findings\ram_design.md` §4, packages P7 (new-run path and the Datasets page)
and P8 (Param History and the drawer).

## 1. What was measured

Implementer: `w7_bench.cjs --only ds`, n=5, real headless Chrome over CDP, `ds_rig.sh` rig (a hardlink copy
of the 4,121-run KH archive plus the chip; big30x also carries the 200-snapshot history); before = `integ/w7`
(`*_before3`), after = the branch at `c9628e1` (`*_after4`); results in `_findings/w7_ds.json`.
Independent verifier: the same bench (`--only ds,dsv[,dsrow]`, n=5), its own rig, every pair a fresh rig
built back-to-back, medians; results in `_findings/w7v_ds.json`. Both ran on a shared machine (the
implementer counted 7 other SM rigs live). ms unless stated.

big30x:

| | target | implementer before -> after | verifier integ -> branch |
|---|---|---|---|
| first `/workspace/tree` after a run | <=50 | 970 -> 9 | 2817 -> 16 (8-556) |
| first `/datasets` after a run, ttfb | <=60 | 158 -> 46 | 312 -> 103 (42-356) |
| `/datasets` steady ttfb | <=40 | 103 -> 48 | 230 (201-341) -> 40 (38-110) |
| alignment, first request on a new root | <=150 | 66,332 -> 152; background ready in 120 s | 160,795 -> 164; ready in 121.8 s |
| `/param-history/changes` repeat | <=40 | 131 -> 73 | 298 -> 69 (63-105) |
| `/param-history/changes` after a capture | <=120 | not reported | 397 -> 142 |
| `/param-history` after a capture | <=60 | 158 -> 255 | 459 -> 386 (220-914) |
| value-history drawer, warm | <=30 | 111 -> 26 | 447 -> 32 |
| alias drawer, warm | <=30 | 17,255 -> 28 | 46,701 -> 34 |
| capture | <=600 | 5,337 -> 6,006 | 13,575 -> 7,339 (6,414-8,445) |
| typeahead, server | — | `q` 1,199 -> 8; `q1` 425 -> 8 | `q1` 1,457 -> 9; `qu` 4,173 -> 11 |
| Param History open, settle | — | 4,945 -> 3,529 | 10,002 -> 3,042 |

krs5 (implementer / verifier): first tree after a run 979 -> 8 / 2835 -> 13; first `/datasets` after a run
119 -> 38 / 302 -> 82; steady 188 -> 59 / 281 -> 30; capture 498 -> 477 / 2,540 -> 1,122; `/param-history`
after a capture 39 -> 34 / 263 -> 77; Changes repeat 84 -> 120 (implementer: "noisy") / 502 -> 275; drawer
warm 114 -> 22 / 645 -> 39; alias drawer 101 -> 23 / 749 -> 30; typeahead `qu` (verifier) 1,126 -> 25.

The Changes filter dropped typed keys before (big30x `value_kept` 0). After, `value_kept`, `focus_kept` and
`feed_matches` are all 1 (big30x last key 524, burst 722 implementer / 676 verifier; krs5, verifier: keys
were dropped before, last key / burst 635 / 809 now).

What moved rather than shrank:

- The first `/datasets` in the new-run race is slower: implementer 124 -> 591 (big30x), 144 -> 562 (krs5);
  verifier 295 -> 894 (big30x, tree 3104 -> 1310, sum 3.4 s -> 2.2 s) and 328 -> 714 (krs5, tree
  2,971 -> 1,452). The rescan cost moved from `/workspace/tree` to `/datasets`; the sum is lower.
- The new-run tick itself (verifier only; the implementer reported request-side figures only): big30x
  `tick_done` 403 -> 4087 (3396-7932) ms of background CPU per run — sidebar 1.5 s, alignment 2.6 s,
  payload 0.55 s.
- Row click / j+Enter on big30x looked slower in both benches (verifier 181 -> 291, 178 -> 258). The
  verifier's six interleaved same-rig curl A/B rounds gave integ detail medians 43/48, 54/57, 61/57 vs branch
  59/76, 60/58, 95/80, and cProfile 73.7 vs 89.0 ms per request with an identical function mix. Verdict:
  a possible +10-20 ms on first-visit run detail, inside the noise envelope, no code-level cause found —
  "not called a regression, but flagged as unproven either way".

## 2. Cause

From the commit bodies: a new run bumped its date dir and the rescan re-walked and re-parsed every run of that
day (649 `node.json` on KH's busiest day) inside the first request; `/workspace/tree` re-rendered the whole
tree (250-350 ms over 4,160 runs) because only a request filled its HTML memo; `/param-history/alignment` ran
`scan_workspace_alignment` inline (60 s on the first view of a 4,121-run root, ~0.7 s after every new run);
`changes_by_snapshot` grouped the whole change-point table and natural-sorted every matching row to show 25;
the drawer scanned 150 full snapshots for an alias path and found nothing; every typeahead keystroke re-ran a
join + GROUP BY + Python natural sort (861 ms for `q`, 180k paths).

## 3. Mechanism

**P7 — the new-run path off the request.**
- `1b06f77` — a run folder is reused when its mtime, `node.json` (mtime_ns, size) and quam_state dir mtime
  are unmoved; the listing cache is saved on a coalescing thread; a half-written run keeps its root stale
  until its `node.json` lands. The Datasets payload is a `KeyedMemo` validated on each store's identity and
  both generations. The run-watch tick rescans and re-encodes open Datasets views (`RunIngest.add_after`).
- `3d6a6ee` — the tick pre-renders the sidebar tree through the routes' own `_unfiltered_tree_html`; the
  memo key is (workspace version, active chip path), since a chip switch was being served the previous
  chip's tree.
- `aa14f69`, `b5e9af4` — the candidate-folder rebuild takes `dirname` on strings, memoized per string; a
  new run costs one split (profiled 72 of 105 ms before). `1aa1eb2` — the rows JSON is escaped for its
  `<script>` body once per payload (`ScriptJson`); warm `/datasets` in-process 32-42 -> 19-23 ms.
- `2e4103b` — workspace alignment is a single-flight background job per chip; the request waits 120 ms,
  else answers a self-refetching placeholder with progress and stamps no counts. The accessor is
  token-validated; the tick recomputes a chip somebody has viewed.
- `c9628e1` — `run_ingest.Foreground` counts user page requests (static, `/debug` and held polls exempt);
  every `add_after` step waits for 250 ms of quiet (at most 3 s) and the alignment refresh yields every 64
  runs. The yield applies only when the worker runs as a thread.

**P8 — Param History and the drawer from RAM.**
- `18aaea7` — the Changes feed is a `KeyedMemo` validated by `param_history_ram.hist_token`: the manager's
  chip version, `PRAGMA data_version` through one persistent connection (moves on any other connection's
  commit, a second SM process included), the index file identity and the snapshot list.
  `changes_by_snapshot` walks snapshots newest-first via `idx_leaf_cp_snap` and stops at the limit. The
  drawer keeps one parsed value per (chip dir, dot path, snapshot), validated by that snapshot's files'
  (mtime_ns, size), and reads an alias path at the leaf it names.
- `2017e77` — the filter input carries an id and `hx-preserve`; `natural_first()` picks the first n rows
  without sorting all of them. `5bbe5d6` — the typeahead's datalist is preserved with the box (it filled a
  detached list after every swap: 0 options -> 30); an older answer after a newer query is dropped.
- `922ad49` — `Differ` classifies in one pass over the new side (`summary_between` big30x vs itself
  494 -> ~155 ms). `05dc655` — snapshot/qubit/trigger distincts by index skip-scan (16-22 ms apiece ->
  1.3 / 1.2 / 0.4 ms). `e8a32c1` — `list_chip_histories` reuses a chip's row while its `index.sqlite` and
  `-wal` stats are unchanged; the persistent connections are an LRU of 4, and every token carries the
  connection's generation.
- `6f286e1` — `leaf_index.PathRank`: the ranked path list once per chip history (23.9 MB on big30x),
  validated by `hist_token`; a keystroke is a substring scan that stops at the 30th hit. `c9628e1` — a rebuild
  after a capture reuses the previous natural order unless a path is new (180k `natural_key` calls, 2.3 s of
  3.9 s on big30x, before); krs5 capture had regressed to 821 ms in after3 and is 477 in after4.

At integration the capture-site flat-map recall (`_LAST_FLAT`/`_remember_flat`/`_recall_flat`) was dropped
for livewrite's folder-diff memo, `run_ingest.Foreground` and coldopen's `core/activity` became one foreground
rule, and `settle_wal` also consults trends' reader pool — see docs/224.

## 4. Found by the independent verification and fixed

Verdict `PASS_WITH_MINOR_DEFECT`. Staleness: 11 surfaces dumped warm after a tag, note, bookmark, 4 UI star
clicks, an outside live write (`qubits.q1.T1`), a capture, a new run folder and a chip switch and back, then
compared to a cold restart — every surface EQUAL on big30x and krs5; the only diffs were the new tree
version stamp and D1's figure. Intact-after-reload checks 1 on both chips; no page errors except
ERR_CONNECTION during the deliberate restart.

- **D1 (P2) — the token reader pinned the WAL.** The persistent connection meant a writer's close was never
  the last one, so `index.sqlite-wal` stayed at its high-water size (6,204,752 B after one 6,000-row write)
  and "MB on disk" counted it (krs5: 23.9 MB before a restart, 17.8 MB after; du 17.81 MB). Fixed in
  `72014ec`: the connection opens `mode=rw` (never creates; ro fallback — the implementer measured that a ro
  checkpoint fails with "disk I/O error" when frames are not backfilled), `timeout=0`, and runs
  `wal_checkpoint(TRUNCATE)` whenever `data_version` moved; a checkpoint is not a commit, so the token is
  unchanged. `settle_wal` runs only on an already-open connection; `history_disk_stats` calls it before
  walking. Implementer, real Chrome on krs5: WAL 6,600,272 B -> 0 B, header 33.4 MB on disk = du 33.38 MB.
- **Note — the agent's held node wait counted as foreground** (`/api/agent/run/<key>?wait_s`, up to
  3,600 s: every tick step waited the full 3 s). Fixed in `72014ec`: `/api/agent/run/` and
  `/api/agent/run-node` are exempt.
- **Note — `AlignmentJobs.refresh()` ran unregistered**, so a request during it started a second scan.
  Fixed in `72014ec`: the refresh registers its job under the lock `_start` checks; a request joins it.

Verifier figures that differ from the implementer's: big30x capture 13.6 s -> 7.3 s (implementer
5,337 -> 6,006 ms — "the branch is faster in my A/B, not slower"); steady `/datasets` 40 vs 48; first
`/datasets` after a run 103 vs 46 (target <=60).

## 5. Pins

- `tests/test_param_history_ram.py` — `test_path_rank_reusing_an_earlier_natural_order_equals_cold` (red
  with the missing-path check removed; re-checked by the verifier), `test_the_token_reader_does_not_pin_the_wal`,
  `test_disk_stats_does_not_count_a_pinned_wal`, `test_settle_wal_never_opens_a_connection` (fix round:
  checkpoint removed 2 red, `settle_wal` call removed 1 red, `settle_wal` opening a connection 1 red,
  ro-only connection 2 red).
- `tests/test_newrun_path.py` — `TestTreePreRender`, `TestForegroundYield` (4 tests on the branch; M1-M4 red;
  agent exemptions M5, M6 red; the exemption removal re-checked red by the verifier). The yield pin was
  order-dependent on the branch itself and was rewritten on `integ/w7` (`8656f2b`, `5caf722`) with
  `test_the_held_routes_are_one_list` added — see docs/224.
- `tests/test_alignment_job.py::TestWorkerRefresh::test_a_request_during_the_tick_refresh_joins_it` (M7 red).
- `tests/ph_typeahead_swap_fragcheck.cjs` (fragment-fed, pytest-driven; preserve and ordering mutation-checked).
- Suites: implementer 1302 passed, 36 skipped, 0 failed (cqt); the verifier reproduced 1302 / 36 / 0 over 66
  files (507 s) and 27 `.cjs` selfchecks exit 0. Fix round: 515 passed, 1 skipped, 0 failed over 16 files.

## 6. Open / not done

- Targets missed on big30x: Changes repeat 73 ms (<=40); `/param-history` after a capture 255 ms (<=60 —
  `extract_property_history` ~67, `history_disk_stats` ~57, sparklines ~26, templates ~23; needs an
  incremental trends append); capture 4.4-6.0 s (<=600, the 19.6 MB full snapshot copy plus index ingest).
- The new-run tick costs 3.4-7.9 s of background CPU per run on big30x; the race moved cost to `/datasets`.
- `_refresh_live_diverged` re-hashes the 19 MB live state once every 30 s (~1 s on big30x) inside whichever
  page request lands on it — outside P7/P8, flagged for the live-write owner.
- Not measured by either report: P7's "no `/topology` request > 80 ms after a new run" and alignment
  "<= 50 ms after a new run".
- Rig caveat: 2 of the verifier's 7 rig builds came up with only the 39-run KRISS root (a `ds_rig.sh`
  startup race hitting both codes); those runs were discarded.
