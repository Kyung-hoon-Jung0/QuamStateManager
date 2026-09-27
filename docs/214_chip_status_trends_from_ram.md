# docs/214 — Chip Status > Trends from RAM: one table per history token, a toggle is a patch

2026-09-27, branch `w7/trends` (3 commits), merged `fff2c8e`. RAM P1a + P2 of
`D:\work\sm_qa_rigs\_findings\ram_design.md` (§2a, §4). This is the Trends
section of **Chip Status** (step-shaped state history). Not docs/210
(Datasets > Trends, one point per run, P3) and not docs/208 (the IRB /
held-point customer fix in this same section, which this branch keeps).

## 1. What was measured

`w7_bench.cjs --only trends --n 5` (real headless Chrome over CDP), medians
in ms. Implementer: before = integ/w7 `e74b8ce` on the same rig
(`_findings/w7trends/bench_trends.json`). Independent verifier: A/B
interleaved on ONE rig by swapping the served code path
(`_findings/w7v_trends/bench.json`). Chips: big30x = 19.4 MB state, 200
snapshots; krs5h = the real KRS_5Q chip plus a 1,600-snapshot history (the
`ram_design` accept condition). Both at `acc0b4c` (before the §4 fixes).

big30x:

| bench label | implementer before -> after | verifier before / after |
|---|---|---|
| `toggle_on` click to chart | 972 -> 330 | 1571, 1313 / 339, 325, 343 |
| Plotly renders per toggle | 4 -> 1 | 4 / 1 |
| `Intl.DateTimeFormat` per toggle | 1308 -> 1 | 1330 / 1 |
| `toggle_off` gone | 754 -> 155 | 1117 / 144, 145 |
| `badge_on` chart | 877 -> 140 | 1037 / 165, 119 |
| server `/topology/trends` default | 151 -> 15 | 172, 245 / 21, 49, 18 |
| typeahead `paths_inter` | 78 -> 7 | 83 / 7, 14 |
| `after_snapshot_3s_first` | 7642 -> 20 | 11694, 6659 / 493, 104, 238 |
| `after_snapshot_first` | 1785 -> 952 | 2299 / 886, 687 |
| Trends section ready since scroll | 10836 -> 3408 | 11296, 12805 / 3002, 3287, 3296 |
| settled since scroll (page-wide) | 10882 -> 10270 | 16453, 20424 / 23505, 29063, 27567 |

big30x `after_snapshot_first` has 10-11 s outliers (implementer samples
[952, 11529, 226, 9891, 221]); the verifier saw 9.3 s outliers in both codes.

krs5h (verifier order b1 / a1, a2):

| bench label | implementer before -> after | verifier before / after |
|---|---|---|
| `toggle_on` click to chart | 1444 -> 180 | 1791 / 142, 144 |
| Plotly renders per toggle | 5 -> 1 | 5 / 1, 1 |
| `badge_on` chart | 1460 -> 136 | 1425 / 116 |
| server `/topology/trends` default, in-page | 546 -> 92 | 748 / 164, 117 |
| same over plain HTTP | 6-8 (server-side 2, prof) | about 45 (after) |
| `after_snapshot_first` | 4969 -> 411 | 4310 / 1019, 944 |
| `after_snapshot_3s_first` | 2602 -> 59 | 3443 / 1348, 212 |
| settled since scroll | 1989 -> 1745 | 3940 / 3158, 1698 |

krs5 (15 snapshots, implementer only): toggle on 216 -> 70, badge 212 -> 65,
server default 19 -> 10, `after_snapshot_first` 181 -> 69, section settled
1309 -> 1423. The "Please wait" loader was seen 0 times during toggles, on
every chip, in both reports.

## 2. Cause

- Server: `ram_design.md` §2a puts the cost in per-request derived work
  (provenance, families, the metrics-with-data probe, freshness checks,
  connection opens), not in the SQLite reads.
