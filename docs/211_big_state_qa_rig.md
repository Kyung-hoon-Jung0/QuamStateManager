# docs/211 — The big-state QA rig: at ~12x the bytes, live writes cost 7-18x and Live Edit 37-86x

2026-09-26, no SM code changed (`w7/rig` stayed at `e74b8ce` with a clean tree;
the rig and the harness live outside the repo in `D:\work\sm_qa_rigs`).
Baseline measured on `integ/w7` = `e74b8ce`. The w7 RAM branches (docs/212-217)
took their before/after numbers on these rigs.

## 1. The chips

Source: a read-only copy of `260907_KRS_5Q` (the richest modern quam 0.6.0 /
quam_builder 0.4.0 chip on the machine: 5Q, 4 pairs, 1 TWPA, 2 FEMs, 13 ports,
1,654,639 B state, 2,443 fields, 30,198 scalars). The rig names it `krs5`.

`_tools/make_big_chip.py` (`--n --cols --leaf-boost --seed`, default seed
20260926) clones qubits onto a 5-column grid, renaming every qubit token in
keys and string values so every `#/`, `#./`, `#../` pointer is re-targeted;
pairs form a triangular lattice; pair-bound content is stripped from the
qubits and re-attached per target pair; each 5-qubit feedline group gets its
own MW-FEM, LF-FEM and TWPA.

| chip | qubits / pairs | state.json | fields | scalars | ports / FEMs |
|---|---|---|---|---|---|
| big20 | 20 / 43 | 7,812,444 B | 16,516 | 136,758 | 52 / 8 |
| big30x | 30 / 69 (con1 + con2) | 19,375,514 B | 131,012 (25,847 natural, 5.07x boost) | 312,946 | 78 / 12 |

Verification (`_shared/bigstate/verify_result.json`): QuamStore loads in
0.018 s (source) / 0.046 s (big20) / 0.098 s (big30x); `validate_pointers` 0
hard and 0 soft dangling on all three; `lint_state` no error class (one NEW
warning category, `physics_addressability`, x2 on big20 and x4 on big30x);
0 control ports shared by more than one qubit. In the chip's own env (conda
`KRISS_CZ`) `Quam.load` + `generate_config` succeed on all three (big30x:
`Quam.load` 16.7 s; `generate_config` 102 elements, 5,047 pulses, 73.9 s). The `cqt` env cannot run
`Quam.load` for this chip (its `quam_config` is another lab's; it fails on
the source too).

History: 200 snapshots for big30x, ingested through SM's own
`HistoryManager._ingest_entries_into(compute_diff=True)` then
`rebuild_leaf_index` (3,931,877 KB, `index.sqlite` 53 MB, 2026-04-01 to
2026-09-24, 2-8 changed leaves per step, median 3, along real paths). 200,
not the ~800 asked: SM snapshots are full copies (~19.6 MB each).

## 2. Bring-up and bench

- `bash /d/work/sm_qa_rigs/rig_up3.sh NAME SM_PORT CDP_PORT [kh|-] CODE_WORKTREE CHIP`
  (CHIP = big20 | big30x | krs5; about 17 s, 37 s for big30x). A rig holds a
  copy of the chip, a copy of `KRISS_CZ_260906` as its data folder, a fresh
  instance dir, and for big30x the shared history (400 hardlinks + 205
  copies of everything SM rewrites in place). `_tools/shared_fp.py` gave the
  same fingerprint over 616 files before and after all three rigs: the shared
  copy is never mutated. Tear down with `rig_down.sh SM_PORT CDP_PORT`.
- `node _tools/w7_bench.cjs SM_PORT CDP_PORT --only <groups> [--n 5] [--label]
  [--out] [--no-prof] [--rig]`; groups `write,bulk,tree,chip,sync,pulses,hist,load,cold`.
  Results merge into `--out` per label and group, saved after each group.
  `python _tools/w7_table.py <json> OUT.md <label> <label>` renders the table
  (two labels add a ratio column).
- Timing: `file_ms` = the target file's mtime polled every 5 ms from the CDP
  input; `status_ms` = the sync control reads `synced` with no apply in
  flight, counted only after the file was written; `settled_ms` = the page's own fetch/XHR quiet for 400 ms; page opens
  = Navigation Timing (ttfb, dcl) plus settle. Each action gets one extra
  cProfile'd iteration (`_tools/serve_prof.py`) never mixed into the samples.
