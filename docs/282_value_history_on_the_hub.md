# 282: The per-value history reads the ledger

S7 of the state-tracking hub, on `feat/hub-drawer` (base `origin/main` `1ef8983c`).
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md` §3.1, §3.2 (path keys,
pointer and alias rules), §3.6 ("How each surface moves onto it") and §3.7 S7, read on
2026-10-05 with [269](269_hub_rules.md), [270](270_hub_ledger_builder.md),
[271](271_every_sm_write_is_recorded.md), [275](275_the_ledger_keeps_itself_current.md) and
[279](279_hub_read_side.md).

**Result.** The value drawer, Column History and the agent's field-history now answer through one
function on the chip's change ledger; Chip Status Trends uses it for an alias path. Golden check on
copies of two real archives: every old change point is contained (drawer 289/289 and 222/222,
Column History 229/229 and 204/204, missing 0), every extra ledger point classified. One alias
path that gave three different answers now gives one. Fault injection pinned by what the drawer
says. 24 pins, 38/38 mutations RED, 60 existing test files green, a real-browser journey ALL OK
with 0 console errors. Warm drawer 2-3 ms (was 10-16 ms); the first open after a ledger change on a
very large chip is 2.6 s (§7, §10).

## 1. Spec (written before the implementation)

### 1.1 What moves

| Surface | Before | After |
|---|---|---|
| 🕘 value drawer (`GET /field/history`) | `HistoryManager.field_history`: curated SQLite tier, then the leaf index, then a 150-snapshot scan, merged with a 60-run scan of the workspace runs (`_runs_field_series`); run names checked by `_WriterCheck` | `value_history.read` over the chip's ledger |
| Column History (`POST /bulk/column-history`) | `HistoryManager.column_history` (curated tier or a 40-snapshot scan) merged with `_runs_column_series` (60 runs) | the same `value_history.read`, all rows from one read snapshot |
| Agent API (`GET /api/agent/field-history`) | `HistoryManager.field_history(path, alias)`: no alias resolution, no runs tier | the same `value_history.read`, returned as JSON |
| Chip Status Trends, a typed path that crosses a pointer | nothing (the leaf index never holds an alias path) | the same `value_history.read` for that entity path (only this case; the rest of Trends is S8) |

**One implementation.** `core/value_history.py` is the only builder of a per-value history
from the ledger. It reads through `hub_index.snapshot` and `hub_query`'s series reader (S6a); it
adds no second series builder, no second pointer resolver and no second equality. Every surface
above calls one route-level function, `routes._value_history`, which picks the mode (below) and
calls `value_history.read`. The surfaces differ only in how they draw the answer.

### 1.2 Path keys, pointers and aliases (DESIGN 3.2)

A requested dot-path is resolved **once, now**, against the open chip's merged document through
`pointer_path.resolve_field_target`, the one resolver:

* **resolvable** -> the value's holder is `resolved_path` (mid-path pointers and leaf pointers
  followed, `#./` included);
* **not resolvable, ends on a pointer string** (a runtime alias such as
  `#./inferred_intermediate_frequency`, or a pointer naming nothing) -> the holder is that
  pointer's own holder; its history is the history of the pointer string;
* **not resolvable, a missing key** -> the literal requested path (what was once there).

The holder is spelled as the S2 holder path (`hub_rules._segment` per segment). The answer carries
`via = [{from, pointer, to}]`, one entry per pointer hop, and `retargets`: every ledger row of each
hop's pointer holder (`set` / `add` / `gone` / `retarget`). A value row older than the newest
row that gave a hop its current pointer string is marked `before_via`: the alias did not name this
holder then, and the drawer says so on that row. The value history is never stitched across a
retarget (that would be a second resolver, per snapshot, which is exactly how the surfaces came to
disagree).

**Long arrays.** A path that names an element of a scalar list longer than 16 (one S2 holder)
is read from the array holder's rows, each array version loaded from its blob and the element
taken; an array version that leaves the element unchanged is not a change of the element. Rows of
the per-element holder (while the list was 16 or shorter) are merged in the same pass, per event.

