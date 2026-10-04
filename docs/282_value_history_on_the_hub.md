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
says. First commit: 24 pins, 38/38 mutations RED, 60 existing test files green, a real-browser journey ALL OK
with 0 console errors. Warm drawer 2-3 ms (was 10-16 ms); the first open after a ledger change on a
very large chip is 2.6 s (§7, §10).

**Review round (§11).** An adversarial review of the first commit found three places where a wrong
value was shown as fact (Trends and By run drew the alias's CURRENT holder for runs before a
retarget; By run offered a run of another chip identity), two P1s (`before_via` decided by the
newest hop row; Param History's non-run snapshots missing) and six P2/P3s. All fixed on top, each
reproduced RED first and pinned: one rule now answers "which holder did this path name at this
position" for Trends, By run and the row marks; SM's own non-run snapshots enter the ledger as
`observed` events; the read index is extended in place after an append and rebuilt off the request
thread, so the first open after a run on the 155k-path chip is **18-34 ms (was 3.2 s)** and a read
of another chip never waits on a busy one. Golden again with 20 outside edits per archive: every
old point contained (drawer 363/363 and 299/299, Column History 236/236 and 240/240, missing 0),
the in-force value right in 109,259 run x alias checks (the first commit's rule: 6 wrong). 42 pins,
62/62 mutations RED, 2,585 related tests passed (one pre-existing S5 race, §11), the browser
journey ALL OK with 0 console errors.

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
hop's pointer holder (`set` / `add` / `gone` / `retarget`).

**Which holder the path named at a time (review round, §11).** One function,
`value_history.holder_at(rows, path, position)`, walks the path the way the resolver walks today's
document, but reads every node on the way from the ledger AT that position (a node holding a
pointer string then is followed; a leaf pointer is followed while its target is a value holder).
`alias_segments` turns it into `[(start position, holder)]` over the whole ledger (breakpoints: the
rows of every holder the walk consulted, to a fixed point). Three things read those segments and
nothing else:

* the drawer's rows are the **holder's own** rows (not stitched); a row is marked `before_via`
  exactly when the segment at its position names another holder ("before readout pointed here");
* the **in-force series** (`effective`): at each change, the value of the holder the path named
  then, plus one `via` row where a retarget changed the value without a row of its own. Chip
  Status Trends and the agent's `in_force` draw it;
* **By run**: each run's value is the fold of the holder the path named at that run.

The first commit marked rows by the newest hop row and drew Trends / By run from today's holder, so
a value of a holder the alias did not name yet was shown as the alias's value (P0-1, P0-3, P1-1).

**Long arrays.** A path that names an element of a scalar list longer than 16 (one S2 holder)
is read from the array holder's rows, each array version loaded from its blob and the element
taken; an array version that leaves the element unchanged is not a change of the element. Rows of
the per-element holder (while the list was 16 or shorter) are merged in the same pass, per event.
Which of the two a time reads is decided **per event from that row's own shape** (an array row
holding a blob marker, or the element's own row), never from today's list length: a list that
was long and then shrank keeps its long-era history (review P2-4).

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
| SM event later undone | the same, struck through, "(undone)"; for a write whose undo took back only some of its paths (`PARTLY_UNDONE`), only on a path where a later undo still in effect names it (review P2-2) | as above |
| `observed` event (review P1-2): a Param History snapshot that is not a run (`auto` / `manual` / `save` / `backup` / `restore`) | "seen by SM (`<trigger>` snapshot)" / "writer unknown" | no |

Flags shown on the row: `SOURCE_GONE` ("run folder deleted"), `REWRITTEN` ("run folder rewritten
after the run"), `OVERLAPS_SM_WRITE` ("this run's save went over an SM write"),
`REVERTS_TO_EARLIER` ("returned to an earlier saved state"), `NODE_UNREADABLE`, `TIME_ASSUMED`.

`_WriterCheck` / `value_writer` are not used on the ledger path: the ledger's `proven` is the proof.

### 1.5 Column History on the ledger

* **Changes** tab: each row's own change points from the same `value_history.read`, newest first
  (6 shown, the count of the rest stated).
* **By run** tab: the newest 6 successful run events of **this chip** in the ledger, each row's
  value in force at that run: the fold of the holder the path named at that run (§1.2), i.e. the
  value of that holder's newest row at or before the run. A run event's value is its own saved
  value (ledger invariant I2), so the fold is exact for runs. A run of an uncertain chip identity
  (`CHIP_UNCERTAIN`) is **left out** (no column, no Use all) and the footer counts it ("1 run of an
  uncertain chip identity is left out"); the first commit showed it with a Use all (P0-2). A cell
  is highlighted when it differs from the previous run's under the one equality (`hub_rules.same`,
  so NaN then NaN is no change; P3).

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
| `core/hub_store.py`, `core/hub_index.py` | Two equivalence-checked speedups of the S6a index build (§7). Review round: `extend_index` (append-only commits extend the cached index in place), a zone-free index with per-zone views, the try-lock read (`READ_WAIT_S`), stale readers closed outside the global lock, `prewarm`. |
| `core/hub.py`, `web/app.py` (review round) | The projector builds a touched chip's read index right after each ingest burst, on its own thread (`set_prewarm`, on outside tests). |
| `core/hub_sync.py` (review round) | `observed` events: `attach_observed`, `drop_observed_runs`, the observe step of a sync slice (after the runs settle), fed by `routes._hub_observed_source` (this folder's own non-run Param History snapshots). |
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
  recorded yet". (The first commit also marked every row `before_via`; the review round corrected
  that pin: a row is marked only where the path named another holder at that row's position, so
  a value the path DID read through the old pointer string is not marked -- §1.2, §11.)
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

| | Archive A (~1 MB states) | Archive B (~0.3 MB states) |
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
| The same pointer edited, not applied | `/field/edit` of `x180` to `#./x180_Gauss` | "via `x180` → `x180_Gauss` · this pointer is not recorded yet (an edit not applied to the chip): every row below is from before it"; a row is marked only where `x180` named another holder then (review P1-1: `x180_Gauss`'s value from #1, read through `x180` at #1-#3, is not marked) |
| A retarget and back (A → B → A) | runs retarget the alias away and back | rows of A from the B era alone are marked; Trends / the agent's `in_force` draw A, B, A in turn; By run shows B's value at the B-era run (review P0-1, P0-3) |
| A run of another chip identity | a run whose saved chip name differs (`CHIP_UNCERTAIN`) | the drawer row "#N (chip uncertain)" / "not named as writer"; By run leaves it out and says so (review P0-2) |
| A write partly undone | one Apply of two paths, then one undo taking back one | the path taken back: "undo by operator", then "applied by operator (undone)" struck through; the other path: "applied by operator", not struck (review P2-2) |
| A state SM saw between runs (an outside edit) | an `auto` Param History capture after a file edit | "seen by SM (auto snapshot)" / "writer unknown" at the capture's time (review P1-2) |
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

**Review round journey** (same rig, fresh copy, plus three runs: #7 retargets qA1 `readout` to
`readout_GEF` while `readout_square.amplitude` moves, #8 retargets it back with a new amplitude, #9
is a run of another chip identity that moves qA1 `T1`). **ALL OK, 37 checks, console errors 0.**

| Step | Seen |
|---|---|
| clock on qA1 RO amp **from the grid cell** | the drawer opens on the alias path itself (P2-1), "via readout → readout_square · pointed here since 10-03 18:30 (saved in #8, was `#./readout_GEF`); retargeted 2 times"; of four rows only #7's carries "before readout pointed here", in text, wrapped inside its cell (`4_alias_drawer.png`) |
| Column History of RO amp, Changes | the same four values as the drawer; the #7 chip says "· before readout pointed here" (`4b_alias_column.png`) |
| By run | columns #8 #7 #6 #5 #4 #3 -- no #9; footer "1 run of an uncertain chip identity is left out"; qA1 at #7 shows `readout_GEF`'s 0.010625..., the value in force then, not `readout_square`'s 0.013144 (`4c_alias_byrun.png`) |
| T1 By run | no #9 column, one Use all per column shown (`6_column_byrun.png`) |
| two T1 edits (qA1, qA2) as `operator`, one Apply, one undo (in-page `fetch`, the real routes), reload | T1 Column History: qA1 "applied by operator", not struck; qA2 "undo by operator" then "applied by operator (undone)" struck (`6b_partly_undone_column.png`); qA2's drawer says the same (`6c_partly_undone_drawer.png`) |
| Escape, Back, reload | the page is whole; console errors 0 |

The first pass of this journey showed the drawer's "before readout pointed here" flag running over
the When column (a `nowrap` flag in a narrow cell); it now wraps inside the value cell and takes
the warning colour.

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

**Review round (P2-3): the first open after a run, and a read of another chip meanwhile.**
`tools/check_hub_drawer.py perf-after-run`: the big chip's state (9.5 MB, 155,528 ledger paths)
with 8 runs, SM in production mode (the projector thread ingests), a new run written, and the
drawer for a `T1` opened as soon as the run is in the ledger; a small second chip's ledger read in
a loop meanwhile. Three changes:

* `hub_index.extend_index`: a commit that only APPENDED events (a run at the head, an SM write)
  extends the cached index in place (new events, paths, postings) instead of rebuilding it; any
  other commit (an event moved, re-diffed, re-flagged or removed; another ledger file) is a full
  build. Checked: the extended index equals a full build field for field (pinned).
* the projector builds the index right after each ingest burst, on its own thread
  (`hub.set_prewarm`, on outside tests), not on the first reader's request;
* `hub_index.snapshot` never holds the global readers lock while waiting for a chip's read lock:
  it tries that lock for 0.25 s and otherwise answers `preparing` (the drawer asks again); stale
  readers are closed outside the global lock. Zones share one zone-free index (a zone is a view).

| | first open after a run (big chip) | a read of another chip during it |
|---|---:|---:|
| first commit (S7 as merged; no prewarm, full rebuild in the request) | **3,169-3,248 ms** | blocked behind the busy chip: the pin measured **4.96 s** with the old lock order |
| full rebuild moved to the projector thread (intermediate) | 257-460 ms answering "preparing", rows after 3.6-5.2 s | 133-326 ms |
| **final** (append extends the index; prewarm; try-lock) | **18.5-34.5 ms, rows on the first request** | **1.1-7.8 ms** |
| the next open, index settled | 3.8-5.1 ms | |

Target (< 300 ms for the first open after a run, no cross-chip blocking) met; the PC had other
work running.

## 8. Pins, mutations, tests

*First commit; the review round's pins, mutations and test runs are in §11.*

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

1. **First open after a ledger change** -- resolved in the review round for appends (§7: 18-34 ms
   on the 155k-path chip). A commit that is not an append (a late run landing in the middle, a
   rewritten folder, a removed event) is still a full build (~1.2 s on that chip), now on the
   projector thread; a reader meanwhile gets "preparing" after 0.25 s and asks again.
2. **The grid hands the drawer the cell's own path** since the review round (P2-1), so the drawer
   opened from an alias cell names the hop like every other surface.
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
7. **A run of an uncertain chip identity stays in the ledger's chain** (S5's model): the drawer
   lists its change, labelled "#N (chip uncertain)" / "not named as writer", and the next row's
   delta is measured against it (in the browser rig, an operator write of qA1 `T1` shows -50 %
   against the foreign run's value, while the live chip held another value). By run leaves such a
   run out (P0-2). Taking it out of the chain is a ledger decision (S5), not a drawer one.
8. **`observed` events** come only from this chip folder's own Param History lineage (never a
   parallel folder's, docs/250) and only for a chip whose ledger sync runs (a data folder is
   linked). A capture of a run's save that SM stamped BEFORE the run (clock skew) is given back to
   the run only when the two states are equal; one that differs in an incidental field as well
   keeps the change, labelled "seen by SM ... / writer unknown" -- never a run named wrongly.

9. **Two windows opening a ledger that does not exist yet can race** (`HubStore.__init__`, S5):
   one of them fails with `database is locked` (the WAL switch) or `duplicate column name` (the
   schema `ALTER`). Measured on `origin/main` as well (§11 Tests); the sync's caller survives it and
   the next slice retries, but the open itself should take the schema step under one lock.

## 11. Review round

An adversarial review of `3a9a774f` executed a repro for every finding. Each was reproduced RED on
this branch first, fixed on top (no rewrite), pinned, and every new or changed pin mutation-checked
(`tools/mutate_hub_drawer.py`).

| Finding | Fix | Pin(s) | Measured |
|---|---|---|---|
| **P0-1** Trends drew the alias's current holder for runs before a retarget, and extended a holder's held tail from another holder | `holder_at` / `alias_segments` (§1.2): Trends draws `effective`, the value in force through the path at each change, segment by segment; a holder's rows are never used outside its segment | `test_p0_1_trends_draws_the_value_in_force_through_the_alias`, `test_p0_1_after_a_return_trends_draws_each_holders_value_in_force`; the first commit's own Trends pin (it pinned the bug, `[0.1, 0.2, 0.25]`) corrected to the truth `[0.3, 0.25]` | golden alias truth (below): 0 mismatches against the value each run's own saved state holds through the alias |
| **P0-2** By run showed a `CHIP_UNCERTAIN` run with Use all | excluded (chosen over "marked": a run of another chip is never this chip's saved value); the footer counts it | `test_p0_2_by_run_leaves_out_a_run_of_another_chip` | browser: no #9 column, "1 run of an uncertain chip identity is left out" |
| **P0-3** By run showed the current holder's value for an alias row before a retarget | each run's value is the fold of the holder the path named at that run's position | `test_p0_3_by_run_shows_the_then_holders_value_for_an_alias_row`, `test_p0_3_and_p1_1_share_one_rule_after_a_return` | browser: qA1 at #7 = `readout_GEF`'s value |
| **P1-1** `before_via` decided by the newest hop row (mostly false) | a row is marked exactly when the segment at its position names another holder -- the same function as P0-1 / P0-3 | `test_p1_1_before_via_marks_only_rows_the_alias_did_not_name`, the shared-rule pin above; the unapplied-pointer pin corrected (it pinned "every row marked") | browser: 1 of 4 rows marked, the right one |
| **P1-2** Param History's non-run snapshots (outside edits SM saw) were not in the history | ingested by the S5 sync as `observed` events (instant = the capture stamp, trigger and actor kept, writer unknown); a snapshot equal to the state before or after it is recorded as looked at and left out; an observation followed by a run that saved exactly the same state is given back to the run | `test_p1_2_a_state_sm_saw_between_runs_is_in_the_history`, `test_p1_2_an_sm_writes_own_snapshots_are_not_a_second_history`, `test_p1_2_a_runs_save_seen_early_stays_the_runs`, `test_p1_2_a_run_landing_after_its_own_early_observation_takes_it_back` | golden with 20 outside edits per archive (below) |
| **P2-1** the grid's clock passed `data-resolved`, losing the alias | the cell's `data-dot-path` is sent (resolved only as a fallback) | `vh_retry_selfcheck.cjs` check 13 | browser: the drawer from the grid cell names the hop |
| **P2-2** "partly undone" struck through a value still live | undone is decided per path: `UNDONE` on every path; `PARTLY_UNDONE` only on a path where a later undo still in effect names the write | `test_p2_2_partly_undone_marks_only_the_path_taken_back` | browser: qA1 not struck, qA2 struck |
| **P2-3** first open after a run ~2.4 s, and it blocked other chips' readers (the global lock held while waiting on a chip's lock) | append-only commits extend the index in place; the projector rebuilds right after a burst; try-lock with "preparing", never the global lock while waiting; zones share one index | `test_p2_3_a_busy_chip_never_holds_a_read_of_another_chip`, `test_p2_3_the_projector_builds_the_index_after_a_burst`, `test_p2_3_every_zone_shares_one_index`, `test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build` | §7: 3.2 s → 18-34 ms; cross-chip 4.96 s → 1.1-7.8 ms |
| **P2-4** a long array that shrank lost its element history | per event from the row's own shape (§1.2 Long arrays) | `test_p2_4_an_array_that_shrank_keeps_its_element_history` | |
| **P3** NaN highlighted as a By-run change | `hub_rules.same` decides the highlight | `test_p3_nan_in_by_run_is_not_a_change` | |
| **P3** `before_via` in Column History only by opacity; the current chip dimmed | a visible text marker on the chip and the row; the current row never dimmed | `test_p3_column_history_says_before_via_in_text` | browser screenshots |
| **P3** docs quoted archives' UTC offsets | dropped | -- | |

**Golden again (check a), with P1-2's snapshots.** The same harness and seed on fresh byte copies of
the same two archives, plus **20 outside edits per archive**: the live chip's file written between
two runs with the earlier run's saved state and 1-3 of its numeric qubit values moved by 1 %, each
followed by a Param History `auto` capture stamped midway between the two runs, and the old side's leaf index
rebuilt so the OLD drawer shows those captures too. The sample now draws its paths from the edited
ones first (`outside_edit`). All 40 captures were imported as `observed` events (inserted 20 + 20,
dropped 0).

| | Archive A | Archive B |
|---|---:|---:|
| ledger events (runs + observed) / Param History snapshots | 320 (300 + 20) / 237 | 311 (291 + 20) / 195 |
| paths (outside-edit / alias / pointer leaf / long-array element / leaf) | 200 (33 / 40 / 20 / 20 / 87) | 200 (33 / 40 / 20 / 0 / 107) |
| **old drawer points contained** | **363 / 363** (277 same event, 86 dated earlier) | **299 / 299** (219 / 78, plus 1 pointer string in `via`, 1 absent on both) |
| **old Column History chips contained** | **236 / 236** (39 / 197) | **240 / 240** (50 / 188, 1 / 1) |
| -- missing | **0 / 0** | **0 / 0** |
| dated earlier: the old window's start / the changing run has no Param History snapshot | drawer 67 / 19, column 196 / 1 | drawer 66 / 12, column 186 / 2 |
| extra ledger points, drawer | 39 returns to an earlier state, 23 the change right after such a return, 1 an outside edit right after such a return | 1 older than the old window |
| extra ledger points, Column History | 184 older than its window, 3 outside edits it did not show, 2 the run right after such an edit, 1 return | 52 / 4 / 4 / 0 |
| extra points not classified | **0** | **0** |
| **alias truth**: each of the newest 60 runs, each alias path, the value the drawer's in-force series gives vs the value that run's own saved state holds through the alias | 80,099 checks, **0 wrong** (the first commit's rule, today's holder for every run: 6 wrong) | 29,160 checks, **0 wrong** |
| alias agreement (b), the most-changed alias | drawer = Column History (20 rows, 2 marked `before_via`); Trends = in-force (23 points, 5 of them from the other holder); every unmarked row is on Trends | drawer = Column History = Trends (3) |

"The change right after a return" is the class the first round left as "inside the old window"
(24 points): the run that returns to a byte-equal earlier state has no Param History snapshot (its
global dedup drops it), so the old surface saw neither the return nor the next change. Counts in
[282_value_history_checks.json](282_value_history_checks.json) (`review_round`).

**Mutations.** `tools/mutate_hub_drawer.py` now holds **62 mutations, 62 / 62 RED**, targeting **all 42
test functions** of `tests/test_hub_drawer.py` plus two `test_hub_query` entity pins. The 24 added
since the first commit's 38 break exactly the review's fixes: the shared position rule (`holder_at` replaced by today's
holder, `before_via` from the newest hop row), By run's chip filter and fold, the per-path undone
rule, the per-event element shape, the observed import and its two "already explained" rules, the
try-lock, the prewarm, the zone-free token, the guards of `extend_index`, the grid's
`data-dot-path`, NaN equality, the visible `before_via` text. The full sweep first came back 59 / 61:
two `extend_index` / zone mutations were GREEN because a later guard (the new-event id check) and
the extend path itself masked them; their pins now assert what the guard alone protects (an event
re-flagged in place, no append: the index equals a full build; three zones prepare the index
once). One pin had no mutation reaching it (an Apply's own Param History copies): it now asserts at
the ledger level that the save copy adds no event, and the mutation that drops the "same as the
state before" rule turns it RED. Every mutation that targets a changed pin was re-run (13);
results: [282_value_history_mutations.json](282_value_history_mutations.json).

**Tests.** The test env, one process, serially, on the final code: every `tests/test_hub_*.py` plus
the same related files as the first commit (61 files including `test_hub_drawer.py`): **2,585
passed, 28 skipped, 1 failed** (19 min 17 s). The one failure,
`test_hub_sync.py::TestConcurrency::test_two_windows_on_one_chip_ingest_each_run_once`, is a
pre-existing race in `HubStore.__init__` (S5), not this round's: two processes opening a ledger
that does not exist yet. Alone it fails 1 of 8 runs on this branch, and on `origin/main`
(`1ef8983c`, a throwaway worktree) 1 of 15 and again 1 of 3, with either message --
`database is locked` at `PRAGMA journal_mode=WAL` or `duplicate column name` at the schema
`ALTER` (both windows add the same column). Left for S5's owner (§10). The jsdom selfcheck (13
checks) runs inside `test_hub_drawer.py`; the real-browser journey is §6.