- Wall time: krs5 about 6 min for all groups, big30x about 1 h 40 min.

## 3. The baseline (real headless Chrome over CDP, n=5, median ms)

Write doors (file / status / settled):

| door | krs5 | big30x | ratio |
|---|---|---|---|
| auto_apply | 313 / 1051 / 2831 | 2641 / 15127 / 26045 | 8.4x / 14.4x / 9.2x |
| apply_now | 1335 / 2547 / 3666 | 23986 / 42777 / 45045 | 18.0x / 16.8x / 12.3x |
| keep_mine | 726 / 1664 / 2407 | 11682 / 24665 / 25629 | 16.1x / 14.8x / 10.6x |
| pull_apply | 1673 / 3681 / 4595 | 21686 / 38380 / 62744 | 13.0x / 10.4x / 13.7x |
| take_live | 162 / 1614 / 3264 | 1179 / 19016 / 43963 | 7.3x / 11.8x / 13.5x |

Pages and interactions:

| group | metric | krs5 | big30x | ratio |
|---|---|---|---|---|
| bulk | warm ttfb / dcl / settle | 116 / 665 / 1087 | 5372 / 34072 / 39688 | 46.3x / 51.2x / 36.5x |
| bulk | after_edit ttfb / dcl / settle | 327 / 812 / 1231 | 22654 / 69820 / 75214 | 69.3x / 86.0x / 61.1x |
| bulk | Enter->repaint qubit cell | 27 (23-337) | 606 (463-18156) | 22.4x |
| bulk | Enter->repaint pair cell | 28 (26-47) | 12330 (1023-15038) | 440.4x |
| tree | open ttfb / dcl / settle | 30 / 115 / 608 | 226 / 538 / 1021 | 7.5x / 4.7x / 1.7x |
| tree | search first / last key | 361 / 363 | 486 / 362 | 1.3x / 1.0x |
| chip | /topology ttfb / dcl / settle | 17 / 104 / 894 | 129 / 609 / 6671 | 7.6x / 5.9x / 7.5x |
| chip | Trends ready since nav | 1193 | 7183 | 6.0x |
| chip | metric toggle click->chart | 316 | 1165 | 3.7x |
| sync | /state/live-diff warm | 427 | 5390 (4164-21331) | 12.6x |
| sync | live-diff after edit / after outside write | 362 / 382 | 4431 / 12792 | 12.2x / 33.5x |
| sync | /state/drift warm | 337 (5-425) | 7 (5-113) | - |
| sync | drift after outside write | 370 | 12987 (1545-26851) | 35.1x |
| sync | /state/versions warm / after edit | 436 / 370 | 4931 / 7877 | 11.3x / 21.3x |
| pulses | open ttfb / dcl / settle | 11 / 141 / 574 | 18 / 214 / 624 | 1.6x / 1.5x / 1.1x |
| pulses | row select / field commit | 143 / 162 | 526 / 160 | 3.7x / 1.0x |
| hist | /param-history ttfb / dcl / settle | 21 / 75 / 575 | 27 / 166 / 6131 | 1.3x / 2.2x / 10.7x |
| hist | /param-history/changes ttfb / dcl / settle | 34 / 89 / 588 | 225 / 330 / 823 | 6.6x / 3.7x / 1.4x |
| load | warm POST /load | 85 | 1349 | 15.9x |
| cold | server start / POST /load | 5355 / 858 | 7360 / 5934 | 1.4x / 6.9x |
| cold | /bulk ttfb / settle | 571 / 1991 | 19608 / 52833 | 34.3x / 26.5x |
| cold | /topology ttfb / settle | 27 / 809 | 393 / 8126 | 14.6x / 10.0x |
| cold | /param-history ttfb / settle | 103 / 673 | 318 / 6142 | 3.1x / 9.1x |
| cold | /pulses ttfb / settle | 88 / 663 | 1169 / 1791 | 13.3x / 2.7x |

Full table with every min-max: `_findings/w7_baseline.md` / `.json`;
screenshots `_findings/w7_png/{krs5,big30x}_*.png`, all viewed. Every write
sample ended `live_ok=true`, `state_after=synced`, 0 timeouts. big30x `/bulk`
renders 1,605 grid cells in the DOM (the rig_load count is 4,116).

## 4. The finding

The suspicion held: every live-write path and the Live Edit grid scale
roughly linearly-plus with state size — 7-18x on writes, 37-86x on `/bulk`
for about 12x the bytes. Pulses, the tree search and
`/param-history/changes` barely move.

