# 283: Chip Status and Param History read the change ledger

S8 of the state-tracking hub, on `feat/hub-chip-status` (base `origin/main` `5c17996a`).
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md` §1.3, §3.6 ("How each
surface moves onto it") and §3.7 S8, read with [269](269_hub_rules.md), [270](270_hub_ledger_builder.md),
[271](271_every_sm_write_is_recorded.md), [275](275_the_ledger_keeps_itself_current.md),
[279](279_hub_read_side.md), [281](281_calibration_log_on_the_hub.md) and
[282](282_value_history_on_the_hub.md).

**Result.** Chip Status Trends, the per-metric meta, the Param History grid (and its cell drawer)
and Param History Changes now answer from the chip's change ledger through S7's one reader
(`routes._value_history` -> `value_history.read`): one series rule, one alias rule
(`holder_at`), one set of provenance words. A run is named as a value's writer only when its own
patch proves it -- for a value made of several leaves (a confusion matrix, an RB block) only when
EVERY leaf change at that event is proven. Golden check on copies of two real archives: every
point the old Trends, the old grid and the old Changes showed is contained (missing **0**, extra
points all classified, unexplained **0**). Metric-meta writer check: 100 / 100 sampled cells agree
with a brute-force check per archive; on Archive A the old meta named a run for 141 cells that the
new one says "writer not proven" (10 spot-checked by hand, below). 35 test functions, **57 / 57
mutations RED, 35 / 35 pins turned RED**; 85 related test files serially: 3,065 passed, 1 failed
(pre-existing, date-dependent, fails on the base too); the real-browser journey ALL OK (27
checks, 0 console errors). Warm: the meta 14x, the grid 1.7x, Changes 8x faster on a big chip
copy, 7-1,200x on a 10,000-run ledger; the one exception is +0.3 ms on a cached Trends fragment;
cold costs in §7. Counts: [283_hub_chip_status_checks.json](283_hub_chip_status_checks.json).

## 1. Spec (written before the implementation)

### 1.1 What moves

| Surface | Before | After |
|---|---|---|
| Chip Status Trends (`/topology/trends`) | `chip_trends_ram.table`: the curated `param_history` table and the leaf change-point index; a point's run from the snapshot provenance map, re-checked by `_WriterCheck`; S7's ledger read only for a typed path that crosses a pointer | `hub_status.LedgerTable`, the Trends table interface over `_value_history`: every path's in-force series (`effective`), every point S7's words; `FamilyTable` over the ledger's numeric paths |
| Chip Status metric meta (`/topology/metric-meta`) | cold `leaf_field_series_many` + `metric_meta.newest_change`; writer from `_WriterCheck` / `value_writer.attribute` | the newest change of the panel's leaves from the same read; the writer only on proof; SM writes name actor and kind |
| Param History grid (`/param-history`) + its cell drawer (`/param-history/expand`) | `extract_property_history` over the curated table | `LedgerTable.curated` (the same read); the drawer shows each point's words and opens a run only on proof |
| Param History Changes (`/param-history/changes`) + its path typeahead | `leaf_change_groups` (leaf index) | `hub_query.timeline` with a path-prefix filter; per-row provenance; `PathRank` over the ledger's paths |

### 1.2 Binding rules

* **One reader, one alias rule.** No surface builds a value history of its own: every series is
  S7's `effective` (the value in force through the path at each change, `holder_at` per
  position), read by `routes._value_history`. A metric derived from several leaves (a readout
  fidelity from its confusion matrix) is refolded from the leaves' in-force series with the
  snapshot index's own formula -- a display calculation, not a reader. The S7 helper that read the
  ledger for Trends alias paths only (`_trend_alias_series`) is removed: every Trends path is read
  the same way now.
* **The writer only on proof.** S7's `proven` decides. A several-leaf value is written by an event
  only when every leaf change at that event is proven; otherwise the words of an unproven leaf are
  shown. Other rows: "saved in #N, writer not proven", "first recorded in #N, ledger start; writer
  unknown", "#N (chip uncertain), not named as writer", "seen by SM (... snapshot), writer unknown",
  an SM write's actor and kind. A data link only on proof (and never for a deleted run folder).
* **Honest states.** building / preparing: S7's words, no rows, the surface asks again by itself;
  idle / degraded / deferred / no data folder: S7's notes on every surface. A building or preparing
  read is never cached as an answer.
* **Labelled fallback.** A chip whose ledger holds none of its runs keeps the old path, labelled
  ("Older snapshot history: ...") inside the swapped root; ledger and snapshot answers are never
  mixed.

## 2. What was built

| File | What |
|---|---|
| `web/hub_status.py` (new) | `LedgerTable`: `series_many` (S7's answer, cached per ledger state), `leaf_series_many`, `curated` (with the derived-fidelity refold), `leaf_families` / `leaf_matching_paths` (numeric ledger paths), `path_rank`; `source_of` (the grid's Source filter), `is_proven` / `representative` (the several-leaf proof rule). Cache token: ledger id + read version + newest event, the chip's `mutation_seq`, the dataset roots, the sync status. |
| `web/routes.py` | `_hub_status_table`, `_hub_waiting`, `_hub_surface_wait`; the four routes and the cell drawer dispatch on S7's mode; `_hub_metric_meta`, `_hub_grid_rows` / `_hub_grid_summary`, `_param_history_hub_ctx` (an archived chip reads its own ledger), `_hub_param_changes`, `_hub_param_history_expand`; the old bodies kept as the labelled fallback (`_legacy_topology_metric_meta`, `_legacy_param_history_changes`). |
| `core/value_history.py` | `target(container=True)` (a panel enumerates leaves through the one resolver); points carry `ord`; the in-force points get the per-path undone mark; `undone_paths` (the `_mark_undone` rule for every path of one event, for Changes) and the shared `_undo_takes_back`. |
| `core/hub_query.py` | `timeline(path_prefix=, event_id=, changed_only=)`. |
| `core/value_history.py` (read) | the ledger summary also reports the `version` it was read at, so a surface validates its cache without a second read snapshot. |
| `core/hub_index.py` | a chip folder's reader slot (its resolved path) is resolved once per spelling: `Path.resolve` was the largest single cost of a warm read on Windows. |
| `core/metric_meta.py` | `qubit_paths` / `pair_rb_paths` take the one resolver. |
| templates | `_hub_surface_wait.html` (+ page shell): the waiting line that asks again; the ledger notes / fallback label inside each root; the Changes group head + per-row "who" cell; the drawer's wait line and notes. |
| `static/chip-status.js`, `static/app.js`, `static/style.css` | the meta hover / tile tag on the ledger (`_describeLedger`), the panel summary words, the Trends hover words, the cell drawer's words and restore trace. |
| tests / tools | `tests/test_hub_chip_status.py`, `tests/hub_chip_status_selfcheck.cjs`, `tests/browser/journeys/hub_chip_status.cjs`; `tools/check_hub_chip_status.py` (golden + meta writer), `tools/faults_hub_chip_status.py`, `tools/perf_hub_chip_status.py`, `tools/mutate_hub_chip_status.py`. |

## 3. Review of the first draft

A first draft of this step existed uncommitted. Reviewed against the rules above; what changed:

| Draft | Why it was wrong | Now |
|---|---|---|
| Changes labelled every run group "saved in #N, writer not proven", even where the run's own patch set the row (its pin asserted exactly that) | under-claims a proven write and contradicts the value drawer for the same value | the group names the event ("run #N ..."); each row says "its own patch set it" or "writer not proven" |
| meta / Trends took the newest leaf point of a matrix metric as THE point | one proven leaf named the run as writer of the whole matrix | `is_proven` (every leaf change at the event) + `representative` (an unproven leaf's words) |
| derived fidelity events sorted by clock | an SM write whose clock trails its causal place was folded out of order | sorted by the ledger's order (`ord`) |
| fixed 2x2 / 3x3 matrix | a larger matrix would be read wrong | the size the ledger recorded |
| a building / preparing read was cached under the ledger token | the surface would stay "preparing" until the next commit | the compute raises; nothing is cached |
| every path's "the value now differs" note merged into the page notes | vague on a page, false on an archived chip (no current state) | page notes are the sync notes only |
| fallback label prepended outside the swapped root | stacks on every swap | inside the root |
| Trends wait line never asked again | stuck until the next scroll | asks again (`load delay`) |
| Changes page cached without the htmx flag | a full page could be served a fragment | keyed on it |
| grid Source filter keyed on the event kind | an SM apply was in no Source; the import gate counted ledger events | `source_of`; the gate keeps the snapshot counts |
| typeahead offered text families, `*` matched across segments | charts nothing; wrong family | numeric paths, whole segments |
| meta hover dropped the value-on-screen check, the lab's stamp and node run, the tile's run number | regressions of the old hover | kept |
| cell drawer still on the old index + `_WriterCheck` | the grid's own detail would name writers the ledger cannot prove | on the ledger |
| eleven fields per Trends point | 4.84 MB on the 10,000-run chart (the old one: 2.09 MB) | only what the hover / click read: 1.62 MB |
| every cached part deep-copied on every request; the ledger's own path / family tables keyed on the chip's edit counter | warm Trends measured slower than the old path; an edit rebuilt the family table | parts shared read-only (as `chip_trends_ram`'s are), a ledger-only token for ledger-only parts, the dataset roots resolved only on a miss |
| harness: writer sample with 0 proven cells; spot checks all on one run | the positive direction was never checked | patched window, stratified sample, 10 distinct runs |

## 4. Golden comparison

`tools/check_hub_chip_status.py golden` on byte copies (`shutil.copy2`, never a link) of 200
consecutive runs of each archive (`node.json`, `data.json`, the saved pair), the window chosen to
hold the most runs whose patches set a Chip Status metric (so proven writers occur), plus 20
outside edits seen by an `auto` capture (S7's injector) and 6 SM writes through the real doors (2
undone). The old side is the unchanged old code on the same copy (Param History backfilled by its
own importer); the new side is the S8 adapters the routes call. An old point (value V at its
snapshot) is contained when the new surface's series has V in force at the event that snapshot
shows (a run's key, a capture's key, an SM copy mapped by content hash). Copies deleted after.

| | Archive A | Archive B |
|---|---:|---:|
| ledger events / Param History snapshots | 228 / 185 | 225 / 148 |
| sampled leaf paths (alias / pointer leaf / long-array element / leaf) | 200 (40 / 20 / 20 / 120) | 200 (40 / 20 / 0 / 140) |
| **Trends, curated**: old points contained | **1,160 / 1,160** (673 same event, 487 dated earlier) | **1,424 / 1,424** (807 / 617) |
| **Trends, leaf tier** (200 paths) | **230 / 230** (125 / 105) | **176 / 176** (121 / 55) |
| **Param History grid** (every snapshot of 12 metrics) | **33,670 / 33,670** (678 / 32,992); 12,950 old cells showed no value | **11,988 / 11,988** (807 / 11,181); 3,996 empty |
| **Changes** (sampled + curated holders) | **549 / 549** (545 / 4) | **829 / 829** (818 / 11) |
| Changes, every path | 12,266 / 12,266 | 3,167 / 3,167 |
| **missing** | **0** | **0** |

Why "dated earlier" (each classified): grid -- a snapshot after the change, the value still in force
(the old grid is dense); Trends -- the old change-point read kept BOTH edges of a value's
unchanged stretch plus a held endpoint (A 477 + 105, B 608 + 54), the changing run has no Param
History snapshot (A 9 + 3, B 8 + 1 + 10), a return to an earlier state or the change right after
one (A 1 + 1, B 1 + 1). Changes also read 2,732 / 0 long-array elements out of their array's row
and 1,463 / 1,204 pointer leaves at the holder they named.

Extra points the new surfaces show, all classified (no "unexplained"):

| | A Trends / grid | A leaf | A Changes | B Trends / grid | B leaf | B Changes |
|---|---:|---:|---:|---:|---:|---:|
| a curated alias the old index recorded as empty (`readout_amplitude`) | 52 / 52 | | | | | |
| a return to an earlier saved state (Param History's dedup drops it) | 20 / 20 | | 9 | 9 / 9 | 1 | 11 |
| the change right after such a return | 2 / - | | 4 | | | |
| SM writes | 2 / 2 | | 2 | 2 / 2 | | 2 |
| text / booleans / removals (the old tiers chart numbers) | | 37 / 40 | 2 | | 57 / 64 | |
| an alias path the old leaf tier never held | | 30 | | | 58 | |
| an outside edit SM saw | | | | | 3 | |

## 5. Metric meta: the writer, by brute force

100 meta cells per archive, every provenance class sampled first (up to 10 each), the rest at
random. Brute force, independent of `value_history`: for the cell's leaves at the named event, the
run's own `node.json` patches must set every changed leaf to exactly the ledger's value (and the
run not of another chip); an SM writer must have an S4 journal line whose entries set those
leaves to those values, with the same actor and kind.

| | Archive A | Archive B |
|---|---:|---:|
| sampled (proven run / saved, not proven / first record / SM write) | 100 (16 / 41 / 37 / 6) | 100 (78 / 2 / 15 / 5) |
| **agree with brute force** | **100** | **100** |
| cells the OLD meta named a writer run for | 178 | 93 |
| -- the new meta names the same run | 37 | 93 |
| -- the new meta names another run | 0 | 0 |
| -- **now "writer not proven"** | **141** | 0 |

**Spot checks by hand (Archive A, 10 distinct run x metric cases, read in the run folders).** In
all 10 the named run's `node.json` holds no patch at all, and the value changed in that run's
saved state. In 9 the run targeted that qubit with a node of the metric's own family (a T1, echo,
Ramsey or readout node): a plausible writer, not a provable one. In 1 the old meta named a
power-Rabi run as the writer of a qubit's readout frequency although that qubit was not among the
run's targets -- the confident wrong answer the rule exists to stop. Evidence kept out of the repo.

## 6. Fault injection: what each surface says

`tools/faults_hub_chip_status.py` (synthetic chips; the real mechanism wherever one exists).

| Fault | Trends | Metric meta | Param History grid / cell drawer | Changes |
|---|---|---|---|---|
| a building ledger (status 2 of 9) | "The change history is being built (2 of 9 runs). It shows here when it is complete." -- no rows, asks again | the same sentence, `updating` | the same, asks again | the same, asks again |
| an idle ledger | "The ledger is not being kept current in this window; runs after <time> may be missing." | the same on the panel line; cells keep their words | the same note | the same note above the groups |
| a run of another chip identity whose patch sets T1 | "#5 (chip uncertain) / not named as writer" | "#5 (chip uncertain) / not named as writer", no writer run | the same, opens nothing | "#5 (chip uncertain)", every row "not named as writer", no Data |
| a pointer retargeted mid-history | the alias drawn 0.3 then 0.25 (each holder's value while it was named), "saved in #4 / writer not proven" | "saved in #4 / writer not proven" | the same, opens nothing | "run #4", the pointer row and the value row "writer not proven" |
| an SM write later undone | "applied by operator (undone)" then "undo by operator" | "undo by operator / SM write (ctrl_z)" | the same | "applied by operator (undone)", the row struck through |
| a NaN-only metric | "No finite numeric value recorded; there is no numeric trend to draw." | `nonfinite: nan`, no value, "first recorded in #1 / writer unknown" (valid JSON) | no numeric point | the rows, as recorded |
| a deleted run folder | "#2 / its own patch set it / run folder deleted", no link | the writer stays #2 (the proof survives), flag, no link | the same, opens nothing | "run #2 ... run folder deleted", no Data |

Each row is also a pin in `tests/test_hub_chip_status.py` (§9).

## 7. Performance

`tools/perf_hub_chip_status.py`: each surface through the Flask test client (server time, no
network), the same chip for both sides in one process. "Before" is the old body exactly
(`_hub_status_table` stubbed to answer `fallback` at zero cost); "after" is the ledger. Cold = each
side's first request after every RAM cache and read index was dropped; warm = the median of the
following passes, **the two sides alternating pass by pass** (a first version measured all of
"before" then all of "after" and read drift on the shared PC as a regression). The PC had another
campaign's work running; numbers move by tens of percent between runs, the ratios hold.

**Big chip copy** (30 qubits, 33,490 ledger paths, 20 runs; 15 warm passes), ms:

| Surface | cold before | cold after | warm before | warm after | bytes before -> after |
|---|---:|---:|---:|---:|---|
| Chip Status page (`/topology?view=trends`) | 680 | 21 | 20.6 | 20.0 | 490 KB (same) |
| its Trends section (`/topology/trends`) | 1,713 | 1,066 | 1.8 (p90 3.8) | 2.1 (p90 2.5) | 57 -> 66 KB |
| metric meta | 262 | 940 | 68.6 | **4.8** | 33 -> 203 KB |
| Param History grid (`since=all`) | 145 | 887 | 32.3 | **18.6** | 373 -> 221 KB |
| Param History Changes | 946 | 1,557 | 24.4 | **3.1** | 226 -> 169 KB |

**Synthetic 10,000-run ledger** (32 qubits, one T1 moved per run, the same runs backfilled into
Param History; 9 warm passes), ms:

| Surface | cold before | cold after | warm before | warm after | bytes before -> after |
|---|---:|---:|---:|---:|---|
| Chip Status page | 558 | 33 | 37.9 | 36.2 | same |
| Trends section | 41,188 | 22,320 | 1,486 | **62** | 2.09 -> 1.62 MB |
| metric meta | 35,841 | 24,981 | 37,248 | **31** | 21 -> 56 KB |
| Param History grid | 56,570 | 2,042 | 741 | **56** | 1.60 -> 0.53 MB |
| Param History Changes | 104 | 514 | 16.2 | **2.3** | 152 -> 151 KB |

**Warm**: faster on every surface except the big chip's Trends section, a cached fragment, where
the median is +0.3 ms (p90 lower): that is the per-request cost of asking the ledger its mode and
version (S7's `_value_history` with no paths, one read snapshot). Two equivalence-preserving
changes brought it there from +0.6 ms: the table validates its cache on the version S7's read
reports instead of a second snapshot, and `hub_index` resolves a chip folder's reader slot once.

**Cold** (every cache dropped, so it includes S6a's RAM index build): slower than the old path on
the big chip for the meta, the grid and Changes (0.9-1.6 s instead of 0.15-0.95 s) -- every path the
surface needs is read once through S7's reader; faster for Trends; on the 10,000-run ledger the
Trends section and the meta take 22-25 s cold (the old path 36-41 s), with the in-process app's own
background threads (the ledger projector, Param History) running alongside. After the first request,
a part is recomputed only when the ledger's version, the chip's edit counter, the dataset roots or
the sync status change; the ledger-only parts (path and family tables) only when the ledger
changes.

**Per-point payload.** A Trends point carries its own words (label, the "who" line, flags, a data
link only on proof); the first version shipped eleven fields per point (4.84 MB on the 10,000-run
chart, 2.3 x the old 2.09 MB). Trimmed to what the hover and click read, it is 1.62 MB.

## 8. Real browser

SM served from this worktree on **5163** (waitress, the test env) against a **copy** of the agent
rig's chip and data folder (`extras.data_folder` pointed at the copy, network `127.0.0.1:1`,
sandboxed `HOME` / `USERPROFILE`, no qualibrate config), plus four synthetic runs: #5 sets qA1 `T1`
by its own patch, #6 moves qA2 `T1` with none, #7 sets qA1 `T1` by its patch and moves qA3 `T1`
without one, #8 is a run of another chip identity. Headless Chrome on CDP **9483**
(`--remote-allow-origins=*`, a scratch profile), real mouse events through `cdp.cjs`; journey
`tests/browser/journeys/hub_chip_status.cjs`: **ALL OK, 27 checks, console errors 0.**

| Step | Seen |
|---|---|
| Chip Status > Trends | charts from the ledger, "8 recorded events", no fallback label (`01_trends.png`) |
| hover an unproven point | "qA2 ... saved in #6 06_T1 / writer not proven"; a click opens nothing (`02_trends_unproven_hover.png`) |
| isolate qA1 (legend double click), hover, click its #5 point | "#5 05_T1 / its own patch set it / click to open the dataset"; run #5 opens in the inspector (`03_trends_click_opens_run.png`) |
| T1 panel, Show Meta Info, hover tiles | tile tags "10-03 18:10 · #7" (proven) and "10-03 18:05" (no run); qA2 "Recorded: saved in #6 06_T1 -- writer not proven"; qA1 "Written by: #7 07_T1 -- its own patch set it"; panel line "1 proven run write, 3 recorded changes, 17 first recorded, writer unknown (of 21)" (`04_meta_unproven.png`, `05_meta_proven.png`) |
| sidebar > Param History, the qA2 T1 cell | sparklines from the ledger; the drawer: "saved in #6 06_T1 · writer not proven", no click hint (`06_grid.png`, `07_grid_drawer.png`) |
| Changes tab | "#8 (chip uncertain)", rows "not named as writer"; "run #7 07_T1": qA1 T1 "its own patch set it", qA3 T1 "writer not proven" (`08_changes.png`) |
| Back, reload | the page is whole (`09_after_back.png`, `10_after_reload.png`); console errors 0 |
| detail shots after the journey | the hover cards of both kinds, the tiles with Show Meta Info on, the grid rows, the cell drawer's hover "saved in #6 06_T1 · writer not proven" (`02b_*`, `05b_*`, `06b_grid_rows.png`, `07b_grid_drawer_hover.png`) |

The first pass found two display defects, both fixed: the per-row "who" cell wrapped one word per
line under a long value ("writer / not / proven"), and the panel line said "1 proven run writes".

## 9. Pins, mutations, tests

`tests/test_hub_chip_status.py`: **35 test functions** (54 cases with the per-surface
parametrisation) on synthetic chips through the real routes, plus `tests/hub_chip_status_selfcheck.cjs`
(25 jsdom checks of the meta hover / tile tag, the Trends hover and click, the cell drawer),
driven by `test_the_chip_status_selfcheck`. No customer data, no existing test edited.

`tools/mutate_hub_chip_status.py`: one source edit at a time (exactly one occurrence of its
anchor, in the file's own line ending), the pins it claims run, the bytes restored in `finally`
and read back; RED only on a failed assertion in a targeted pin's own traceback (never a
collection, syntax or runtime error). **57 / 57 RED; 35 / 35 test functions turned RED.** The first
sweep counted six survivors whose pins failed on a `KeyError` / `IndexError` rather than an
assertion, and one vacuous pin (the archived-chip grid: both chips had the same chip name, so the
"other" chip was the same chip); each pin now asserts what it checks before indexing, and the
archived chip has its own name. Results: [283_hub_chip_status_mutations.json](283_hub_chip_status_mutations.json).

**Existing tests**, the test env, `--timeout=900`, three files per process, serially: every test
file that mentions Trends, the metric meta, Param History, the leaf index, `value_writer`,
`chip_trends_ram`, the topology routes, the value history / field / column history, plus every
`tests/test_hub_*.py`: **85 files, 3,065 passed, 44 skipped, 1 failed.** The one failure,
`test_calibration_log_hub.py::test_report_lists_an_agent_only_day_and_says_when_history_is_building`,
fails identically on the base commit in a clean worktree (it hard-codes today's date). After the
last edits, again: the S8 and S7 files (96 passed); the ten closest files (metric meta, Trends
provenance / writer, Param History Changes / RAM, `hub_query`, Chip Trends + RAM table, the
calibration log, `test_web`): 847 passed, 19 skipped, the same 1 failed; every `tests/test_hub_*.py`
plus the calibration log after the `hub_index` change: 462 passed, the same 1 failed.

## 10. What S10 can delete, and when

Same condition as S7 (docs/282 §9): after the user's full-day verification **and** once no chip
reaches the fallback (every chip's runs reach its ledger; today a chip with no data folder linked
still does).

Deletable then (S8's surfaces no longer read them):

| Code | Note |
|---|---|
| `routes._legacy_topology_metric_meta`, `_METRIC_META_SNAP_CACHE`, `metric_meta.newest_change` / `verify_truncated` / `snapshot_values` / `_follow` | the old meta |
| `routes._WriterCheck`, `_trend_point_writers`, `_trend_run_folder_resolver`, `core/value_writer.py` (check `story.py`'s last use first) | the writer re-check |
| the old branch of `topology_trends` / `_topology_trends_html` (snapshot provenance map, `chip_trends_ram` table), the curated-vs-leaf dedupe | Trends' old tiers |
| `routes._legacy_param_history_changes`, `_PH_CHANGES_MEMO`, the old branch of `param_history_param_search` | Changes |
| the `extract_property_history` branches of `param_history` and `param_history_expand` (and its `_WriterCheck` loop) | the grid |
| the `fallback` branches of these routes and their `hub_fallback` template lines | the label |

**Not deletable by this step alone** (still read elsewhere): `chip_trends_ram` and
`_snapshot_provenance_map` (the chip report's Trends section, `_report_build_trends`);
`extract_property_history` and the curated `param_history` table (`/api/topology/sparklines`,
Auto Calibrate's drift gate via `column_history`); `leaf_index` / `leaf_change_groups`
(`story._writes_for_run`, `param_history_ram`).

## 11. Residual risks and hand-over

1. **A run of an uncertain chip identity stays in the in-force series** (S5's model, S7 residual 7):
   Trends and the grid sparkline draw its value, labelled "#N (chip uncertain) / not named as
   writer" on hover. Taking it out of the chain is a ledger decision.
2. **The grid's Source filter is a classification**, not a writer claim: a run event is
   "Experiment" whether or not its patch proves the value (its words say which).
3. **A pointer leaf's own Changes row** shows the pointer string; a change of the value it points
   at is a row of the target holder (S2 records holders). The golden check reads 1,463 / 1,204 such
   old rows at the holder they named.
4. **Cold cost**: the first request after a ledger change reads every path the surface needs
   through S7's reader (§7).