- Client: a toggle swapped the whole section (`ram_design.md` §2a P2),
  re-rendered Plotly 4-5 times and constructed 113-1308
  `Intl.DateTimeFormat` objects (implementer-measured). The implementer's
  deciding finding: after the patch, the rest of a toggle's
  cost was NOT JavaScript but Chrome's style and layout of ~110 drawn Chip
  Status panels (`getBoundingClientRect` inside Plotly's bBox).

## 3. Mechanism (`acc0b4c`)

- Server (P1a): `core/chip_trends_ram.py` keeps one table per HISTORY TOKEN
  = (hist dir, snapshot-name seq, chip version, snapshot-list identity,
  index connection, `PRAGMA data_version`), validated on every read. It
  holds provenance, curated reads, the metrics probe, pair chips, leaf
  families, IRB matching and leaf series, each computed once per token by
  the same function the cold path calls. The in-RAM family table equals
  `leaf_index.path_families` row for row and is derived incrementally from
  appended change points. A capture pre-warms an open chip on the
  index-listener thread. `extract_property_history` derives a downsampled
  change-point read from the cached full one. `SM_RAM_VERIFY=1`
  shadow-compares served parts with a cold recompute.
- Client (P2): a toggle patches `#topo-trends` in place; unchanged charts
  are kept and never detached (`data-trend-sig` per chart box), so 1 render
  per toggle-on and 0 per toggle-off. One `Intl.DateTimeFormat` per zone
  (`app.js`); `TopbarHeight` measured once per frame. The Chip Status chart
  pump yields while a Trends toggle is in flight (hold capped at 4 s); a
  drawn panel gets `content-visibility:auto` at its own measured height.
  (`acc0b4c` also made the pump draw ONE chart per task — reverted, §4 D2.)

## 4. Found by the independent verification and fixed

Verifier verdict on `acc0b4c`: FAIL (narrow) — correctness, staleness, IRB
default and held points hold; two P1 defects.

- **D1 — `StaleCacheError` under `SM_RAM_VERIFY=1`** (krs5h,
  `stale.cjs`, about 1 in 10 captures): `part ('delta', _Marks) differs from
  a cold recompute` on `leaf_matching_paths -> _delta`, a 500 on
  `/topology/trends?metrics=...&paths=...InterleavedRB`. With verify off no
  stale chart was reproduced. Root cause (fix round): the shadow check was
  unsound, not the serve — it re-ran the cold read against the index as it
  was NOW, so a background indexer commit between the memoized delta and the
  check compared a correct at-token answer with a later index. A table is
  served only while `data_version` is unchanged, so a served part can be
  newer than its token inside a racing request, never older. Fix `556dd17`:
  every shadow comparison goes through `ChipTrendsTable._shadow()`, which
  "compares at the token" — only while `data_version` equals `token[5]`
  before and after the cold read; otherwise it skips and counts
  `COUNTS['verify_moved']`. Re-run on krs5h with `SM_RAM_VERIFY=1`: 3 runs,
  24 captures, 36 page-vs-reload comparisons, all EQUAL, 0
  `StaleCacheError`, 0 HTTP 500 (implementer-side).
- **D2 — big30x Chip Status page-settle / cold-first-open regression.** The
  implementer had reported settled 10882 -> 10270; the verifier measured
  16453, 20424 (before) vs 23505, 29063, 27567 (after), long tasks
  15823, 19775 vs 21348, 20675, 23840, and a first open after a server
  restart of 17675, 21140 vs 26229, 30702. Root cause (fix round, client-only
  A/B via CDP Fetch interception, r4 n=7): one chart per task plus
  `content-visibility` applied after EVERY batch forced a whole-page
  `offsetHeight` layout per chart on a 109-panel page — charts_done +4370 ms,
  long tasks +4066 ms vs integ in all 7 rounds. Fix `556dd17`: "pump back to
  3 per task, content-visibility once" (applied when the pump's last chart
  is drawn; the Trends yield, the in-place patch and cv on drawn panels
  stay). Server half, `820f5ca`: the first request after a restart built the
  badge row's one-term `qubit_pairs` family table by reading all 180k paths
  (1.87 s profiled); a one-term table now reads only the matching paths
  (the `path_families` LIKE filter plus an ASCII-fold check), with marks over
  the whole index.

After the fix (implementer-measured, big30x, full stack, n=6 with restarts,
integ vs `820f5ca`):

| ms | first open after restart: integ / fix | warm open: integ / fix |
|---|---|---|
| Trends ready | 9236 / 8490 | 9219 / 2650 |
| charts_done (last non-Trends chart) | 19757 / 18168 | 19721 / 18067 |
| long tasks, scroll to quiet | 16094 / 16366 | 16840 / 16199 |
| toggle on | - | 1171 / 383 |
| toggle off | - | 996 / 148 |

