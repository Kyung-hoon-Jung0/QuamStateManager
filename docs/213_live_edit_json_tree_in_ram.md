# docs/213 — Live Edit grids and the Json Tree patch themselves from a change feed

2026-09-26/27, branch `w7/liveedit` (12 commits), merged `5f55603`. RAM P5 + P6
of `D:\work\sm_qa_rigs\_findings\ram_design.md` §4 (P0-lite and P3: docs/210).

## 1. What was measured

`w7_bench.cjs --only le`, real headless Chrome over CDP, medians in ms, other
agents' rigs running throughout. Implementer (`_findings/w7_liveedit.json`):
big30x after2 = through `c51fd9e`, after3 = server only + `fbdce27`, final =
through `da2f1b3` (n=3, noisier). Independent verifier
(`_findings/w7v_liveedit.json`): n=5, one rig for both codes, fresh working
copy per run; on big30x branch and base ran sequentially, not interleaved.

big30x (synthetic big-state chip; 1,264 qubit + 2,389 pair columns), server:

| ms | impl. before | impl. after2 / after3 / final | verifier base / branch |
|---|---|---|---|
| `/bulk` warm | 8763 | 27 / 41 / 66 | 7287 / 19 |
| `/bulk` after an edit | 42981 | 935 / 1326 / 738 | 10193 / 125 |
| `/topology` after an edit | 10873 | 261 / 189 / 212 | 4521 / 77 |
| `/diagnostics/summary` after an edit | 12843 | 133 / 67 / 205 | 4492 / 28 |
| `/type-alarm/banner` after an edit | 14901 | 121 / 33 / 43 | 3560 / 12 |
| `/qualibrate/subnav` warm | 11263 | 10 / 14 / 25 | 7628 / 6 |
| `/bulk/all-values` 304 | 12698 | 3 / 5 / 4 | 7742 / 3 |
| `/bulk/all-values` 200 | 30096 | 16050 / 26988 / 33694 | 17022 / 11713 |

big30x, client:

| ms | impl. before | impl. after2 / final | verifier base / branch |
|---|---|---|---|
| Enter -> repaint, qubit | 1279 | 130 / 183 | 1229 / 130 |
| Enter -> repaint, pair | 3539 | 307 / 792 | 3702 / 118 |
| undo repaint, qubit | 14521 | 2637 / 2915 | 13520 / 1261 |
| undo repaint, pair | 11327 | 3171 / 4476 | 11960 / 1369 |
| cell typing, burst of 10 | 2883 | 448 / 531 | 2349 / 284 |
| Ctrl+D fill-down | 775 | 174 / 184 | 642 / 140 |
| column search, first key | 2267 | 1992 / 915 | 2079 / 545 |
| horizontal jump to the far right | 372 | 3250 / 2928 | 408 / 2812 (§4) |
| Json Tree open, DCL | 1534 | 896 / 1193 | 1104 / 411 |

krs5 (real 5-qubit chip copy), implementer n=5 through `fbdce27`, verifier
base / branch in brackets: `/bulk` warm 281 -> 36 (166 / 19), after an edit
989 -> 55 (518 / 44), `/topology` after an edit 634 -> 51 (380 / 35),
`/diagnostics/summary` after an edit 649 -> 30 (377 / 21), subnav 117 -> 10
(62 / 10), all-values 304 551 -> 4 (207 / 3). Client: implementer Enter (le)
qubit 115 -> 75, pair 147 -> 138; verifier flat between codes (qubit 93 /
83, pair 93 / 96, column search first key 288 / 318). Single-run undo and
live-diff slowdowns on krs5 did not survive interleaved A/B in either report.

## 2. Cause (implementer's profiling on big30x, the "30Q rig")

- `/bulk` re-rendered and re-gzipped whole per request: 41.6 MB of markup,
  4-5 s warm (`7c78831`).
- `lint_state` 4.86 s per edit and `analyze_state` 2.5 s (`2872154`); the
  chunk replay's TypeSpec `dict.update` 56-86 ms per after-edit request
  (`fbdce27`); `path_closure`'s O(n^2) ancestor check 2.4 s and a per-request
  deepcopy of the cached topology 0.43 s (`a41f4ab`).