### 1.3 Modes: never a partial history presented as complete

| Mode | When | What every surface says |
|---|---|---|
| `building` | `hub_sync.status` says `building` (a catch-up, a moved folder, a run in hand, a retry due), or the read raised `hub_sync.Building` | "The change history is being built (n of N runs)". No rows. The drawer and Column History ask again by themselves. The agent gets `history: null` and the same note. |
| `preparing` | the read raised a plain `ramcache.Warming` (the RAM index is being built by another request, or a commit raced the read) | "Preparing the change history…", asks again. |
| `ledger` | a ledger file exists and covers runs (it holds a run event, or a data folder is registered for the chip) | the ledger rows, with the notes below |
| `fallback` | no ledger file, or a ledger that holds only SM writes and no data folder is linked (so it knows no runs) | the **old path, unchanged**, under a label naming why: "Older snapshot history -- ...". |

Notes in `ledger` mode (from `hub_sync.status` and the ledger itself), shown on every surface:
`degraded` (a data folder cannot be read, or runs could not be ingested: named), deferred runs
("the newest run is still being saved"), `idle` (no sync in this window: "runs after <time> may be
missing"), and a current value that is not the newest recorded value ("not applied yet, or changed
outside SM since the last run").

### 1.4 Provenance (the binding "never show wrong provenance" rule)

Each row names what the ledger can prove, and nothing more (label / the visible second line):

| Row | Shown as | Data link |
|---|---|---|
| run event, `CHIP_UNCERTAIN` | "#N (chip uncertain)" / "not named as writer" | no |
| run event, `proven` (its own `node.json` patch set exactly this leaf to exactly this value) | "#N `<experiment>`" / "its own patch set it" | yes, unless `SOURCE_GONE` |
| run event, first in the ledger (`base_hash` is null) | "first recorded in #N" / "ledger start; writer unknown" -- the value was already set when the ledger begins | no |
| run event, not proven | "saved in #N `<experiment>`" / "writer not proven" (the run's saved state was the first to carry it; a change made outside SM before the run lands here too, by the user's binding decision) | no |
| SM event | its kind and actor: "applied by operator", "agent x" (an agent's own write) or "approved by operator (agent plan)" (a person approving a plan; the plan id in the title), "Auto Calibrate", "restored by ...", "undo by ...", "redo by ...", second line "SM write (`<door>`)"; a `dataset_apply` adds "(a run's state)" | only the applied run (`run_uid`), labelled as such |
| SM event later undone | the same, struck through, "undone" / "partly undone" | as above |

Flags shown on the row: `SOURCE_GONE` ("run folder deleted"), `REWRITTEN` ("run folder rewritten
after the run"), `OVERLAPS_SM_WRITE` ("this run's save went over an SM write"),
`REVERTS_TO_EARLIER` ("returned to an earlier saved state"), `NODE_UNREADABLE`, `TIME_ASSUMED`.

`_WriterCheck` / `value_writer` are not used on the ledger path: the ledger's `proven` is the proof.

### 1.5 Column History on the ledger

* **Changes** tab: each row's own change points from the same `value_history.read`, newest first
  (6 shown, the count of the rest stated).
* **By run** tab: the newest 6 successful run events of the ledger, each row's value at that run
  folded from the same rows (the value of the newest row at or before the run). A run event's
  value is its own saved value (ledger invariant I2), so the fold is exact for runs.

### 1.6 Checks (DESIGN S7) and the pipeline

* (a) Golden comparison on 200 random paths per chip on copies of two real archives: the new
  series must contain every change point the old drawer and old Column History showed; every
  extra ledger point is classified.
* (b) One alias path whose drawer, Column History and Chip Status Trends answers disagreed before
  and agree now.
* (c) Fault injection, checking what the drawer SAYS: a ledger missing a run, a building ledger,
  a pointer retargeted mid-history, a deleted run folder (`SOURCE_GONE`), an SM write later undone.
* Pins in `tests/test_hub_drawer.py`, every pin mutation-checked; a jsdom selfcheck for the
  drawer/column retry JS; every existing test that touches these surfaces; a real-browser journey;
  drawer server time before vs after on a big chip copy and a 10k-run synthetic ledger
  (target: no slower, < 150 ms warm).

(Results follow in §2-§10.)

## 2. What was built

### 2.1 Files

| File | What |
|---|---|
| `core/value_history.py` (new) | `target(merged, path)`: the one resolution of a requested path (holder, `via`, long-array element, the current value). `read(chip_dir, targets, limit=, runs=)`: every target's rows, the hops' rows and the By-run values from **one** `hub_index.snapshot`, through `hub_query`'s series reader. `provenance()`, `notes()`. |
| `web/routes.py` | `_value_history` (the one function every surface calls: mode, targets, read, notes), `_vh_present` (what a row says about who set it), the drawer / column / agent views, `_trend_alias_series`; `field_history` and `bulk_column_history` dispatch on the mode; their old bodies moved, unchanged, into `_legacy_field_history` / `_legacy_column_history` (the fallback). |
| `web/agent_api.py` | `/field-history` answers through `_value_history` (`source: ledger` / `building` / `preparing` / `snapshots`). |
| `templates/_field_history_ledger.html`, `_column_history_ledger.html`, `_value_history_wait.html` (new) | The ledger drawer, the ledger Column History, the "being built" / "preparing" line. The old templates gained one line each: the fallback label. |
| `static/app.js` | `FieldHistory` and `ColumnHistory` ask again while a `[data-vh-retry]` line is shown, only for the newest open and only while the panel is open; a slow answer for an older open is dropped. |
| `static/style.css` | `.vh-*` rules (notes, via line, flags, undone, before-via). |
| `core/hub_store.py`, `core/hub_index.py` | Two equivalence-checked speedups of the S6a index build (§7). |
| `templates/_qubit_detail.html`, `_pair_detail.html`, `_pulse_params_section.html` | The clock button's title no longer says "from Param History snapshots". |
| `tests/test_hub_drawer.py`, `tests/vh_retry_selfcheck.cjs` (new) | Pins (§8). |
| `tests/browser/journeys/value_history_drawer.cjs` (new) | The real-browser journey (§6). |
| `tools/check_hub_drawer.py`, `tools/mutate_hub_drawer.py` (new) | The golden / alias / performance harness and the mutation sweep. |

### 2.2 Decisions taken while building

* **A pointer to a whole object** (`x180 = "#./x180_DragCosine"`, opened on the `x180` cell itself):
  the value that path holds is the pointer string, so its history is the pointer holder's own rows
  (its retargets), not the empty history of the object it names.
* **A pointer edited in the working copy but not applied**: the hop's newest ledger row is not the
  pointer the chip holds now, so every value row predates it; the drawer says "this pointer is not
  recorded yet" and marks every row.
* **The fallback covers "a ledger with no runs"** as well as "no ledger": a chip whose ledger holds
  only SM writes and has no data folder linked knows none of its runs, while Param History (fed by
  every workspace folder) does. Showing the ledger there would drop the runs the person saw before.
  It is labelled ("this chip's change ledger holds no runs (no data folder is linked to the chip)").
* **Long-array elements**: S2 stores a long array's integral floats as integers (`1 == 1.0`), so an
  element reads `7`, not `7.0`; the value is equal under the one equality.
* **Chip Status Trends** changed only where it drew nothing before: a typed path whose leaf-index
  read is empty and that crosses a pointer is read through `_value_history`. Trends' snapshot
  provenance map and its `_WriterCheck` are untouched (S8).
* `_WriterCheck` / `value_writer` are not used on the ledger path; the old path keeps them.

## 3. Golden comparison (check a)

`tools/check_hub_drawer.py golden` on byte copies (`shutil.copy2`, no links) of the newest 300 run
folders of two real archives, `node.json` + `data.json` + the saved pair only, into the scratchpad;
the chip is a copy of the newest run's saved pair with its `extras.data_folder` pointed at the copy.
SM runs in-process (`create_app(testing=True)`, the ledger synced inline on `/load`). The **old**
side is what the old surfaces read on such a chip: Param History backfilled from the same copied
folder (the Param History page's own `backfill_from_workspace`, its data-folder question answered
"same"), plus their runs tier over the same folder. Old drawer = `_legacy_field_history`, old
Column History = `_legacy_column_history` -- the unchanged bodies. New = `_value_history`, unlimited
(containment) and with the drawer's 40-row limit ("shown").

200 paths per chip, seed 282: up to 40 alias paths (`<channel>.operations.<alias>.<field>` through a
`#./` operation alias), up to 20 pointer leaves, up to 20 elements of long arrays, the rest random
scalar leaves. Every path answered in `ledger` mode. Each old change point (value V at its
snapshot or run key) is looked up at that event in the ledger's fold:

| | Archive A (+09:00, ~1 MB states) | Archive B (-04:00, ~0.3 MB states) |
|---|---:|---:|
| ledger events / Param History snapshots | 300 / 203 (97 identical states skipped by its dedup) | 291 / 175 (+9 stateless runs, see below) |
| paths (alias / pointer leaf / long-array element / leaf) | 200 (40 / 20 / 20 / 120) | 200 (40 / 20 / 0 / 140) |
| **old drawer points contained** | **289 / 289** | **222 / 222** |
| -- same value at the same event | 202 | 134 |
| -- same value, the ledger dates the change earlier | 87 | 86 |
| -- the raw pointer string, found in the answer's `via` rows | 0 | 1 |
| -- absent on both sides | 0 | 1 |
| -- missing | **0** | **0** |
| old drawer points inside the new drawer's 40 shown rows | 289 / 289 | 220 / 220 matched to a row |
| **old Column History chips contained** | **229 / 229** | **204 / 204** |
| -- same event / dated earlier / pointer string / absent | 36 / 193 / 0 / 0 | 9 / 193 / 1 / 1 |
| -- missing | **0** | **0** |

**Why the ledger dates 87 + 86 drawer points (and 193 + 193 chips) earlier.** Each sub-class was
checked (the harness records up to four examples per class):

* drawer A 87, B 85; Column History A 193, B 193: the old point is the **oldest** point the old
  surface showed, i.e. the start of its window -- the scan tier's 150-snapshot cap for the
  strings and `None` values the leaf index drops (`digital_marker`, `id`), and Column History's
  40-snapshot scan. The value was already there; the ledger's row is at the archive copy's first
  run, or at the run that really changed it.
* drawer B 1: the run that changed it has **no Param History snapshot** (its state equals an
  earlier one, flagged `reverts_to_earlier`, and Param History's global hash dedup dropped it), so
  the old drawer saw the change only at the next snapshot.

**The extra points the ledger adds**, all classified:

| | A drawer | A column | B drawer | B column |
|---|---:|---:|---:|---:|
| older than the old surface's window | 0 | 64 | 1 | 19 |
| a return to an earlier saved state (Param History's dedup drops it) | 4 | 0 | 0 | 0 |
| any other | 0 | 0 | 0 | 0 |

The four "returns" are a long parking waveform element alternating between two values run to run
(the two-state alternation DESIGN §2.2 measured): the old drawer showed one of the four
transitions. Archive B's nine extra copied folders have no saved state; the ledger holds them back
as `deferred` (their folders were written seconds before the check, `FRESH_S`), and the drawer
says so ("9 newest runs are still being saved") -- they carry no value, so containment is
unaffected.

Machine-readable counts (no paths, values or names): [282_value_history_checks.json](282_value_history_checks.json).

## 4. Alias paths agree (check b)

The same alias path read by the three surfaces, before (the old code paths, run on the same
copy) and after:

| Surface | Before | After |
|---|---|---|
| Value drawer | 17 change points (its leaf-index tier on the holder the alias names now) | 17 change points |
| Column History | 3 chips (its 40-snapshot window) | the same 17 change points |
| Chip Status Trends, the typed path | **nothing** ("Nothing recorded": the leaf index keys holders, never the alias) | the same 17 points |

The path is a qubit's `resonator.operations.readout.threshold`, where `readout` is
`"#./readout_square"` (archive A copy; `readout_square.threshold` is the holder). Before: three
different answers. After: one list of 17 `(event key, value)` pairs, identical on all three
(`after_agree: true`). The same holds for a never-changed alias (`readout.amplitude`: before
1 / 1 at a different time / nothing; after 1 / 1 / 1, identical) and, on archive B, for
`xy.operations.x180.length`. Pinned on a synthetic chip by
`test_chip_trends_charts_an_alias_path_with_the_drawers_points` and
`test_the_agent_reads_the_same_points_in_the_same_words`.

## 5. Fault injection (check c): what the drawer SAYS

Each case is a pin in `tests/test_hub_drawer.py` on a synthetic chip whose declared data folder
holds four runs (#1 genesis; #2 sets `T1` by its own patch; #3 moves `f_01` with no patch; #4
retargets `x180` from `x180_Gauss` to `x180_DragCosine`). The channel is driven by the real
mechanism wherever one exists (a run folder written without its state, a data folder moved away,
a folder deleted then swept, real `/field/edit` + `/state/apply-to-live` + `/undo` presses);
only the two RAM states (`building`, `Warming`) are injected at the status/read seam.

| Fault | Injected by | The drawer says |
|---|---|---|
| A run the ledger does not hold yet (in flight) | a newest run folder with `node.json` and no saved state, then a watcher tick | the recorded rows, and "1 newest run is still being saved and not in this history yet." |
| Runs the ledger cannot read (data folder offline) | the data folder moved away, full sweep (`degraded`) | the recorded rows, and "This history may be missing changes: a data folder cannot be read (`<path>`)." No row is called deleted. |
| A building ledger | `hub_sync.status` = building 2/9 | "The change history is being built (2 of 9 runs). It shows here when it is complete." -- no rows; asks again every 2 s. Column History the same; the agent gets `source: building`, `history: null` and the same sentence. |
| Building found inside the read | `hub_sync.Building` from the read | the same sentence (1 of 4 runs) |
| RAM index being prepared | `ramcache.Warming` from the read | "Preparing the change history…", asks again after 0.8 s |
| A pointer retargeted mid-history | run #4 retargets `x180` | "via `x180` → `x180_DragCosine` · pointed here since <time> (saved in #4 scan, was `#./x180_Gauss`)"; the three rows from runs #1-#3 are dimmed with "before x180 pointed here" |
| The same pointer edited, not applied | `/field/edit` of `x180` to `#./x180_Gauss` | "via `x180` → `x180_Gauss` · this pointer is not recorded yet (an edit not applied to the chip): every row below is from before it"; every row marked |
| A pointer to a whole object | the drawer opened on `x180` itself | the pointer string's own history: `#./x180_DragCosine` "saved in #4", `#./x180_Gauss` "first recorded in #1"; no via line |
| A deleted run folder (`SOURCE_GONE`) | run #2's folder deleted, full sweep | the row stays ("#2 scan", its own patch set it) with the flag "run folder deleted" and **no** Data button |
| An SM write later undone | edit + Apply as `operator`, then Ctrl+Z | newest row "undo by operator" (the restored value); the next row "applied by operator (undone)", struck through, title "A later undo took this write back." |
| An agent's own write; a person approving an agent's plan | Apply with `X-SM-Agent: helper`; Apply with `X-SM-Actor: operator` + `X-SM-Plan: p-1` | "agent helper"; "approved by operator (agent plan)", the plan id in the title |
| A value not yet recorded | an unapplied edit in the working copy | "The value now is not the newest recorded one: an edit not applied to the chip yet, or a change made outside SM since the last run." |
| A chip whose ledger knows no runs | a chip with no data folder, then an Apply (a ledger of SM writes only) | the OLD drawer, under "Older snapshot history: this chip's change ledger holds no runs (no data folder is linked to the chip), so changes between snapshots can be missing." Before the Apply: "...this chip has no change ledger yet...". |
| An unproven run | run #3's `f_01` | "saved in #3 scan" / "writer not proven", no Data button |
| The ledger's first run | run #1's `T1` | "first recorded in #1" / "ledger start; writer unknown", no Data button |

## 6. Real browser

SM served from this worktree on **5157** (waitress, the test env) against a **copy** of the agent
rig's chip and data folder in the scratchpad (network `127.0.0.1:1`, `extras.data_folder` pointed at
the copy, sandboxed `HOME` / `USERPROFILE`, no qualibrate config), plus two synthetic runs: #5
moves qA1 `T1` with its own patch, #6 moves qA1 `readout_square.amplitude` with none. Headless
Chrome on CDP **9477** (`--remote-allow-origins=*`, a scratch profile), real mouse and keys through
`tests/browser/journeys/cdp.cjs`; journey `tests/browser/journeys/value_history_drawer.cjs`.

Result: **ALL OK, 22 checks, console errors 0.**

| Step | Seen |
|---|---|
| Sidebar → Live State Edit | the grid (`1_live_edit.png`) |
| clock on qA1 `T1` (a cold column, scrolled in) | ledger drawer: "#5 05_T1 / its own patch set it" with Data; "first recorded in #1 / ledger start; writer unknown" without; foot "from the change ledger (6 events: runs and SM writes)" (`2_t1_drawer.png`) |
| Use | the cell takes the drawer's value, nothing staged; Escape closes (`3_after_use.png`) |
| clock on qA1 RO amp (the cell's path is the alias `readout.amplitude`) | the grid hands the drawer its resolved holder: "amplitude `qubits.qA1.resonator.operations.readout_square.amplitude`", "saved in #6 04_resonator_power / writer not proven" (`4_alias_drawer.png`) |
| Column History of the RO amp column (posts the alias paths) | "via readout → readout_square" on every row; qA1's chips are exactly the drawer's two values (`4b_alias_column.png`) |
| Column History of T1, Changes then By run | chips with who-lines; two rows say "not recorded yet" (the rig chip holds T1 values no run saved -- true of this rig); By run shows the 6 runs (`5_column_changes.png`, `6_column_byrun.png`) |
| Escape, browser Back, reload | the page is whole (`7_after_back.png`, `8_after_reload.png`) |

The first pass of the journey found a real display defect: the "Set by" cell ellipsized at 150 px,
so "saved in #6 ..." lost exactly the part that says the writer is not proven, and Column History
chips clipped their provenance at the card edge. Fixed: the who-line is its own line (drawer and
chips), the drawer's provenance column is wider and wraps, and both pins now assert the visible
qualifier (`<span class="vh-sub">writer not proven</span>`, the chip's "· ledger start; writer
unknown"), not only the hover title.

## 7. Drawer server time, before vs after

`tools/check_hub_drawer.py perf-big` / `perf-synthetic`: the route `GET /field/history` through the
Flask test client (server time, no network), the same paths before and after in one process.
"Before" is the old path exactly (`_value_history` stubbed to answer `fallback` at zero cost, so the
route runs the unchanged old body); "after" is the ledger path. The first pass over the paths is
"cold" (every path read once), then three warm passes. The PC had other work running.

| Chip | | first open | cold p50 / p95 | warm p50 / p95 |
|---|---|---:|---:|---:|
| Big chip copy (30 qubits, 9.5 MB state, 155k ledger paths), 20 runs, Param History backfilled, 50 paths | before | 1,801 | 1,945 / 4,200 | 10.2 / 13.6 |
| | **after** | 2,573 | **1.7 / 3.1** | **1.9 / 3.3** |
| Synthetic 10,000-run ledger (128 qubits x 8 fields), the same runs as a Datasets folder, 50 paths | before | 3,570 | 31.7 / 198.4 | 15.6 / 172.9 |
| | **after** | 318 | **3.3 / 11.5** | **2.7 / 10.1** |

Warm: 2-3 ms, far under the 150 ms target, and faster than before on both chips. Cold per path:
1,000x faster on the big chip (the old drawer parsed each run's 9.5 MB state once per path; the
ledger holds the rows).

**The first open after a ledger change** pays S6a's read-index rebuild (`hub_index`: every commit
-- a new run, an SM write -- changes the token, and there is no incremental path, docs/279). On
the 155k-path chip that was **4,533 ms** with the code as merged; two equivalence-checked speedups
brought it to **2,573 ms**:

* `hub_store.segments`: a path with no backslash is split on dots directly (the character loop was
  2.8 s of the 3.1 s build). Checked: equal to the loop on 200,000 generated spellings, pinned by
  `test_holder_path_decoding_is_the_same_with_and_without_escapes` against an independent decoder.
* `hub_index.build_index`: only a segment that starts with `q`/`Q`/`c`/`C` can name an entity, so
  the rest skip `_entities`. Checked: the whole index (every array, map and posting) built before
  and after on the big ledger is identical; the existing entity pins of `test_hub_query` go RED when
  the prefilter is narrowed.

What remains (~1.2 s build + ~0.7 s RAM accounting in `footprint`) is S6a's design and is left to it
(see §10). The old drawer's per-path cold cost was 1.9-4.2 s on the same chip, so a person opening
a few fields after a run waits less than before; a person opening exactly one field once after
each run waits 2.6 s instead of 1.8 s.

## 8. Pins, mutations, tests

`tests/test_hub_drawer.py`: **24 test functions** (22 on synthetic chips through the real routes, with
the hub projecting inline and the chip's declared data folder synced on `/load`; one driving the
jsdom selfcheck; one pure), plus
`tests/vh_retry_selfcheck.cjs` (11 jsdom checks of the drawer / Column History retry, driven by
`test_the_retry_selfcheck`). No customer data, no existing test edited.

`tools/mutate_hub_drawer.py`: one source edit at a time (exactly one occurrence of its anchor), the
pins it claims run, the source bytes restored in `finally` and read back; a RED counts only on a
failed assertion, never a collection, syntax or incidental runtime error.
**38 / 38 RED on the final code; every one of the 24 test functions is targeted**, plus two
existing `test_hub_query` entity pins for the index prefilter.
Results: [282_value_history_mutations.json](282_value_history_mutations.json).
The first sweep (before the browser round) was 33/33; the browser round added the visible
qualifier mutations, and the agent / approval labels one more. One mutation of the `segments` fast path first came back with the existing
store pin failing on a `KeyError`, not an assertion, so it was not counted: a dedicated pin against
an independent decoder now catches it on its assertion.

**Existing tests**, the test env, `--timeout=900`, one process, run serially. Scope: every test file
that mentions the value drawer, Column History, the agent field-history, Param History, Chip Status
Trends or the leaf series (grep of `field[-_/]history`, `column[-_]history`, `FieldHistory`,
`ColumnHistory`, `param[-_]history`, `topology/trends`, `leaf_field_series`, ...) plus every
`tests/test_hub_*.py` (this branch touches `hub_store` / `hub_index`): **60 files**.

| Run | Result |
|---|---|
| first full run, mid-implementation | 2 failed, 2,283 passed, 28 skipped. Both pass alone: `test_runner_p2::TestTrendAnchor::test_routes_wire_the_provider` reads `routes.py` through `inspect.getsource` and I edited the file while the run was going (the docs/275 artefact); `test_one_run_instant::...test_revert_keeps_a_label_written_after_the_rekey` hit a Windows `PermissionError` on a rename (the flake docs/270 and 271 recorded). |
| **final code, 60 files** | **2,567 passed, 28 skipped, 0 failed** (19 min 49 s) |
| after the last edits (the history-button title, the agent / approval label), the ten closest files again (incl. `test_hub_record`) | 457 passed |

Every existing test passes unedited, including the ones that pin the old drawer and Column History
(they run on chips with no ledger of runs, so they meet the fallback, whose body is unchanged) and
`test_existing_field_with_no_history_is_an_empty_success` (the agent's fallback hands back
`field_history`'s own answer).

## 9. What S10 can delete, and when

The old path stays only as the labelled fallback (§1.3). **When S10 may delete it:** after the
user's full-day verification (step 4), **and** once no chip SM opens reaches the fallback, i.e. every
chip's runs reach its ledger. Today the fallback answers two kinds of chip: one opened before its
ledger exists (gone after the first catch-up) and one with **no data folder linked** (its ledger
holds SM writes only, while Param History still ingests its runs from every workspace folder by
chip identity). The second needs a decision first: either the ledger adopts runs of workspace
folders by the same identity ladder, or the drawer for such a chip shows the SM writes plus "link
a data folder". Until one of those ships, deleting the fallback would take runs out of those
chips' drawers.

Deletable then (no other caller, checked by grep on this branch):

| Code | Note |
|---|---|
| `routes._legacy_field_history`, `routes._legacy_column_history` | the old bodies, unchanged |
| `routes._runs_field_series`, `routes._runs_column_series`, `CH_SERIES_RUNS`, `CH_SERIES_EXAMINE` | the 60-run workspace scans |
| `_RUN_IDENT_CACHE`, `_RUN_CHIP_CACHE`, `_RUN_VALUE_CACHE`, `_trim_run_caches`, `_RUN_CANDIDATES_MEMO`, `_store_run_candidates`, `_runs_candidates` | only the two scans read them |
| `HistoryManager.field_history`, `_scan_field_series`, `_scan_one_snapshot`, `_SCAN_SERIES` | the drawer's three tiers (their pins in `test_field_history.py` and the old-path parts of `test_column_history.py` go with them) |
| `templates/_field_history.html`, `templates/_column_history.html`, the `vh-fallback` label, `_VH_FALLBACK_NOTES` | the old panels |
| the `fallback` branch of `_value_history`, of the two routes and of the agent route | |

**Not deletable in S10 by this step alone:**

* `HistoryManager.column_history` (and the `_tracked_property_for` / `_TRACKED_QUBIT_SUFFIX_TO_PROP`
  index fast path it shares) -- Auto Calibrate's drift gate G5 (`_autofit_start_real` →
  `history_points`) reads it too; it moves with that gate (or S8).
* `_WriterCheck` / `value_writer` -- Chip Status Trends and the metric meta still use them (S8).
* `leaf_index` and the curated `param_history` table -- Param History and Chip Status (S8).

## 10. Residual risks and hand-over

1. **First open after a ledger change on a very large chip** (§7): 2.6 s on a 155k-path ledger,
   paid by whichever surface reads first after each commit (S6b's Calibration log too). The
   remedy belongs to S6a: an incremental index, a cheaper RAM accounting than the recursive
   `footprint`, or a rebuild started on the projector thread right after each commit.
2. **The grid hands the drawer the resolved holder** (`data-resolved`), so a drawer opened from a
   Live State Edit cell never shows a `via` line, while Column History, Chip Status Trends and the
   agent take the alias as given and name the hop. Both read the same holder rows, so the answers
   agree (§6); only the hop is named on one side.
3. **Rows-only reading across a drifted SM write** (docs/275 residual 1): an outside change between
   a run and an SM write appears in no row. The drawer shows recorded rows only; the "value now is
   not the newest recorded one" note fires when the chip disagrees with the newest row, which is
   the visible symptom.
4. **Chip Status Trends** reads the ledger only for a typed path that crosses a pointer and whose
   leaf-index read is empty; its other charts, its snapshot provenance map and `_WriterCheck` are
   S8. A point it draws from the ledger is keyed by the run's snapshot key, so where Param History
   has that run's snapshot, Trends' existing provenance rule names it (S8 replaces that rule).
5. **Long-array elements** read their values from the array blob, where S2 stores integral floats
   as integers: an element shows `7`, not `7.0`. Equal under the one equality; a `Use` types `7`.
6. **By run** values are folded from the rows, exact for run events by ledger invariant I2. A
   column whose rows are limited would fold wrongly; Column History never limits them.