Client-only r4 n=7 medians: charts_done integ 20327 / `acc0b4c` 25218 /
fix 19761; long tasks 19169 / 23235 / 17953. Cold first `/topology/trends`
in-process: integ 998-2064, `acc0b4c`/`556dd17` 2515-5012, `820f5ca`
420-1283 ms. No second independent verification of `556dd17`/`820f5ca` is
in the sources.

Verifier claims not met on `acc0b4c` besides D2: krs5h
`after_snapshot_first` 944-1019 vs the claimed 411, `after_snapshot_3s_first`
212-1348 vs 59; warm `/topology/trends` over plain HTTP on krs5h about 45 ms
vs 6-8 claimed (the 40 ms target borderline there; big30x in-page 18-49 ms).

Held on verification: page == reload after every `stale.cjs` step in 4 runs
(outside write + archive, toggle racing a capture, capture then badge,
A -> B -> A chip switch with a write, switch mid-toggle; the one STALE
seen was a toggle issued before an unawaited capture landed, a race the
base has too, removed from the harness by a 4.5 s wait); warm fragments
equal the base's except `data-trend-sig`; the paths typeahead JSON is
byte-identical; two chip folders aliased to one history dir agree; a bare
`/topology/trends` still charts `macros.*.fidelity.InterleavedRB` by default
with held terminal points (docs/208).

## 5. Integration

At the merge, `param_history_ram.settle_wal` also asks
`chip_trends_ram.has_conn`, the capture prewarm waits (bounded) on
`run_ingest.FOREGROUND`, and csmeta reads through trends' shared
`leaf_series_on`. See docs/224.

## 6. Pins

- `tests/test_chip_trends_ram.py`: `test_no_request_ever_rebuilds_the_leaf_index`;
  `test_a_warm_request_resolves_nothing_and_barely_stats` (a `Path.resolve`
  in `token()` -> red); `test_a_downsampled_change_read_derived_from_the_full_one_is_the_cold_read`
  (LTTB removed -> red; version check and capture purge each guard the same
  case, only both removed -> red); `test_a_commit_between_a_delta_and_its_shadow_check_is_not_a_stale_serve`
  (D1; old check, `_at_token` always True, always False -> 3/3 red);
  `test_a_term_read_is_the_full_read_filtered[1,2]` and
  `test_the_badge_rows_first_table_reads_only_its_paths` (D2 server half);
  `test_client_patch_selfcheck` drives `tests/trends_patch_selfcheck.cjs`
  (27 assertions; "a kept box was never detached").
- `tests/chip_jump_selfcheck.cjs` (via `test_chip_status_layout.py`): the
  RAM P2 block (pump yield, hold cap, cv, measured height, release — each
  mutation 2-3 FAIL) and the D2 block (3 charts in the first task, no cv
  while charts are pending, one height read per panel; 2 FAIL against
  `acc0b4c`'s `chip-status.js`).
- Verifier's mutations M1-M4 all KILLED: `data_version` dropped from
  `token()`, provenance dropped from `_trend_chart_sig`, `_apply` keeping a
  box regardless of sig, `_delta_read` ignoring a changed `cp_sig` prefix.
- Runs: implementer 512 passed, 1 failed (21 files); verifier 146 passed
  (4 trends files); fix round 230 passed, 0 failed (7 files). The one
  failure, `chip_status_resume_selfcheck`, fails on integ/w7 too
  (pre-existing; implementer 4/6 runs on both codes, verifier 4/4).

## 7. Open / not done

- First `/topology/trends` after a capture: about 270 ms server-side at
  1,600 snapshots (implementer; target 120).
- No `/topology/trends/chart` endpoint and no M4 / `hv` step downsampling;
  LTTB `downsample=400` still applies to the curated tier. P1b (alias
  expansion, `SCHEMA_VERSION = 2`) not done.
- big30x network settle is bounded by `/state/live-diff` and
  `/qualibrate/subnav` (8-21 s on both codes) — P4 scope.
- krs5h section settled <= 1,500 ms: borderline (implementer 1745;
  verifier 1698 / 3158).
- 8 tests of `test_chip_trends_ram.py` fail under a GLOBAL `SM_RAM_VERIFY=1`
  (hm=None stubs, stat counts); the same 8 fail at `acc0b4c`.
- Persistent readers per `index.sqlite`: datasets' 1 + trends' 2 (bounded,
  4 files each, closed by `close_all`); open-handle accounting to be
  re-checked (integration note).