- An Enter: ~0.95 s of querySelector for the header stats, ~200k empty cold
  tds in layout (`c51fd9e`), and the apply echo hydrating every detached
  column (`dfd7f83`).
- The first cell commit paid ~100 ms of Jinja compile for the Review tray
  inside `POST /field/edit` (`fbd3e81`).

## 3. Mechanism

- `2872154` — `core/store_revs`: one event per `mutation_seq` (plain vs
  structural), validated on read: an unexplained seq move or a log that no
  longer reaches back means rebuild, never "nothing changed". Chunk tokens
  per depth-2 subtree, a structure token, pointer path closures.
  `lint_state` re-derives per entity chunk and recomputes waveform findings
  only for owners whose pointer closure a write reaches; `analyze_state`
  replays each chunk's recorded `add()` calls. all-values checks its ETag
  first (a 304 builds nothing). The subnav comes from a stat-keyed index
  (pin `1a0a435`).
- `a41f4ab` — each grid cell records the stored paths it read (incl. the FSP
  an amplitude's dBm annotation reads); with `store_revs.column_token`
  unmoved, only the cells a plain write reaches are re-run, by the same
  `_qubit_cell_for` / `_entity_cell_for` a cold build uses.
- `7c78831` + `d74512c` — `/bulk` is spliced: only the chrome is rendered;
  each heavy piece (grid head, row, cold-map JSON, extra grids) is compressed
  once by `core/gzsplice` (raw DEFLATE pieces, sync-flushed, joined into ONE
  gzip member with a combined CRC-32) and kept until its versions move; a
  missing or reused piece falls back to the classic render. One splice at a
  time; eviction snapshots before it iterates.
- `73602f2` — `get_topology` reuses a node/edge only while every event since
  is a plain `qubits`/`qubit_pairs` write outside its PATH-level pointer
  closure (entity-level reach is useless: every entity's chunk closure is
  the whole chip, 132 chunks on big30x).
- `dfd7f83` — an Enter hydrates only the detached columns that claim a
  written path; `/explorer` composes `json.dumps(store.state)` from
  per-chunk texts (in-process big30x warm 375 -> 97 ms, after an edit 985 ->
  143 ms).
- `c51fd9e` + `da2f1b3` — GridVirt tail collapse: above 20,000 shown cells
  the maximal cold run at a grid's right end leaves layout via one
  `display:none` rule per `ck-N` class, a margin keeping the scroll range;
  `da2f1b3` re-collapses clean columns (no unapplied edit, no focus) once the
  user is two viewports left of the run.
- `fbdce27` — a chunked env analysis returns `types` as ordered parts.
- `fbd3e81` — templates compile on a daemon thread at startup. Profiled
  big30x first POST after `/load`: `field_edit` 17 ms, tray render 8 ms.

## 4. The far-right jump trade-off (known gap, accepted)

- What the user sees: on big30x's 2,389-column pair grid, dragging the
  scrollbar to the far right freezes the UI once for 3+ s while the browser
  lays out the columns the tail collapse took out. The page stays correct;
  scrolling back left re-collapses. An Enter while parked at the far right
  still pays the whole table's layout.
- Implementer-measured: bench jump 372 -> 3250 / 2928 ms. `enter_lean.cjs`
  (real Chrome), after one jump right and back: Enter qubit 551-615 ms / pair
  1277-1816 ms before `da2f1b3`; after it 3,590 columns collapse again and
  Enter is qubit 135 / pair 126 ms. Scroll range about 649k px throughout.
- Independent verifier measured: bench `hscroll_end.settled` 2812 (branch)
  vs 408 (base). `farright.cjs`, 3 fresh pages each, `scrollLeft =
  scrollWidth` (648,843 px): branch long tasks [3153], [3552], [3250] ms —
  ONE 3.2-3.6 s block, then 0.1-1.4 s tasks; base [339], [300], [206] ms
  plus a 0.6-0.8 s follow-up.
- Reach: only above the 20,000-shown-cell gate (synthetic big30x); krs5 and
  every real chip measured are gated off. The verifier judged it acceptable
  as a recorded known gap, not a blocker.
- Side effect (verifier): a collapsed column is `display:none`, so
  programmatic `scrollIntoView`, `elementFromPoint` and `UndoNav.visibleEl`
  ("go to field") treat it as absent until the user scrolls, Tabs or
  searches to it. Base had no input element there at all: equivalent.

## 5. Independent verification — PASS, no defects

- Server oracle vs integ/w7 (every request cold), two big30x scenarios, 8 + 3
  checkpoints (edits, undo, fill/paste, FSP, pointer and wiring retargets,
  subtree create/delete, stored-as-text, outside write + Take live / Pull &
  apply, chip switch and back, Apply to live): 0 differing cells over 202,640
  grid cells; all-values, diagnostics, type alarm, `/explorer/model`
  identical; `/topology` differed only in `historyCount` (200 vs 205, the
  instance's snapshot count).
- Real-Chrome journey, patched DOM vs a cold reload (2,640 cells per
  checkpoint on krs5, 20,324-20,774 on big30x): the same differences on
  branch and base (the transient "old -> new" hint). 0 page errors.
- Every claimed server number reproduced within or better than its range;
  undo repaint 1.26-1.37 s (claim 2.6-4.5 s). Write paths A/B: no consistent
  difference.

## 6. Integration

`store_revs.note` became the ONE mutation recorder (it also feeds the Pulses
journal); the lint / env memos keep coldopen's under-the-lock re-check keyed
on `store_revs.seq_token`. See docs/224.

## 7. Pins

- `tests/test_ram_liveedit.py`: randomized 240-260-step sequences (store
  events, lint/env, patched grids, topology, composed Json Tree text) equal a
  cold recompute after every step; shadow mode raises `StaleCacheError`;
  FSP -> dBm, splice byte-identity, template warm-up. Sweeps: 16/16
  (`2872154`), 8/8 (`a41f4ab`), 6/6 (`7c78831`), 3/3 (`dfd7f83`), 4/4
  (`fbdce27`), 2/2 (`fbd3e81`) mutations red. `tests/test_gzsplice.py`.
- `tests/liveedit_big_grid_selfcheck.cjs` via `test_liveedit_big_grid.py`:
  37 assertions, 13/13 red (`c51fd9e`); section 8, 51 assertions, 9/9 red
  (`da2f1b3`). `bulk_virt_server_selfcheck.cjs` /
  `pair_virt_server_selfcheck.cjs` (4/4) and `cellbtn_commit_selfcheck.cjs`.
- Verifier's own mutations, all red: FSP read dropped from `amp_annotation`;
  pointer strings treated as plain in `store_revs`; the re-collapse's
  dirty-column stop removed; `tailRecollapse` disabled.
- Implementer full cqt suite: 9904 passed, 1 failed, 251 skipped — the
  failure (`test_config_manual::test_per_key_help_affordances_exist`, grid
  heads moved into macros by `7c78831`) fixed in `021fff5`, 2/2 red.
  Verifier, 71 neighbouring files: 1384 passed, 2 failed (pre-existing
  `chip_status_resume_selfcheck` P3; a `pulses_undo_selfcheck` load flake,
  3/3 alone), 21 skipped.

## 8. Open / not done

- big30x misses Enter -> repaint <= 60 ms (verifier 130 qubit / 118 pair) and
  first `/topology` after an edit <= 80 ms (implementer 189-261; verifier 77,
  borderline).
- `/bulk` after an edit on big30x 0.7-1.3 s (implementer): edited rows
  re-render whole (a 2,389-column pair row is about 130 ms of Jinja).
- Not met: subnav <= 5 ms (10 krs5, 10-25 big30x); pair grid DCL <= 900 ms
  stretch (big30x 17-20 s, was 36 s). Column search first key on big30x
  0.9-2 s; all-values 200 16-34 s (only the 304 was a target).
- First POST after `/load` contends with the post-load `_decide` thread and
  config preview: wall 81 ms krs5, 289-562 ms big30x (implementer).
- Json Tree live-diff toggle on big30x (w7/livewrite's scope): implementer
  "about 13 s, unchanged"; the verifier's bench read 10921 (base) / 4992
  (branch).
- `73602f2`'s "a write to any other section recomputes" rule has no by-name
  reader in the fixture: its mutation stays green.