## 5. Top costs and which w7 branch attacked each

Leads from the one cProfile'd big30x iteration per action. cProfile in
Python 3.12 is process-wide, so tops include other threads' work
(`run_watch.wait`, `datasets_wait`, a `config_generator` subprocess wait):
leads, not an attribution. Branch mapping only where a branch report names
the work.

| # | lead (big30x) | attacked by |
|---|---|---|
| 1 | `/bulk` is Jinja render (`bulkedit.html` root 1.67M calls); after an edit ttfb 5.4 s -> 22.7 s | `w7/liveedit`: `/bulk` spliced from cached compressed fragments, templates compiled at startup, grids patched from the change feed — docs/213 |
| 2a | env validation: `analyze_state` 44.6 s cum over 4 calls, `walk` 1.38M, `judge` 744k, `_step`/`_spec_context` 2M | `w7/liveedit` (analyze_state depth-2 chunk replay; the livewrite report names this RAM P5 as liveedit's) — docs/213 |
| 2b | sort keys and diffing: `loader.natural_key` (1.9M / 72 s on pull_apply), `differ._leaf_key`, `differ._values_equal` (1.55M on keep_mine) | `w7/livewrite`: tree diff that skips identical subtrees + content-keyed diff memos — docs/212. A separate `natural_key` hotspot (the Param History typeahead re-sort after a capture) went in `w7/datasets` — docs/215 |
| 2c | `search_index._categorize` 313k / 49.7 s on apply_now | `w7/livewrite` (lazy SearchIndex, cell edits patch it) and `w7/coldopen` (lazy index prewarmed off the request thread), unified on coldopen's class at integration — docs/212, docs/216, docs/224 |
| 2d | `lint_state` 16 s, `_pulse_peak` 18.7 s, `pair_columns._walk_pair` 17.4 s, recomputed inside `/state/live-diff` and `state_drift` | `w7/livewrite` serves the live-diff body and the drift diffs from RAM on content tokens — docs/212; `w7/liveedit` makes `lint_state` re-derive only what an edit reaches — docs/213 |
| 2e | `path_match.same_folder` / `_scope_for` / `_project_for_path` 50.5 s cum over 67 calls on keep_mine | not named by any w7 branch report |
| 3 | `/state/versions` quick-diff (`natural_key` 313k, `differ._leaf_key`) | `w7/livewrite`: snapshot-pair diffs from the folder-diff memo — docs/212 |
| 4 | cold `POST /load`: `qualibrate_config._load_toml_retry` 128 calls / 7.6 s, `undo_journal.load_state` 3.5 s | `w7/coldopen` attacked cold open and chip switch (RAM P10) — docs/216; its report does not name these two functions |
| 5 | `/topology` mount: `_topology_with_derived_rb`, `deepcopy` top self-time | not named by any report; `w7/liveedit` made `get_topology` recompute only what an edit reaches — docs/213 |
| 6 | `/param-history` settle 6.1 s vs 27 ms ttfb (client-side) | `w7/datasets` serves Param History, the Changes feed, the value-history drawer and the typeahead from RAM (server side; this lead was client-side) — docs/215 |
| 7 | pair-cell Enter->repaint 12.3 s while the edit POST is 9 ms (client-side) | `w7/liveedit`: an Enter repaints only the detached columns it names — docs/213 |

Rows outside the top-cost list: Chip Status Trends ready / metric toggle ->
`w7/trends` (docs/214); Pulses after an edit -> `w7/pulses` (the Pulses
index stays warm across value edits, docs/217).

## 6. Caveats and limits of the rig

- One run per chip, sequential, one machine. The big30x write group is noisy
  on status/settled (auto_apply status 7336-18024).
- The first krs5 full run exited 1 after the load group with no error and did
  not reproduce in three later cold runs; the krs5 pulses and cold rows come
  from a rerun on the same rig and code.
- krs5 has no shared synthetic history (only the instance copy plus the
  bench's own 52-61 snapshots), so the history-related ratios compare
  history sizes as well as state sizes.
- big30x's 312,946 scalars exceed `leaf_index.WALK_CAP` = 200,000: the build
  log's `rebuild_leaf_index` reported rows=0, yet the browser's Changes page
  shows 180,599 changes over 204 snapshots. Which path built them was not
  investigated — a real scaling edge, open.
- No pins: no SM code changed, so no pytest run and nothing to mutation-check.
