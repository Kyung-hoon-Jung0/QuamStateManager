# docs/141 — The night session: apply cost, the Config Manual, search speed, the undo trail (2026-08-27/28)

User-directed, autonomous overnight campaign. Rules for the night: accuracy first,
tokens frugal, time unconstrained, a heavy adversarial review after each commit.
Commits: `264a4e3` `03b89fa` (apply), `d08eaac` (manual), `4ffee11` (speed + undo +
review fixes). Every number below was measured, and the ones that were wrong the
first time are corrected in place.

## 1. Apply-to-live: what it cost, honestly

Profiled on a COPY of the PJ 20Q chip. The first report (0.82 s) was taken in
`testing=True` mode, where the route's `defer_index` is off — production already
indexes on a thread, so the real steady-state was ~0.55 s. Two changes:

* **Copy bytes, do not re-dump.** One apply re-serialised the same content six
  times (saver ×2, live write ×2, two history snapshots ×2). `safe_io` gained
  `read_json_raw` / `read_state_wiring_raw` (same share-delete handle, retry ladder
  and double-checked-mtime pair read, also returning the exact bytes parsed) and
  `write_state_wiring_bytes` (same tmp+fsync+replace, same state rollback).
  `check_and_snapshot` and `apply_to_live` use them: a snapshot is a byte copy of
  its source, the live files are byte copies of the working files. `read_json` and
  `read_state_wiring` now delegate to the raw variants (one retry ladder).
  Production-mode, 2 runs each: steady-state apply 0.58/0.52 → 0.27/0.43 s.
* **Heal readers join the deferred index.** A Param History read ~100 ms after an
  apply found the index "behind" while the deferred insert was still running and
  started a FULL rebuild (seconds). `_join_deferred_index` (bounded, never raises)
  runs first in both self-heal readers.

Kept on purpose: both snapshots (pre-apply arms Revert; post-apply is the record).

## 2. The Config Manual (`core/key_manual.py`, `core/key_manual_docs.py`, `manual.js`)

Customers populating a state did not know which keys a node can carry, what
values they take, or what they mean. Two sources, labelled, never blended:

1. **The env's own classes.** `probe_state_schema` parses each class's Google-style
   `Args:` / `Attributes:` docstring sections (MRO-walked, subclass may override,
   an auto-generated dataclass signature is not documentation) into a per-field
   `doc` + class `doc`. The schema cache is gated on `SCHEMA_FORMAT` — on the
   probe AND on the request path (`cached_only`), a review finding: a warm
   pre-upgrade cache would otherwise have hidden every docstring forever.
2. **The official QM docs** for the port/config keys the classes do not describe
   (`band`, `upconverter(s)`, `full_scale_power_dbm`, `downconverter_frequency`,
   `lo_mode`, `sampling_rate`, `upsampling_mode`, `output_mode`, `offset`, `delay`,
   `filter`, `crosstalk`, `gain_db`, `intermediate_frequency`, `time_of_flight`,
   `smearing`, `sticky`, `core`), each naming the page + anchor it came from and a
   verbatim line. The review removed three entries the pages never state (`thread`,
   digital `delay`/`buffer`) and moved three defaults to the page that states them
   (`config.md#controllers`). A test asserts every anchor is a real heading.

`manual_entries()` = one row per (class, field) for every class the chip uses —
type, required, default, Literal choices, description, SOURCE, where used — plus
the docs-only keys; a key nobody describes says "no description". `node_keys()` =
"what can THIS place carry": set / unset / undeclared, a leaf focusing its parent.
Wiring paths are walked verbatim (`wiring.*`, `network.*`).

Surface (user-directed): sidebar **Config Manual** directly below Settings /
Calculator → a body-level movable window (the calculator's anchor + header-drag
pattern) with the house search box; F1 on any state value, a `?` on every LIVE
state tree row (crud renders only), on Live-Edit column headers and inspector rows
opens the "this place" view. Buttons carry data attributes (no inline onclick
strings — a key with a quote would end the script); descriptions are escaped
(third-party docstrings are text). Measured on the real PJ chip with the cqt env:
262 keys — 181 with the class's own words, 19 from the QM docs, 62 stated as
undescribed.

## 3. Search speed — measured in real Chrome, then changed

Headless Chrome 151 + DevTools trace (`cdp_measure.js`), 20Q chip, one keystroke:

| surface | query | main-thread block before | after |
|---|---|---|---|
| Live Edit | `amplitude` | 925 ms (style recalc 371, layout 125) | **186 ms** (51, 6) |
| Live Edit | `T1` | 780 ms | **267 ms** |
| Json Tree | `amplitude` (516 matches) | 429 ms | **no long task** (199 ms total) |
| dataset sidebar | `rabi` | none | none |

* **Grid**: not the safety gates (they run at commit) and not the string matching
  (~110 ms JS) — the search hid excluded columns with ONE attribute-equals rule
  per column, and Chrome indexes rules by attribute NAME, so all ~8,000 tds tested
  all ~300 rules. The template stamps `ck-<index>` on every th/td and the search
  hides by class selector (hashed by class name).
* **Tree**: a broad query materialised every matching subtree. Past 150 matches
  only the first 150, in tree order, are materialised and highlighted, under a
  notice with the true count and a "show all" (the full, slower build on the
  user's press); below the cap the classic highlight stays. (The night's first
  version LISTED the matches instead of drawing the tree; the user read that as
  "the tree vanished" and it was revised the same day — the tree stays a tree.)

Residual as of the night: the grid keystroke was 190–270 ms. §4d (daytime)
measured where that went and removed most of it WITHOUT touching the DOM.

## 4. Undo — the field you just changed, fast (user: "very important")

* A Ctrl+Z from the inspector or the tree used to cost the 2.4 s whole-grid re-GET:
  a path with NO cell on the grid counted as "uncovered". `revertPaths` (both
  grids) now reports the cells it FOUND but could not repaint honestly as
  `uncovered`; only those, or a structural change, rebuild.
* **The Undo trail** (`undo-trail.js`): a compact bottom-right panel answers every
  Ctrl+Z / Ctrl+Shift+Z with path, from → to, tier (typed / staged), last 8 steps,
  closable, cleared on a state restore — fed by the `/undo`,`/redo` responses and
  by LiveEditUndo (`quam:undo-step`). Its one button, **go to field**, is the only
  thing that navigates: flash + focus when visible, else the owning surface via
  UndoNav — on the press, not automatically.

## 4b. The review of 4ffee11 (2 major, 2 minor — all fixed, all pinned)

* **Tree list vs the tab switch (major).** The state and wiring trees share one
  parent and one search box, and `switchExplorerTab` re-runs the query on the
  tree it shows. The result list was a SIBLING found through the parent, so the
  wiring search removed the state list, and the dedup guard then left the state
  tree with every node hidden and nothing listed. The list is now the
  container's first child (scoped by construction, follows the tab's display,
  dropped by any re-render), and a row click resets the dedup so the same query
  lists again. Pinned by the two-tree round trip in `tree_search_list_selfcheck.cjs`.
* **Stale list / readonly cells after undo (major).** The new `uncovered` rule
  had been gated on `wrote > 0`, so a found-but-unwritable cell (pair `list` /
  `runtime` inputs, the qubit grid's ✎ list-preview span) kept its edited
  preview and red marker after Ctrl+Z — a regression of docs/124 M-10. Rule now:
  found-but-not-honestly-repainted ⇒ uncovered ⇒ rebuild; NO cell ⇒ missing ⇒
  no rebuild (the 2.4 s saving stays for off-grid undos). Pinned by four cases
  in `undo_repaint_selfcheck.cjs` (now driven by `test_undo_trail.py`).
* Minor: the trail panel sat exactly under the toast sink (moved to
  `bottom: 5rem`); `_rebuildNode` now derives the `?` flag from its own tree;
  the `?` click handler no longer stops propagation (a click-away listener
  elsewhere must see the click) — the grid header sort ignores the button itself.

## 4c. Ctrl+Z on the Json tree and on Pulses, checked in real Chrome (2026-08-28, daytime)

The user asked whether undo is now fast AND stable on every surface. It was
not; a real-Chrome check (`cdp_undo.js`: one real edit through the page's own
UI, trusted Ctrl+Z / Ctrl+Shift+Z key events) found three defects, all fixed
and pinned in `undo_pages_selfcheck.cjs` (mutation-checked 8/8):

* **The tree's data model did not follow a value change.** An inline edit, an
  undo and a redo repainted the DOM only; `container._treeData` — what a
  collapsed branch is rebuilt from and what search matches against — kept the
  value from BEFORE the edit. `_treeModelSet` now writes the leaf (and drops the
  flat index) from the edit commit, `_rebuildNode` and `_revertTreeNode`.
* **An inline (Pulses) field reverted by undo kept its edited `data-committed`.**
  The next click-away saw value ≠ baseline and RE-COMMITTED the reverted value
  as a new edit — which cleared the redo stack, so Ctrl+Shift+Z on Pulses did
  nothing. `_revertCell` now moves the baseline with the value and fires
  `input` (the waveform preview follows).
* **Undo auto-navigated.** `cellsReverted` still called `UndoNav.handle`, so a
  redo of a field not on screen replaced the Pulses inspector with a qubit
  inspector. Now `UndoNav.flashVisible` flashes what is visible and nothing
  else moves; the trail's **go to field** is the one navigator — which is what
  §4 claimed and was not true until this fix.

Measured after the fix (PJ 20Q chip): tree edit 66 ms, undo 51, redo 71, model
in step; Pulses commit 139 ms, undo 47, redo 79.

## 4d. The grid keystroke, profiled to the phase and fixed without DOM surgery (2026-08-28)

The user asked whether DOM-shrinking (detaching hidden columns' tds) was worth
its stability cost. Measured first (CPU profile + phase trace, real Chrome, one
`amplitude` keystroke, 7,875 tds): JS was **34 ms**; the block was the browser —
style recalc **149 ms** over `elementsStyled: 10,673` (the WHOLE grid), layout
30–50 ms, plus 311 ms of accessibility-tree work that only headless/CDP turns
on (chrome://accessibility tells a user whether theirs is on) and an 80 ms
Chrome "AI page content" agent that is not ours. So the night's "~130 ms of JS"
was wrong, and the whole-grid restyle had ONE cause: each keystroke replaced the
hide-stylesheet's text, and Chrome invalidates every element matched by any rule
of the old and the new sheet.

Three changes, none of which detaches anything (the inventory that argued
against DOM-shrinking: 13 path-addressed selector sites across bulk-edit /
pair-edit / app.js, 58 table queries, 0 index-walkers — every one would have to
learn a detached store):

1. **Static rules, delta classes.** One sheet, written once per column set:
   `#bulk-table.sh-N td.ck-N { display:none }`. A keystroke toggles only the
   `sh-N` classes on the table whose state CHANGED; Chrome's class-diff
   invalidation reaches only those columns' cells. Cost ∝ change, not grid.
2. **Hydrate what the narrowed grid puts on screen**, not every surviving cold
   column: the first letter of any query survives most columns, so docs/120
   #28's "hydrate every survivor" undid the whole virtualization on the first
   keystroke (632 ms for `a`). The scroll pass runs instead; `T1` still narrows
   the grid to that column, which is then at the left edge and hydrated.
3. **A hidden column is cold.** `_virtInit` decided coldness by `offsetLeft`, and
   a column hidden by the column checkboxes or by the REMEMBERED search (both
   applied before it runs) has none — so a /bulk opened with a remembered query
   built all 7,761 inputs with virtualization silently off (8/8 loads measured)
   and paid the full price on every keystroke after the search was cleared.

Measured, typing `amplitude` one letter every 250 ms (every letter runs), PJ 20Q:

| load | before: blocked total / worst key | after |
|---|---|---|
| clean | 1052 / 604 ms | **279 / 109 ms** |
| clean, `T1` | 1015 / 772 | **309 / 247** |
| remembered search | 1051 / 892 (virtualization never engaged) | **468 / 256** |

The debounce question (the user's): 120 vs 200 ms at a 150 ms typing gap gave
392 vs 386 ms blocked — 8 runs vs 3, same total, because each run now costs its
delta. The user chose **200** for both grids (fewer runs while typing; the
pause-to-paint latency grows by 80 ms). Two honest notes: the mount itself still takes 1.3–2.0 s
after the table appears before virtualization engages (a keystroke inside that
window hits the un-virtualized grid — a load-time item, not touched here); and
the pair grid still toggles per-td classes (17 ms, left alone).

Pinned by `bulk_search_selfcheck.cjs` (static sheet + sh-class delta, four
cases) and `bulk_virt_selfcheck.cjs` (remembered search ⇒ cold; clearing it
hydrates the viewport).

## 4e. Undo under a burst, and the waveform that follows it (2026-08-28, afternoon)

The user's question: what happens on Pulses when someone hammers Ctrl+Z /
Ctrl+Shift+Z, and could the last 10–20 edits live in RAM "like a clipboard"
so a press applies at once? Findings first (real Chrome, PJ 20Q, trusted key
events through the page's own UI), then what shipped.

**What was actually broken.**
* **Edit → Enter → Ctrl+Z did nothing on Pulses / the inspector.** Enter
  commits and InlineCommit puts the focus back in the field, and the global
  handler ignored any press whose focus was in an INPUT: no request, no trail,
  no native undo either (the re-render had reset the field's own undo stack).
  The same rule as a grid cell now applies: a DIRTY field undoes the typing
  (back to the committed value), a CLEAN one falls through to the server tier.
  This is also why two of the burst runs "lost" presses — a late focus-restore
  pass had put the focus back in the field mid-burst.
* **A held key auto-repeats (~30/s)**: the queue filled in under a second and
  kept undoing after the key was released. Auto-repeat is ignored now — one
  press, one step.
* **A burst of k presses was k round trips.** Presses arriving while one
  request is in flight are coalesced into `?n=k`; `/undo` and `/redo` pop k
  actions inside one lock and answer with every reverted path (a `jrn:`
  cross-save step ends a burst — it is its own press, never a side effect).
  Measured: 8 presses → 3–6 requests, every press counted, the last one
  landed ≤41 ms after the key.
* **A redo burst left the OLDEST value in the field** while the store held the
  newest: the payload was newest-first and the client writes entries in
  order. `/redo` now answers chronologically (last write = newest); `/undo`'s
  newest-first order was already right for the same reason.
* **The committed waveform did not follow an undo.** The detail render
  stashes `root._committedPlot`; an undo reverted the numbers in place and
  then drew that plot — the pre-undo waveform under the reverted values.

**The RAM idea, applied where it pays.** Values were already fast (~50 ms
server round trip, and the truth must stay on the server: two windows and
auto-apply share the change log, so an optimistic local undo would risk
divergence for a 50 ms gain). The waveform is where a RAM cache is exactly
right: every committed waveform the page has drawn is cached by (pulse path +
the committed value of every parameter), bounded to the last **200** states —
the undo journal's own depth (`undo_journal.MAX_UNITS`), so every state a press
can reach is one the page may have drawn; a decimated waveform is ~30–60 KB, so
200 is ≤10 MB, nothing to a desktop browser (the bound is depth, not memory) —
so a press back to a state already seen redraws with no synth request; the live
PREVIEW is memoised the same way, keyed on the committed base + the overrides
(typing back to a value, Escape, a slider retracing its path: no request); a miss is
ONE synth call for the final state of the burst (its own generation token —
a preview fetch racing it can neither drop it nor be drawn over by it), and
the stale committed plot is never drawn while that refresh is pending. A
burst is one debounced refresh; the inspector is never re-rendered by a press.

Pinned by `pulses_undo_selfcheck.cjs` (13 cases, mutation 8/8),
`ctrlz_selfcheck.cjs` (coalescing, auto-repeat, the inline-field rule) and
`test_undo_journal.py::test_undo_n_pops_k_actions_in_one_request` /
`test_undo_burst_stops_at_the_journal_boundary`. Real-Chrome scripts:
`cdp_burst.js`, `cdp_focus_undo.js`.

### 4e-review. The adversarial review of eaa0f05 / 3885487 / 6d57eea (2 major, 6 minor — all fixed, all pinned)

* **The waveform cache key could not see every parameter (major).** It hashed
  the `input[data-param]` rows only; a list row (`waveform_I`), a runtime row,
  a re-link, or an undo at a POINTER TARGET (a shared `#../x180/length`) changed
  the waveform under an unchanged key, and the cache served the wrong plot
  (jsdom-reproduced: synth calls 0, plot 0.9 while the store held 0.1). Rule
  now: an undo the key can see (an inline input's own path, a value kind) is
  repainted in place; anything else re-renders the inspector from the server —
  the same thing a commit does — which re-caches under the true key. Pointer
  inputs carry `data-target-path` so an undo at the target counts as "mine".
* **A stopped burst dropped presses silently (major).** `/undo?n=k` now runs
  under one `store._lock` (the "one lock" claim was false before) and answers
  `requested / consumed / stopped / level`; the client re-queues the presses
  a journal boundary could not consume (each walks the journal on its own),
  shows a mid-burst error as an error and resyncs the grids, and drops the rest
  only when the log is genuinely exhausted. `/redo` the same.
* Minor: the auto-repeat guard sat before the "not ours" bail-out and hijacked
  a held Ctrl+Z in every ordinary textarea (moved after it); a dirty inline
  field + Ctrl+Shift+Z is left to the browser (a server redo would overwrite
  the typing); `_revertTreeNode` writes the model even when the leaf was never
  materialised (both explorer trees tried; the node is painted only if it
  exists); the reverted baseline is the LOSSLESS `old_value_disp`, not `%.6e`
  (a 7-sig-fig key was a guaranteed cache miss and a truncated baseline); a
  search clear / column re-tick schedules the hydration pass in a rAF — which
  runs before the next paint, so no frame of empty tds either way; the
  "synchronous" version this note first claimed forced a layout inside the
  keystroke and was reverted in §4i (corrected in the 4l review).

## 4f. The Diagnostics list that needed F5 (user report, 2026-08-28)

After a sync brought good values back, the red rows on /diagnostics stayed
until F5. The topbar pill and the banner already re-fetch on
`diagnostics-changed` (which the sync path fires through `_diagChanged`); the
page's own findings list was a one-shot render with no trigger at all — so
after any sync / undo / apply that changed the verdicts, the page lied until
reloaded. The list now lives in `#diag-findings`, a slot that lifts itself out
of a fresh `/diagnostics` render (`hx-select`) on `diagnostics-changed` /
`stateRestored` — one render path, no second route — and the persisted
bucket filter is re-applied after the inner swap. Pinned by
`tests/test_diagnostics_refresh.py` (mutation 2/3).

Also found while looking: the CQT_20Q chip's four "ext fed from 3 outputs"
errors were TRUE — that state was built by the docs/119 wizard, which gave
every biased qubit a dedicated trigger port (eleven cables for four ext
inputs); the docs/136 check is what finally said so. The shared cabling
(ext N ↔ con1/fem4/pN, the PJ bench's layout) was staged into the working
copy for the user to apply or discard; the PJ original was consistent.

## 4g. Feedback round of 2026-08-28 (afternoon)

* **Compare waveforms was dead.** `style.css` hides any `.state-review-overlay`
  whose `.state-review-host` has no children (the empty-host click-trap guard);
  the compare card was not a host, so the overlay stayed `display:none` while
  the fetch ran and the plot rendered into an invisible 0×0 div. Real Chrome
  confirmed (computed display none, rect 0×0, svg present). The card is a host
  now; `smModalOpen` sees the overlay; Escape closes it. `test_pulse_compare_overlay.py`
  audits every overlay that borrows the class.
* **The Diagnostics list needed F5** (§4f).
* **The tree stays a tree** on a broad search (§3, revised).
* **"go to field" on a pulse parameter opened the qubit inspector** on the
  Pulses page — `UndoNav.ownerSurface` now routes a pulse-owned path to
  `/pulse/detail` there.

## 4h. The Config Manual, rebuilt: every class the environment offers

The user's three points: the body text read like a comment (grey), the list
was flat, and — the important one — it listed only the keys of the classes the
open chip already used, when the point of a manual is to show what is
*available*.

* **The catalogue.** `probe_state_schema.py --catalog` enumerates every
  `QuamComponent` subclass the env offers WITHOUT walking unknown packages (a
  lab package may talk to instruments at import): quam's own component modules
  and quam_builder's superconducting architecture are imported by name, the
  chip's classes as requested, and the rest comes from the subclass closure —
  descending through private ancestors (`_OutComplexChannel` is the parent of
  every IQ/MW channel; the first cut lost eight classes there). On the cqt env:
  **103 classes, ~750 keys, ~490 with the class's own words, 2.4 s, 375 KB**,
  cached per env (`state_schema_catalog.json`, same version + signature
  freshness as the manifest).
* **Quiet loading.** The catalogue rides the chip-load warm-up thread after the
  chip's own manifest; a cold `/api/manual` starts it in the background and
  answers with what it has (`catalog_state: loading`); the open window re-asks
  every 3 s and fills in when it lands. No request ever waits on it.
* **The model.** `manual_entries(..., catalog)` merges catalogue + manifest
  (the manifest wins for a class both know — it was probed for this chip), so
  every class is listed; a class the chip uses carries `used` and its keys
  carry clickable places. `node_keys` falls back to the catalogue for a class
  the chip probe did not cover. Categories come from the probe (module + name
  heuristics: Roots, Qubits, Qubit pairs, Channels, Ports, Pulses, Resonators,
  Flux & couplers, Gates & macros, Octave, Hardware, Lab (quam_config), Other).
* **The window.** Category › class › key, two collapsible levels; a class the
  chip uses opens, others collapse until a search reaches in; descriptions in
  the normal foreground (muted is for annotations only: type, default, source,
  places); an SM-blue edge, rounded, resizable with the size remembered.

Pinned by `test_key_manual.py::TestCatalog`, the cqt probe pin in
`test_key_manual_probe.py`, `test_config_manual.py` (route state, window CSS)
and `config_manual_selfcheck.cjs` (categories, open/collapsed rules, loading
re-ask, XSS, size restore).

## 4i. The Live-Edit mount: where 1.3–2.2 s went, and the layout the grid never needed

A phase clock in `BulkEdit.mount` (`window.__bulkMountTimings`, always on)
read by `cdp_mount.js` on three fresh loads of the 20Q chip:

| phase | before | after |
|---|---|---|
| virtualization (`_virtInit`) | 1,094–1,822 ms | plan 5–7 + detach 95–190 ms |
| stats | 140–220 | 140–210 (untouched) |
| everything else | < 100 | < 100 |
| **mount total** | **1,334–2,193 ms** | **323–474 ms** |

Three causes, in the order they were found:

1. **`innerHTML` round trip.** `_virtInit` serialised every cold cell's HTML
   into a map and re-parsed it on hydrate — 4,260 serialisations per mount.
   The cell's NODES now move into a `DocumentFragment` and come back verbatim.
2. **The width freeze by attribute selector** — the docs/141 §4d finding met
   again: 300 `th[data-col-key=…]{min-width}` rules were candidates for every
   element at the next style recalc. By class (`th.ck-N`) now.
3. **A forced layout of the whole table.** `_virtInit` decided coldness from
   `th.offsetLeft`, which forces the layout of the FULL 8,000-cell table (~450 ms)
   before a single cell had been pruned — and every later geometry read during
   the load (the topbar measure, a textarea auto-grow, the pair grid's sticky
   offset) paid it again on the still-whole table. Coldness is now ESTIMATED
   from each column's value-fit input `size` against the screen width
   (`screen.availWidth` — NOT `window.innerWidth`, which Blink answers by
   running style + layout for the scrollbar question: 1.4 s inside "plan",
   measured); the estimate only has to be conservative, since the scroll pass
   reads real geometry later on a table a fraction of the size and hydrates
   anything it got wrong the moment it is on screen. The cold columns are
   frozen at their estimated width by class (without the freeze a pruned
   column shrank to its header and every hydration widened it back — ~0.9 s
   of layout churn per search keystroke, measured).

Honest notes. (a) The grid appears at ~40 ms but the mount starts at ~2.1 s
after navigation on every load: that is the evaluation of the page's script
bundles (app.js and the per-page scripts are all loaded on every page) — a
load-time item of its own, not touched here. (b) Typing measurements taken
late in the day were 3–6× noisier than the morning's (the machine was in use
and Chrome auto-updated 151→152 mid-session); an A/B in the same session put
the new virtualization at parity with the morning's on the search keystroke,
not slower. (c) The hydration pass on a search runs in rAF again — a
synchronous pass forced a style+layout inside the keystroke.

Pinned by `bulk_virt_selfcheck.cjs` (geometry-read counters on the header
getters and on `innerWidth`: the mount reads none; coldness by estimate; the
scroll pass and the search clear hydrate what is on screen).

## 4j. Pulses rows: patch the row, not the table

Every undo / redo / discard / value commit re-fetched the whole Pulses table
(`pulses-changed` → `GET /pulses?rows=1`, ~500 rows with a server-side
sparkline each, 400 ms debounce) and re-rendered it wholesale — selection and
scroll position with it. Now a `pulses-changed` event that CARRIES paths
(`{"paths": [...]}` — /undo, /redo, /discard and a `/pulse/edit` value commit
emit it; the server expands each path to its operation plus every operation
whose pointer resolves into it, `_pulse_rows_touched`) is handled by app.js,
which re-renders only those rows through the new `GET /pulse/row?path=` —
`_pulse_row.html`, the same partial the table loop uses — keeping a patched
row's checkbox. Two event NAMES, not an htmx `[filter]`: a trigger filter
needs eval and the app's CSP forbids it, so a filter is silently ignored
(measured — the whole-table re-fetch fired anyway). `pulses-rows-changed`
carries the paths; `pulses-changed` stays the structural / legacy whole-table
re-fetch (create / delete / rename / duplicate / a state restore). More than 24 touched rows ⇒ the server
says structural instead. Pinned by `TestPulseRow` (route, one-template
identity, the trigger payload after an edit and its undo including the
pointer-linked sibling) and `undo_pages_selfcheck.cjs` (the client patcher).

## 4k. The pulse inspector is a view of up to four pulses (user-directed)

Users want to adjust the parameters of every pulse they are looking at — the
CZ macro's qubit flux AND its coupler flux, and the pulses they put side by
side with Compare — not one editable table under a read-only overlay.

* **The view.** `/pulse/detail?path=<main>&paths=a,b,c,d` (≤4) renders one
  plot and one parameter section per pulse. A main pulse alone still brings
  its companions (a pair macro's other slots) as sections; Compare opens the
  same route with the selection. `_render_pulse_detail` builds one context
  per pulse (`_pulse_section_ctx`, the old body factored out) and the
  template renders `_pulse_params_section.html` per section — the table
  loop and the single view share it. Every form carries `view_main` +
  `view_paths`, so a commit re-renders the same view with the same main.
* **One colour per pulse, in the plot and in the section.** Section 0 = the
  app's primary, then the overlay hues; the plot draws each section's
  committed traces in its colour (I solid, Q dotted), a dirty section's
  preview dashed in the same colour; the section carries the colour as a
  quiet left rule + swatch, with a role chip (qubit / coupler / pair), the
  owner and the macro or `channel · op`. The "in view" bar lists the pulses
  as chips (× drops one, the picker adds one, cap 4) — both re-render from
  the server: one mechanism, one truth.
* **Previews per section.** `collectOverrides` is scoped to a section (a
  dirty coupler field is not a qubit override); each dirty section gets its
  own synth with its own generation token; the committed-plot cache and the
  undo refresh are per section (`committedKey(sec)`), and a change the
  in-place repaint cannot express re-renders the view (`reloadView`).

Verified in real Chrome on the 20Q chip: the CZ macro opens as qubit +
coupler sections (two colours, both editable); Compare of two xy pulses gives
two sections; editing a float in section 2 previews in its colour; Enter keeps
both sections; Ctrl+Z keeps both; no console errors. Pinned by
`TestPulseView` (two sections, colours, the view survives a commit, the cap)
and `pulses_undo_selfcheck.cjs` (unchanged pins over the sections model).

## 4l. Page-specific script bundles, loaded when the page is first visited (2026-08-28, evening)

**What shipped.** `base.html` no longer loads every script on every page. Nine
files stay core (htmx, split, search-query, `app.js`, auto-apply, plot-theme,
calc, manual, undo-trail — `PlotTheme` is read bare in nine places of `app.js`,
so it cannot leave). The other fifteen are grouped into named **bundles** and
the template emits, per full-page render, only the current page's bundles as
eager `<head>` tags (the parse-time inline scripts of `_bulkedit.html`,
`_datasets.html` … are unchanged and still find their globals) plus ONE
`#bundle-manifest` JSON naming every lazy file, its versioned URL, the bundles
and the page map. `window.Bundles` in `app.js` is the loader:

| bundle | files | pages |
|---|---|---|
| grid | bulk-edit, pair-edit, all-values | bulk, table |
| pulses | pulses | pulses (+ every `/pulse/*` partial) |
| wiring | topo-graph, wiring-grid | instrument |
| chipstatus + components | topo-graph, chip-status / topo-graph, component-map | topology (+ its `/wiring` alias), trends |
| components | topo-graph, component-map | qubits, pairs, resonators, flux, couplers, qdac, qubit/pair detail |
| generate | topo-graph, wiring-grid, pulses, generate, generate_preview | generate, regenerate |
| datasets | dataset-virtual, ndview | datasets, dataset detail/compare, collections, fit-audit, trends |
| scheduler / autofit / compare | scheduler / autofit / topo-graph + compare-hub | scheduler / autofit / compare-hub, diff |

Three seams, because htmx has three ways of putting content on the page:

1. **`htmx:confirm`** — every htmx request passes through it and `evt.detail.issueRequest`
   is the documented way to hold one. The loader maps the request URL to its
   bundles (a regex table mirroring the routes); if any file is missing it
   `preventDefault`s, appends the tags (all at once, `async=false` so they
   EXECUTE in bundle order — topo-graph before component-map), and issues the
   request when they have loaded. A lost script still issues the request —
   the page renders and its widgets degrade, which beats a navigation that
   silently never happens. The failed file is not marked loaded, so the next
   navigation retries it.
2. **Back/Forward** — htmx restores a history entry WITHOUT an `htmx:confirm`
   (from its localStorage cache, or its own `HX-History-Restore-Request` xhr),
   and the restored content's inline scripts expect the page's bundles. Real
   scenario: full `/bulk` → sidebar → `/explorer` → F5 (core scripts only now)
   → Back. Measured in real Chrome before the fix: `BulkEdit undefined`, the
   grid unmounted. htmx chains the previous `window.onpopstate`; the loader
   chains htmx's — installed at `DOMContentLoaded` AFTER htmx assigns it (app.js
   runs during parse, before) — and holds the ORIGINAL event until the bundles
   are here. After: `BulkEdit object`, 4,280 cold cells (mounted + virtualized),
   zero console errors.
3. **Global controls that reach into a bundle** — the Settings font/bold/spacing
   buttons for the grids call `Bundles.call('grid', 'BulkEdit.setFont', 0.85)`:
   load, then call; a missing target toasts instead of throwing.

**Measured (A/B against a HEAD worktree on another port, same session, warm cache).**
Script bytes per page: 2,236 KB on every page → 976 KB on `/explorer`, 1,035 on
`/pulses`, 1,229 on `/topology`, 1,259 on `/bulk`. DCL on the light pages moved
100–200 ms (`/explorer` 402→279, `/pulses` 249→82 on the first visit; within
noise on repeats). **And a correction to §4i's open item**: the ~2 s before the
`/bulk` mount is NOT script evaluation. With a warm cache all 2.2 MB of scripts
evaluated in ≈0.2–0.3 s (HEAD `/explorer` DCL 229–402 ms). The `/bulk` document
is **8.7 MB of HTML**: first byte at 1.1 s (server render), bytes complete at
1.3–1.6 s, then a 1.1–1.2 s long task (parse + inline mount), then 0.5 + 0.3 +
1.1 s of style/layout long tasks after DCL (the first paint of ~8,000 cells).
That is where a cold `/bulk` goes; the bundle split does not touch it, and the
next lever there is the size of the rendered grid, not the scripts. The cold
`/bulk` mount clock itself stays at ~0.33 s (§4i).

**What this means for the app.js split the user asked about.** The loader is the
infrastructure; new page-specific JS should be born as a bundle. Carving the
existing `app.js` (842 KB) is a different question: its biggest block, the JSON
Tree Viewer (90 KB), is used by eight page families (explorer, diff, compare,
dataset detail, the component pages, chip status, the wizard) and its IIFE also
houses `NumberInput`, `_groupDigits` and `armPlainResize`, which the grids and
the wizard read — it is shared UI, not a page feature. The clear-cut
page-specific pieces (unified compare tree 16 KB, the instrument renderer ~30 KB,
the dataset plot-apply popup ~35 KB) total ~10% of the file, and eight jsdom
harnesses plus the greppable pins would follow each move. Given that script
evaluation is ≈0.3 s warm for everything, the split is a maintainability
decision, not a speed one — recorded here so the numbers are on the table.

**Pinned.** `tests/test_bundles.py` (every page ships the core + only its own
bundles, no file twice; the manifest names every lazy file once and every file
exists; the JS path map agrees with the page map; no inline handler calls a grid
global directly) + `tests/bundles_selfcheck.cjs` (40 asserts, the loader block
executed under jsdom: manifest, present tags, URL map, ordered loading with
`async=false`, the `htmx:confirm` hold, the lost-script contract, the
Back/Forward hold with the ORIGINAL event, `Bundles.call`). `test_web`'s
eager-script pin now checks `dataset-virtual.js` on the rendered `/datasets`
page rather than in the template.

### 4l-review. The adversarial review of 6d57eea..a7e8959 (five reviewers, 2026-08-28 night)

Five independent reviewers (rows patch / multi-pulse inspector / Config
Manual catalogue / grid mount + search / bundle loader) against the day's
commits, each with executed reproductions. **1 critical, 12 major, ~30
minor** confirmed; every one fixed below except the four deferred at the end.
Every claim I re-checked before touching code; the two review claims that
turned out to be pre-existing (undo_nav U7, pulse_overlay) had already been
fixed in a7e8959.

**Rows patch (docs/141 §4j) — the CRITICAL.** `htmx.ajax` with no `source`
attributes every call to `document.body`, and htmx's default `hx-sync`
strategy on an element with a request in flight is `last` — it DUMPS the
queue. Three rows changed by one edit (`x180_DragCosine` + the `x180` alias
+ `x90_DragCosine`): the middle one was never patched (reproduced with the
real htmx under jsdom). The listener now issues a plain `fetch()` per row,
re-targets the `<tr>` at LANDING time (an earlier swap may have replaced it)
and keeps a per-path generation so an older response never overwrites a
newer one. It also carries the page's active `q` / `channel`: `/pulse/row`
answers **204** for a row that no longer matches the filter and the client
removes it (`_pulse_rows_filter` is the one filter for the page and the
row). Server side, `_pulse_rows_touched` returns **structural** for a path
that IS a pulse (create / delete / rename / duplicate, or their undo — a
ghost `sat_copy` row with a 404 behind it, a restored pulse that never
appeared) or that no pulse row owns (the `anharmonicity` a DRAG pulse points
at — the sparkline changed, the trigger said `paths: []`); undo/redo go
through `_pulses_changed_for_entries`, which is structural for a
created/deleted entry or a pointer on either side (the old target's
`used_by` changed too); `/pulse/edit` resolves the OLD target of a re-link /
break-link BEFORE the write and names its row. The doc line in §4j saying
`/discard` emits the paths event was wrong — it emits the plain structural
event, which is correct. `UndoNav.pulseRootOf` now mirrors the server's
`_PULSE_PATH_RES`: a pair macro's pulse is its `flux_pulse_qubit` /
`coupler_flux_pulse` SLOT (the old regex sent the macro root to
`/pulse/detail`, a 404 toast — and `undo_pages_selfcheck` pinned that wrong
URL). Diagnostics: the self-refresh keeps the user's folded domains
(`htmx:beforeSwap` snapshot, re-applied after) and also fires on
`liveDriftChanged` (a scheduler adopt or another window's write reached the
banner but not the list).

**Multi-pulse inspector (§4k).** A stale `view_main` (the main pulse renamed
by another window while a companion was being edited) committed the write
and then answered a 404 the UI drops — the response re-renders around the
pulse just edited instead. An undo burst mixing a reload-needing entry with
an in-place one inside the 120 ms window LOST the reload (same debounce
key): a reload once due stays due. An alias section's inputs live at the
target's paths — the listener matches `actualPath` too and `_revertCell`
repaints EVERY form carrying the path (an alias and its target can share
one view). One error line per section (a section's preview success no
longer hides another's failure); per-section pending flags (two overlapping
refreshes no longer zero each other's counter); a reload that does not land
clears the pending flag instead of wedging the preview; the config
ground-truth trace has its own colour + `longdash` (committed Q and verify I
were visually identical). Never a silent drop: a pulse beyond the four, or
unknown, is NAMED under the view bar. `paths=` is a REPEATED param (a comma
is legal inside a foreign op name; a comma-joined value is still accepted
when its parts are pulse paths), the view bar carries a JSON list, forms
carry one hidden input per pulse. The 4-cap unchecks the box just clicked;
the dead compare-overlay code is gone. Deferred: rename/duplicate/delete
from a compare view return to a single-pulse view (a rename changes the
path the view names).

**Config Manual catalogue (§4h).** The 400-row budget emitted class HEADERS
with no keys for every class past the first ~400 keys — which on the real
cqt catalogue (Roots 32 → Ports cumulative 433) is exactly where a chip's own
classes sit. Past the budget, a collapsed class now defers its keys to its
first toggle and only OPEN classes are charged (a small result renders
eagerly, as before). A failed probe was forgotten instantly — every
`/api/manual` spawned another subprocess and answered "loading" forever (41
launches per open window with the 3 s poll): the outcome is remembered per
interpreter (`catalog_state: error` + the reason, a 60 s backoff), a
`partial` catalogue (a root that is installed but broken — measured 68
classes with quam_builder made unimportable) is served, named, and NEVER
cached as the truth (an ABSENT root, quam_builder 0.2.0, stays fine), the
chip-load warm and the manual share one single-flight key, and a warm
manifest no longer skips the catalogue. The catalogue depended on which chip
warmed it (`quam_builder.common.pulses`' four classes were absent until a
chip using one opened first): `quam_builder.common` joined the roots and the
cache remembers the requested class set, unioning across probes — a chip
whose class the cache never saw re-probes. Two classes sharing a leaf name
(quam's and quam_builder's `DragPulse`, nine such pairs) merged into one row
with duplicated keys: grouping is by class PATH now, with the module shown.
`FluxTunableQuam` was filed under "Flux & couplers" (Roots rule first in both
classifiers); abstract classes are badged; the 1 MB cache file is memoised
on (mtime, size); a poll re-renders only when something changed and keeps
what the user opened; the poll cap is per open; opening re-asks (the env can
change under the same chip); only a size the USER set is remembered.

**The grid (§4d, §4i).** Two harness defects: `bulk_search_selfcheck`'s
four `ck:` pins sat AFTER the exit gate (a mutant with no `sh-N` toggling
passed green), and `bulk_virt_selfcheck`'s fixture had no `ck-N` classes —
the width freeze could never fire, and the harness pinned that as "no width
freeze" (deleting the freeze, or the hidden-column hydration guard, passed).
Both fixed and the pins made real (every cold column frozen by class, no hot
one; a search-hidden column stays cold through a scroll; a hidden-at-mount
column is frozen too). Code: an undo naming a path with no cell used to
`_virtHydrateAll()` — a pair-grid or hidden-column path un-virtualized the
whole grid (1,170 → 0 cold, then every keystroke paid the 4d cost); a
`byPath` map built while detaching hydrates ONE column. The estimate reads a
drag-resized column's real width (`quam_bulk_col_widths`; a narrowed grid
showed blank on-screen cells until the first scroll) and derives px/char
from the real cell font — the UI font size is a `data-font-size` attribute
mapped by the stylesheet (17 px default, 15 / 19), `--bulk-fs` / `--bulk-ls`
are inline on the root, so no computed style is read (jsdom evaluates media
queries for it — a viewport read); one rAF pass after the mount hydrates
anything the estimate got wrong; a cold column keeps its header stats and
gets them computed when hydrated; `_ph` survives a missing `performance`.
The 4e-review note claiming a rAF pass "paints one frame of empty tds" was
wrong (a rAF runs before the next paint) — corrected in the code comment
and in §4e-review above.

**Bundle loader (§4l).** No critical/major. `issueRequest(true)` also skips
the element's own `hx-confirm` (measured with the real htmx 2.0.4) — the
plain call does not re-fire `htmx:confirm`, so it is used now. A held
request was invisible to `hx-sync="replace"`: click `/bulk` (held), click
`/explorer` — the later click lost. A sequence per target reproduces
`replace` (a newer request for the same target supersedes the held one).
PaneState's 60 ms mismatch check on a Back could fire while the Back's
bundles were still loading (purging htmx's history cache and re-fetching an
8.7 MB grid twice): it waits on `Bundles.pending()` first. Tags are rescanned
before a load (a failed tag leaves the page so the retry appends a fresh
one); the `/qubit/<id>` and `/pair/<id>` inspector partials reference no lazy
global and no longer pull the components bundle; the pins now cover every
PATHS regex, every page token and every route spelling (a removed regex or
token passed all of them before).

**Pins added / rewritten.** `undo_pages_selfcheck.cjs` (fetch-based rows,
newest response wins, 204 removes, missing row → structural, the SLOT
regex), `pulses_undo_selfcheck.cjs` (mixed burst keeps its reload),
`pulse_overlay_selfcheck.cjs` (repeated params, typing in a companion section
previews THAT section), `bundles_selfcheck.cjs` (50 asserts: plain
`issueRequest`, hx-sync sequence, `pending()`, lost-tag retry, every route
family), `bulk_virt_selfcheck.cjs` (ck-N fixture, freeze by class, one-column
undo hydration, hidden stays cold), `config_manual_selfcheck.cjs` (lazy past
the budget, leaf collision, error stops polling, open kept across a poll),
`TestPulseRow` (re-link names the old target, pointer/structural undo,
`/pulse/row` 204, `_view_paths_split`), `TestPulseView` (exactly four AND the
fifth named, repeated params, stale `view_main`), `test_config_manual.py`
(partial never cached, requested-set union, a failed probe remembered),
`TestReviewRound4l` (Roots, abstract), `test_undo_trail.py` (alias
sections), `test_bundles.py` (the whole page map + every PATHS regex).

**Deferred, on purpose.** No progress indicator during a bundle hold (tens
of ms on localhost); the Pulses table's count header after a 204 row removal
(the next structural refetch corrects it); rename/duplicate/delete from a
compare view fall back to a single-pulse view; the dataset-poll-driven
`liveDriftChanged` reaches the Diagnostics list only through the drift
poller's own cadence.

## 4m. The /bulk document: 9.0 MB of which 51% was whitespace (2026-08-29, user-directed)

**What was measured first.** The user asked what could be done about the
~2 s before the grid mounts, which §4l had pinned on the DOCUMENT, not on JS.
Rendering `/bulk` in-process on the PJ 20Q chip: 8,977,141 bytes, 7,810
cells (qubit grid 4,480 + pair grid 3,330 in the live render — the "1,530"
this line carried until docs/141 §4ac did not reconcile with the 7,810 beside
it: the pair grid is 111 columns x 30 pairs), **1,140 bytes per cell of which ~250 were information**;
42,072 elements, 36,723 whitespace-only text nodes. Where the bytes went:
3.6 MB was whitespace — each `{% if %}` in the cell template left a blank
indented line, and every `<input>` carried its eight attributes on eight
32-space-indented lines; 0.69 MB was the before→after hover chip
(`.bulk-ba`, four spans) rendered in every cell for the one the user might
hover; 0.17 MB the empty `.bulk-band-msg`; 0.6 MB `title` attributes of which
113 KB were a plain copy of the dot-path; `data-resolved` equal to
`data-dot-path` on 3,332 cells.

**What was done — 1 + 2 of the six options given.** ① The two cell blocks of
`_bulkedit.html` are one tag per line with Jinja whitespace control
(`{%- -%}`), and a single literal space is kept wherever the render had
whitespace between two INLINE siblings — the quote marks around a numeric
string, the pointer glyph after the input, the ✎ button after a list preview,
the ↗ link in a pair list cell. Whitespace at a cell's edges and between cells
is not load-bearing (leading/trailing collapsible space in a block is
discarded; whitespace between table cells never forms a cell) and is gone.
`trim_blocks` was NOT turned on globally: it would touch 63 templates for no
measurable gain outside this one. ② `.bulk-ba` and `.bulk-band-msg` are no
longer rendered: `_ensureBA(td, cell)` in both grids creates the chip on the
first hover of a MODIFIED cell (old text = `data-baseline`, which every path
already set before marking a cell modified; fallback `data-orig`), and
`_ensureBandMsg(td)` creates the message the first time a cell actually
warns, inserted before `.bulk-phys` where the render used to put it. The
apply / cross-table-sync / markModified sites keep their `if (old) old.textContent = …`
— a no-op until the chip exists, in sync after.

**How the render was proven identical.** `scratchpad/bulk_golden.py`
tokenises every `.bulk-td` of the in-process render (elements with sorted
attributes, text, and whether a whitespace text node sits between siblings),
drops the two deliberately removed subtrees and edge/adjacent whitespace, and
compares before vs after: **7,810 cells, 0 differing.** Result: 8,977,141 →
3,903,227 bytes; elements 42,072 → 21,528; whitespace text nodes 36,723 →
6,000 (the remainder is between rows and header cells). Per cell (whole document / cells) 1,149 → 500 bytes; counting only what a
cell's own markup contributes, 1,140 → 404. §4ac: the two denominators were
mixed here, and neither number followed from the totals this section states.

**What it bought in real Chrome (headless, PJ chip, the HEAD tree served on
5198 beside the change on 5199, 10 alternating loads each, medians).**
Document 8,767 → 3,812 KB. Main-thread long-task time before `load`
1,022 → 750 ms; the parse+mount long task 738 → 391 ms; `load` 2,013 →
1,825 ms; the mount's `stats` phase 70 → 34 ms and cold-column detach
79 → 55 ms (fewer nodes to walk). First byte unchanged (~450–500 ms — Jinja
was never the cost) and DCL within the run-to-run noise (1,305 vs 1,572
medians the wrong way, ±500 ms spreads). Honest reading: the change halves
the document and the parse task and takes ~0.2–0.3 s off the load; it does
not touch the ~0.45 s server floor or the layout of a 329-column table.

**Not done, deliberately.** `data-resolved` dedup (~170 KB after the cut): a
cell whose own path is another cell's resolved target must still be found by
the `[data-resolved="…"]` link selectors, so every selector in two files would
need a `:not([data-resolved])` twin — small win, real risk. Plain `title`
copies (113 KB): a lazy `title` on hover for 3% of the file. `data-col-key`
on every td (531 KB — dynamic keys are long): 76 JS consumers. The next real
lever is option 3 of the list given to the user — the server rendering only
the columns near the viewport and shipping the rest as the value map the
client-side hydration (docs/105) already keeps — a separate campaign with
search, column history, undo repaint and paste all riding on that map.

Pinned by `tests/test_bulk_markup.py` (one-line cells, no whitespace between
cells, the one space between inline siblings for all four cases, no per-cell
chip or message, average cell < 650 bytes, every attribute the grids read
still present) + `bulk_markup_selfcheck.cjs` (lazy chip and message against
the real grids, 22 asserts). Mutation-checked 5/5 — a glued sibling, the old
template, no chip creation, no message creation, a chip created on mouseout
each trip exactly the pin written for it.

## 4n. The server stops rendering what the client was about to throw away (2026-08-29, user-directed)

**The decision.** After §4m the user chose option 3 of the six offered —
"do it now, while the code is still this size; modularity and stability
both, and speed" — so the columns past the client's look-ahead window are
now rendered COLD on the server: an empty `<td>` that keeps its identity
(`ck-N`, the flag classes, `data-col-key`) plus one value map, and filled on
demand. Before this the server rendered every one of ~8,000 cells for the
client to detach two thirds of them at mount (docs/105): 68% of the
server's time was Jinja (cProfile: 0.67 s of 0.98 s under the profiler —
`_build_bulk_cell` was 0.16 s, gzip 0.05 s), and the client parsed 4,000
cells it then pulled out of the DOM.

**One renderer, three seams.** The cell contents moved out of
`_bulkedit.html` into `_bulk_cell_macros.html` (`qubit_cell`, `pair_cell`):
the page renders every hot cell through them and `GET /bulk/cells` renders a
cold column's cells through the SAME macro — a hydrated cell is byte-
identical to one rendered with the page, and that identity is a pinned
property, not a convention. `core/bulk_virt.py` is the planner: the
client's own estimate mirrored (cumulative value-fit widths from `maxlen`
against the viewport × 2.5, hidden-at-mount columns cold and widthless, the
600-cell / 800-cold gates), deliberately CONSERVATIVE. **Corrected in §4ac**:
the plan matches the client EXACTLY when the `vw` hint arrives (the hint is
`screen.availWidth`, the same quantity `_virtInit` uses for its own edge), and
is conservative against the client's DEFAULT font scale. The original sentence
here — "a server-cold column is always one the client would have detached,
never fewer hot columns than before" — is false in two measured
configurations: a page loaded WITHOUT the hint (a full load, an F5, a
bookmark) is planned for 1,920 px, so a 2,560 / 3,440 / 3,840-px screen gets
5 / 20 / 26 columns cold the client would have kept hot; and the Live-Edit
table-size slider (`--bulk-fs`, min 0.75) takes the client's px/char below the
server's 8.0, putting the server 2–6 columns ahead. Neither is visible at
`scrollLeft = 0` — the first such column sits past 4,000 px — and the mount's
own rAF pass hydrates whatever is on screen; what is lost is look-ahead
buffer, one extra `GET /bulk/cells` on the first sideways scroll. The
constants were deliberately NOT lowered: doing so gives back a measured part
of this section for every user (198 → 164 cold columns on the PJ chip), and
"server ⊆ client" cannot be pinned as a property anyway — a drag-resized
column (docs/111 `quam_bulk_col_widths`) overrides the client's own estimate
and the server cannot know it. What IS pinned now is that every width metric
the server mirrors matches the client's, which is the part that can be true.
The
client's `htmx:configRequest` hook sends `vw=screen.availWidth` beside
`dynhide`. `_qubit_bulk_grid()` left `bulk_edit()` so the grid can be
memoized per context on (`mutation_seq`, change-log length, `dynhide`) —
the all-values ETag's own key — and a hydration a moment after the page
renders reads the very dicts the page rendered from; a mutation in between
changes the key and the column arrives from the current working copy. The
route takes `?cols=`, names unknown keys, answers 409 when a different chip
is open in this context (`?chip=`), gzips like `/bulk`.

**The client adopts, then fetches.** `_virtInit` reads the server-cold tds
and `#bulk-cold-map` (display + paths per cell, row order) into the same
`_virt` the client-side detach fills — `vals` for whole-chip search,
`byPath` for path-addressed repaints — marked `remote`; it adopts them
whatever its own gates say (the server applied the gates), reads no
geometry (pinned), and freezes a cold column's width from the header's new
`data-maxlen` (a cell-less column had no `size` to read; the estimate is
now independent of row 0 for every column). `_virtHydrateCols` returns a
promise: local fragments land before it returns, remote columns after ONE
`GET /bulk/cells` per pass carrying every due column, a column in flight
never asked for twice, a failed batch left cold with one honest line and
retried on the next pass, a 409 naming the chip and asking for a reload.
Callers that need a cell NOW: the apply-echo sync hydrates only local
columns (a server-cold column is rendered from the working copy when
fetched — hydrating it here would fetch the whole grid on every apply);
the undo repaint skips remote columns for the same reason (they count as
`missing`, which triggers no rebuild — docs/122's contract); the dyn-reload
edit carry places what is here and the rest when the fetch lands; a sort by
a cold column fetches, then sorts; Tab into a cold column starts the fetch
and lands on the next keypress.

**Proven identical, twice.** The macro refactor alone: the hot render's
7,810 cells, 0 differences. Then the cold render (`vw=1600`, 4,040 cold
cells, 202 columns) hydrated through `/bulk/cells` the way the client does
and tokenised: 7,810 cells, 0 differences against the hot render. In-process
on the PJ chip: `/bulk` 3,903,227 → 2,903,518 bytes (the cold map is 244 KB
of that), warm server time 411 → 276 ms (the first render after a mutation
builds the grid: ~420 ms), a 5-column hydration 6 ms / 43 KB.

**Real Chrome (headless, PJ chip, b1b9050 served on 5198 beside this
change on 5199, 10 alternating full loads each, medians).** Document
3,812 → 2,848 KB; first byte 445 → 281 ms; DCL 1,510 → 953 ms; `load`
1,814 → 1,253 ms; the mount's cold-column plan+detach is 6 ms for 340
client-detached cells + 198 server-cold columns. Against the morning's
e898cdb (§4m's baseline): document 8,767 → 2,848 KB, `load` 2,013 →
1,253 ms, first byte 494 → 281 ms. Functional, on the same chip: a value
that lives only in a cold column is found by the search (the row shown,
"1 of 20"); scrolling the grid hydrates through `/bulk/cells` with no note
and no console error; a hydrated cell is a real input — typing arms the
row's Apply, restoring the value clears it. The first scroll-to-the-end
fetched every column the user skipped (198 columns in one request), so the
pass became a WINDOW: columns left of `scrollLeft − 1.5 viewports` stay
cold until scrolled back to (keyboard navigation still hydrates through
`_virtEnsureTd`).

**Not in this cut, on purpose.** The PAIR grid renders whole (§4ac, measured:
3,330 cells / ~1.5 MB on this chip — **53% of the remaining document**, i.e. the
largest single block left, not the small leftover this paragraph's ordering
implied): `pair-edit.js` has no virtualization at all and the
qubit grid's mechanism is the one worth generalizing into a shared module
before a second consumer appears — a follow-up, with the same golden. The
cold td skeleton itself is ~0.5 MB (long derived keys in `data-col-key`,
76 JS consumers); the 329 headers ~0.5 MB. Navigation into a cold column
is one keypress late (the fetch), never wrong.

Pinned by `tests/test_bulk_virt_server.py` (planner mirror + gates +
hint clamping, the cold render and its map, hydration byte-identity against
the hot render, the memo and its invalidation, the chip guard, gzip) +
`bulk_virt_server_selfcheck.cjs` (37 asserts, plus the §4ac additions: adoption without geometry,
data-maxlen freeze, cold-value search, one request per pass, in-flight
dedup, landed markup, the windowed pass, failure + retry + 409 note,
undo-missing without a fetch, the apply sync fetching nothing, the carry
landing after the fetch, sort-after-fetch, the vw hint). Mutation-checked
9/9.

## 4o. Chip Status, laid out the way the user asked (2026-08-29, user-directed)

Five numbered items, taken literally. **1)** Overview is the first section.
**2)** Health sits right below it, and the row-level "Tiles" control (S / M /
L + slider, one scale for the whole dashboard) is gone from the Health row —
**2-1)** each panel now carries its own S · M · L right of its title word
("Readout Frequency  S M L"). **3)** Topology follows Health. **4)** Trends
follows Topology. **5)** "Gate (2Q)" no longer exists as a tab: the Fidelity
section opens with the 2Q gate fidelity (RB) panels, then the 1Q gate panels,
then readout. **5-1)** The IQ-blob metric is named for what it is, everywhere
in SM: **Readout Fidelity (GE)** (two-state, from `confusion_matrix`) and
**Readout Fidelity (GEF)** (three-state, from `gef_confusion_matrix`), badge
form "Read. Fid. (GE)" — never "IQ Blob", never "Assign".

**How.** `_wiring.html` is reassembled in that order (a page-title row with
the diagnostics badge leads; the Topology toolbar keeps its own heading);
the sub-nav and the sidebar sub-links follow the same order; the route
accepts `?view=health` and still `?view=gate` (the client maps it onto
Fidelity). `TAB_SPEC.fidelity` builds both lazy hosts; `buildMetricPanels`
renders its fidelity group into `#topo-fidelity-panels` inside the Fidelity
wrapper (after `build2QRBPanels`' block, now a sub-heading, not its own
`<h3>`), the other groups stay in `#topo-metric-panels`. The density
controller is per panel: `controlHtml(key)` beside every metric / 2Q-gate
panel title — S · M · L **and the fine slider** (the user asked it back after
the first cut dropped it; both are ~0.58 em of the title, the letters having
been "too big beside it" — 0.68 rem buttons, a 4 rem slider), with the floor
the user asked for: a panel down to 0.35 (S / M / L unchanged) and the hero
map down to 0.25× (was 0.5×; the 4× ceiling "was already enough"), ONE
delegated click + input listener on the
dashboard (panels are built lazily), the scale written as
`--topo-density-scale` on THAT `.topo-section`
— the derived cell sizes are now re-computed at the section (a custom
property computed on the dashboard never saw a child's override), persisted
per panel key in `quam_chip_density_panels`. The GEF metric:
`query._assignment_fidelity_n` (mean diagonal over ALL n states — the 2×2
formula would silently drop the f row), a node metric, a glossary entry with
thresholds (warn 0.90 / fail 0.80: three-state discrimination runs lower than
two-state on the same readout), a Trends label, and a tracked history
property with a v3→v4 content upgrade (`_DERIVED_FIDELITY_PROPS` is now the
one table the live extractor and both upgrades read). A chip without the
matrix (PJ stores `null`) shows no GEF tile and no GEF panel — never a
permanent "no data" box.

**What the first cut got wrong, and the pin that could not see it.** The
new Fidelity banner comment had no `-->` (the originals' closing was hidden
by a byte-counting `cut` in the terminal), so the wrapper was in the bytes
and absent from the DOM — the raw-text order pin was green while real Chrome
showed no Fidelity section at all. `test_the_browser_sees_the_sections_in_order`
now parses the rendered page with a comment-aware parser and refuses markup
inside any comment.

**The second thing real Chrome showed.** Trends now sits above Fidelity and
is fetched lazily, so a jump to Fidelity (`?view=fidelity`, the sidebar
sub-link) landed on the three Trends charts that arrived a moment later and
pushed everything down. `ChipStatus.jumpGuard` (a top-level core, no mount
needed) remembers the last jump; when the Trends swap lands, a jump made
within 8 s to a section below Trends is scrolled back to (`scroll-margin-top`
keeps the title out from under the sticky sub-nav). Measured after the fix:
Fidelity's top 64 px below the pane top, Trends' bottom above it.

**Real Chrome (PJ 20Q):** order Overview · Health · Topology · Trends ·
Fidelity · (Coherence · Frequencies · Calibration); no size control in the
Health row; Fidelity heads: 2Q Gate Fidelity — RB (Standard RB, six gates) →
1Q Gate & Readout Fidelity → 1Q Gate Fidelity, Readout Fidelity (GE), |g⟩,
|e⟩; 10 panels, 10 controls; M on the first panel writes 0.85 on that
section only (cell 132 → 112 px), the dashboard untouched; no console
errors.

Pinned by `tests/test_chip_status_layout.py` (order — raw and parsed —,
sub-nav, sidebar, the Health row, the route, `TAB_SPEC` / `PANEL_DEFS`, the
controls, the glossary + thresholds + Trends labels, the GEF formula /
QueryEngine / page / history index + upgrade, the jump-guard wiring) +
`chip_density_selfcheck.cjs` (27 asserts, plus the §4ac additions: per-panel size, the fine slider, the jump guard);
mutation-checked 4/4.

## 4p. A new run folder reaches the screen in well under a second (2026-08-29, user-directed)

**The ask.** "When qualibrate finishes and saves a new experiment folder, the
popup should appear almost at once." Before: the new-run popup rode the
sidebar's 60 s `/datasets/poll`, the Datasets table its 5 s
`/datasets/changes-since` — a 5–60 s wait, by design, and every shorter
interval would have been a scan tax on an idle lab.

**The design (the user chose it from the proposal, then "do it now").**
Push, not faster polling. `core/run_watch.py` is a stat-based watcher
(`watchdog` is not in the customer envs): every 0.5 s a daemon thread takes
each active dataset root's *signature* — the root's mtime, the newest date
directory's name + mtime, the newest run directory's name + mtime + the
count — and bumps a tick when any of them moves. Two `scandir`s and three
`stat`s per root, ~1 ms. NTFS/ext4 move a directory's mtime when an entry
appears, so a new run directory moves the date directory; qualibrate then
writes node.json into the run over some hundreds of ms, which moves the run
directory — the second tick is what turns docs/80's `incomplete` run into a
complete one on screen. `GET /datasets/wait?since=<tick>` long-polls on the
watcher's condition (≤25 s, one thread per waiting window — the servers all
run `threaded=True`), refreshing the watched roots from the active dataset
folders on every call; `since=-1` is the handshake, answered at once and
never as a change. `live-wake.js` (a core script) keeps ONE wait open,
wakes the consumers on a moved tick — `sm:runs-changed` for the popup poll,
`DatasetVirtual.pollNow()` for the table — pauses while the tab is hidden,
backs off exponentially on failure. The existing polls keep their cadence as
the safety net; this only makes them fire NOW. Both consumers got an
in-flight guard: a wake during a poll runs once more after it, never in
parallel.

**Three things only real Chrome showed (a scratch workspace root, a run
directory created then node.json 150 ms later, the popup watched at 50 ms).**
① The first cut's client swallowed the first answer ("the page just loaded")
— but on a fresh server the first REAL change is tick 0 → 1, and it arrived
as that first answer: no popup. Hence the `since=-1` handshake. ② A root
added at the handshake was baselined by the thread's next look, so a run
landing in that half second was folded into the baseline: `set_roots` now
baselines a new root synchronously. ③ The popup poll's own first run — the
one that records "the latest run is this one" and never pops — used to
happen at the first `visibilitychange`, so on a freshly opened page the
first WAKE became the baseline and the run that caused it was never shown;
it now baselines 1.5 s after load. Measured after the three fixes, three
trials: **popup visible 544 / 221 / 360 ms after the folder appeared** (the
node.json landed at +150 ms), no console errors.

**Honest limits.** A run landing inside the first 1.5 s of a page's life is
announced by the 60 s poll, not the wake. (§4ac adds two this list missed:
the run mtimes were read through `DirEntry.stat()`, a Windows CACHE read that
never sees a write inside the directory, so the "second tick" below never
fired at all on the customer's platform — `os.stat` now, which also widens
the trigger to any write inside the newest date directory; and a root whose
runs sit directly under it, with no date directories, gets the first tick
only.) A root that cannot be read never
ticks (the polls still scan it). One waiting thread per open window: fine
for a lab's 1–3 windows, not a public server. The stat signature reads the
NEWEST date directory only — a run written into an old date (a clock skew,
a re-run into yesterday's folder) is caught by the regular polls.

Pinned by `tests/test_run_watch.py` (the signature's four movers and
non-movers, baseline-then-tick, roots churn, early wake vs timeout, a
raising signature, the route's clamps + handshake + one-interval answer +
one watcher per app, the wiring) + `live_wake_selfcheck.cjs` (22 asserts:
handshake, one in flight, wake once per change, backoff 1 s / 2 s, hidden /
visible / stop, the popup poll's in-flight guard). Mutation-checked 4/4
(a watcher that never ticks, a handshake that wakes, two in flight, a popup
poll that ignores the wake).

## 4q. One scrollbar (2026-08-29, user-directed)

**The complaint.** On Chip Status the topology map scrolled inside its own
box; on Live State Edit the grid scrolled inside its wrap (`overflow:auto`
+ a `100vh − 185px` cap) — and in both cases `#table-pane` scrolled the
page around them. Users scrolled the grid to its end and then had to scroll
the page again, and did not always know which bar they were holding. "Make
both a single scroll."

**What changed.** `#table-pane` is now the ONE vertical scroller on both
pages. `.bulk-table-wrap` is a frame around a `max-content` table
(`overflow: visible; max-height: none; width: max-content`); the pane's own
horizontal bar sits at the viewport edge, so the top-of-table scrollbar proxy
(`#bulk-scroll-top` + its sync code path) is gone. The sticky parts re-anchor
to the pane with its padding subtracted: `thead th` at
`top: −pad-v`, the second header row at `grouphead-h − pad-v`, the row heads
at `left: −pad-h`, the Apply column at `right: −pad-h`. Cold-column
hydration listens to, and measures, the pane (`_scrollerOf` → `#table-pane`,
the wrap only as a fallback for a table mounted elsewhere); the jsdom
harnesses define their scroll geometry on the pane. The topology hero:
`.topo-hero-scroll { overflow-x: auto; overflow-y: visible; max-height: none }`
— it grows to its map, and a map zoomed wider than the pane still scrolls
sideways inside.

**What real Chrome corrected.** The toolbar rows, the chip bar and the pair
divider were given `position: sticky; left: 0` to stay put while the pane
scrolls sideways — and scrolled away anyway (the toolbar at −2475 px):
sticky needs room inside its containing block, and those rows are exactly as
wide as theirs. They are now moved by `translateX(scrollLeft)` on the pane's
scroll (`_pinBarsToScroll`, one rAF per event). Measured after: `/bulk` wrap
scrollHeight = clientHeight (no inner bar), the pane 3,373 px tall for a
759 px viewport; after `scrollTop = 400` the header row stays at the pane's
top (33 px, under the group band) and q1 has scrolled beneath it; after
`scrollLeft = 2500` cold columns hydrate (4,260 → 4,000 cold cells), the row
heads stay at 0 and the toolbar at 25 px (its padding). `/topology`: the hero
1,073 px = 1,073 px, no inner bar. No console errors.

Pinned by `tests/test_single_scroll.py` (the wrap and the hero rules, the
pane-anchored sticky offsets, the proxy gone from template and CSS, the
scroller choice and the mount-time bar pinning) + the extended
`bulk_virt_server_selfcheck.cjs` (the toolbar follows the pane's scroll);
mutation-checked 3/3 (the wrap scrolling again, the bars unpinned, the wrap
as the scroller).

## 4r. Two checked runs in the sidebar open the Diff, not the hub (2026-08-29, user-directed)

> **Superseded by §4y** (five commits later, the same night). 2–5 ticks all
> open the diff and the Compare hub is retired as a destination; two archive
> runs open on **figures**, not `tab=node`. The pin this section names now
> asserts the opposite of the sentence below. What follows is the state at
> `2012a3d`, kept for the history. (docs/141 §4ac)

The user ticked two runs in the sidebar tree, pressed "Compare Selected",
and landed on the Compare hub ("2 sources … These sources fingerprint as
the same chip") — while the Datasets table's "Compare selected" and the
Versions panel's Compare open the diff workbench (docs/84's front door).
The sidebar form still went through the docs/49 adapter: `POST /compare`
translated every basket into hub `src=` tokens. Now exactly two checked
sources redirect to `/diff?a=…&b=…` — two archive runs on the node.json tab
(what was ASKED differs more often than the chip, as `/diff/runs` chooses),
anything else on state.json — and three or more still go to the hub, the
N-way surface. Pinned in `test_compare_hub_routes.py::TestP4Redirects`
(mixed pair → `/diff … &tab=state`, two runs → `&tab=node` and the page
renders without the hub's "Pick the comparison context", three → hub) and
`test_web.py::test_compare_post_translates_paths`.

## 4s. The clipped popovers (a §4q regression) and the Pairs picker (2026-08-29, user-directed)

**The bug, and it was mine.** The user's screenshot: the Properties and
Qubits popovers on Live State Edit looked cut off at the chip bar. §4q had
given every toolbar row `will-change: transform` so the sideways-scroll
translate would be cheap — and that made each row its own stacking context,
so the chip bar (a later sibling, opaque) painted OVER the menus. The rows
now carry no `will-change`; the inline `transform` still makes a stacking
context while scrolled sideways, so they are ordered explicitly — the
toolbar rows (which own the popovers) `z-index: 8`, the chip bar / notes /
pair divider `6`, both above the sticky table header (2 / 4). Real Chrome:
with Properties open (540 px tall), a hit-test at a point inside the menu
and inside the chip bar's band returns the menu.

**The ask: a Pairs button right of Qubits.** `⚯ Pairs` (only when the chip
has pair rows) opens the same kind of menu for the PAIR grid's rows: every
pair with a checkbox, All / None / Invert / only, a per-chip persisted
hidden set (`quam_bulk_qhidden:pairs:<chip>`), a "N of M pairs — Show all"
pill. It is folded into the existing follow rule (docs/126: a pair hides
when a member qubit is hidden) — the menu says "qubit hidden" for those —
and a pair with an unsaved edit can never be hidden (disabled checkbox,
"unsaved edit"; None and Invert skip it). The Qubits picker rebuilds the
Pairs menu when it changes, so the badges follow. Real Chrome (PJ, 30
pairs): 30 listed, unchecking q1-2 hides its row, the pill reads 29 of 30,
Show all restores. Pinned by `tests/test_bulk_pairs_picker.py` +
`bulk_pairs_picker_selfcheck.cjs` (15 asserts — the dirty-follow guard
needed its own scenario before its mutation was caught); mutation-checked 3/3.

## 4t. The lone "All" chip on Collections (2026-08-29, user-directed)

Under the Experiments filter on Collections sat a second "All" chip that
stayed lit whatever experiment was picked. It was the tag-filter row
(`#tag-filter-grid`) rendered for a workspace with no tagged run — nothing
but its "All". The row now renders only when `collection_tags` is
non-empty; `app.js` already tolerated its absence (the `htmx:afterSwap`
re-sync clears a stale tag selection when the grid is not there). Pinned by
`test_web.py::test_collections_without_any_tag_has_no_tag_row` (no tags →
no row and the Experiments row stays; one tag → the row with All + the
tag) beside the existing tagged-workspace pin.

## 4u. Three tool windows, one frame, one drag core (2026-08-29, user-directed)

**The ask.** Make the Calculator and Settings float like the Config Manual,
with the same frame; Settings could not be dragged at all; and "select the
Calculator, press Settings, the Calculator vanishes" — a bug.

**What was there.** Two copies of the same drag loop (calc.js, manual.js),
none for Settings; each tool's toggle explicitly closed the other (docs/89
called it a singleton, and the harness pinned it); and a second, quieter
path: the Calculator's outside-click closer, bound a tick after it opens,
counted the click on the Settings BUTTON as "outside".

**What changed.** `web/static/float-panel.js` is the one drag core (a
core script, before its callers): a header press plus a move under 4 px is
a click; a real drag commits the panel to fixed coordinates with the owner's
float class and `fp-floating`, follows the mouse clamped inside the
viewport, ignores the header's own buttons, ends on a lost mouseup or a
window blur; `unfloat()` puts a panel back under its anchor. calc.js and
manual.js delegate to it (their classes `calc-floating` / `manual-floating`
kept for CSS and the outside-click exemption). Settings gained a header
(title + ×, the handle), `settings-floating`, and the same rule the
Calculator has: dragged once, it stays open on an outside click. Neither
toggle touches the other window; both outside-click closers ignore the
other tool's button and window. One frame for the three: the Config
Manual's SM-blue 1.5 px edge, 10 px rounding and shadow, in ONE rule placed
AFTER the three panels' own rules (same specificity — placed earlier, the
Calculator kept its old 1 px / 6 px frame, which real Chrome showed).

**Real Chrome (PJ chip):** Calculator then Settings — both visible; a
120 × 80 px drag of the Settings header floats it (`position: fixed`,
`settings-floating`); an outside click leaves the dragged Settings open
(the never-dragged Calculator closes, as before); the three panels' computed
border colour / radius / shadow are identical. No console errors.

Pinned by `tests/test_float_panel.py` (script order, the owners delegating,
the copied loops gone, the Settings header + floating rule, the shared
frame rule and its position, neither toggle touching the other, both
closers ignoring the other tool) + `float_panel_selfcheck.cjs` (15 asserts
on the core) + the rewritten §4 of `sidebar_tools_selfcheck.cjs` (two
windows, the header drag, surviving an outside click, and — as an async
tail, since the closer binds a tick later — a click on the Settings button
never closing the Calculator; 31 asserts). Mutation-checked 3/3 (the closer
ignoring nothing, no click threshold, the frame rule placed early).

## 4v. The double frame on "Review N schema changes" (2026-08-29, user-directed)

The Environment-schema review (and the type-fix repair dialog) ride the
shared `.ch-overlay` / `.ch-card` shell and put their own `.tfx-card` inside
it; `.tfx-host` strips the shell's padding, background and shadow so one
frame shows. As a single class it lost to `.ch-card`, defined later at the
same specificity — so both frames painted: measured in real Chrome, the
inner card sat 20 px right and 16 px down of the outer and 40 px wider than
the outer's content box, the × on the outer edge. The rule is
`.ch-card.tfx-host` now (two classes beat source order); measured after, the
host and the inner card coincide exactly (240,302 · 920×296), the host
transparent and shadowless. Same lesson as §4u's frame rule: at equal
specificity, source order decides — say so with specificity when a rule
must win. Pinned by `tests/test_modal_frames.py`.

## 4w. The Json tree's ? is a hover tool, right of the others (2026-08-29, user-directed)

The Config Manual's `?` (§4h) was painted on EVERY tree row at rest (opacity
0.55) while the row's ✎ ⧉ ＋ ✕ only show on hover — the user read it as
clutter — and it sat LEFT of that group ("✎ ? ⧉ + ✕"). The order was an
artefact of construction: the `?` is appended when the row renders, but the
action group is built lazily on the first `mouseover` (`_buildRowActions`)
and appended after it. Fix: `_buildRowActions` re-appends the row's
`.tree-help` after attaching the group (moved, never duplicated), and
`.key-help-btn.tree-help` is `opacity: 0` at rest, 0.7 on row hover, 1 on
direct hover — the same reveal the other tools use (two-class rule, so it
beats the generic `.key-help-btn` 0.55 regardless of source order). F1 still
opens the manual for the focused key, so the hover-only `?` loses nothing.
Real Chrome on the PJ chip (`cdp_treehelp.js`): opacity 0 → 0.7 on hover,
the `?` the row's last child at x=532 against the group's right edge 526,
back to 0 when the mouse leaves, no console errors. Pinned by
`tests/tree_help_hover_selfcheck.cjs` (15 assertions, driven by
`test_config_manual.py`; both mutations — no re-append, visible at rest —
fail it).

## 4x. Pulses column resize: one column moves, the others hold (2026-08-29, user-directed)

A user dragged the Pulses table's WAVEFORM column narrower and watched
OWNER / CHANNEL / OPERATION widen instead. `enhanceColumnResize` (app.js,
the shared resizer the Pulses table opts into) freezes every column to px
and flips `table-layout: fixed` on the first drag, but left the table at
Pico's `width: 100%` — and under fixed layout the browser redistributes the
space a shrunk column gives up across the other columns, so the total was
being held constant and the widths traded inside it. Now, once any column is
under manual control, the table is exactly as wide as its columns:
`fitTableToColumns()` freezes any unpinned column at its laid-out width and
sets the table's width to their sum, re-derived on every drag step and on a
re-render that restores saved widths (saved widths never actually held
before, for the same reason). A double-click still clears its column and
releases the table width so that column auto-fits the pane; the next drag
re-freezes and re-pins. With nothing saved the table is untouched (still
fills the pane, reflows with it). Real Chrome on the PJ chip
(`cdp_colresize.js`, WAVEFORM handle dragged −150 px): Waveform 155 → 36 px
(the floor) and every other column byte-identical, table 1179 → 1060 px.
Pinned by `tests/col_resize_selfcheck.cjs` (16 assertions, offsetWidth
stubbed per header; the no-fit mutation fails it) + `tests/test_col_resize.py`.

## 4y. Compare Selected: figures first, the ticks stay, 2–5 all open the Diff (2026-08-29, user-directed)

Three asks on the sidebar's run comparison, one commit. **① Figures first.**
`_DIFF_TABS` is now `figures · state.json · wiring.json · node.json · data`
and the default tab is decided AFTER the sources resolve: figures when every
source is a run (runs have figures), state.json otherwise (a snapshot or the
working copy has none — an honest blank is not a landing page). `/compare`
(sidebar) and `/diff/runs` (Datasets table) both open runs on figures.
**② The ticks stay.** `/compare` answered with `HX-Redirect`, a whole-document
reload that rebuilt the sidebar and dropped every tick. It now answers with
`HX-Location {path, target: "#table-pane", swap: "innerHTML"}`
(`_pane_redirect`): htmx GETs the diff into the main pane and pushes the URL;
the sidebar DOM is never touched. The checked set is also mirrored into
`sessionStorage` (`quam_sidebar_compare_sel`) and re-applied after every
`#sidebar-tree` swap (filter, rescan, workspace add/remove) and at load, so a
re-render or an F5 keeps them too. **③ 2, 3, 4, 5 → Diff.** Every tick count
from two to five opens `/diff?a..e` (slots `_DIFF_SLOTS = "abcde"`); the
Compare hub is retired as a destination — no sidebar/Datasets/workbench link
reaches it (its routes stay for bookmarks until the code is removed
separately). The SIXTH tick is refused in the sidebar (toast; a shift range is
clamped from its far end), Compare Selected is disabled below two, and the
server refuses <2 or >5 by name (`HX-Reswap: none` + an `sm:toast` trigger —
a new document-level bridge to `showToast`) rather than truncating. The
Datasets toolbar keeps ONE button (Diff, 2–5) — its N-run comparison page is
retired with the hub. Real Chrome on the PJ chip + the 2025-06-24 archive
(`cdp_sidebar_diff.js`): tick 3 → Compare: same document, `/diff?a=run…`
pushed, figures active, tab order as specified, 3 figure columns, ticks and
count kept; 6th tick refused with the toast at 5; a filter round trip keeps
all 5; no console errors. Pinned by `tests/sidebar_compare_selfcheck.cjs`
(21; no-cap and no-restore mutations fail it), `test_sidebar_compare.py`,
rewritten `TestCompareRedirect` / `TestP4Redirects` pins, and
`test_the_diff_no_longer_links_to_it`. The N-way pane view itself is §4z.

## 4z. The pane view: N runs side by side, differences against a baseline you pick (2026-08-29, user-directed, CRITICAL)

The ask, verbatim in spirit: like VS Code — one window per run under its own
title, only the differences listed, and clicking a run's title makes it the
reference; default the first column. **Shape.** `/diff?a..e&view=panes&base=k`
renders `_diff_panes.html`: a single table whose column 0 is the leaf path and
whose columns 1..N are the panes (own header button, own left rule, rows
aligned so one scroll moves every pane — the IDE's synchronized scroll for
free). Rows = `json_diff.diff_rows_n` (one per leaf where ANY two sources
differ, path order; a leaf all N agree on is never rendered), 300 per page
with Show more. **Baseline.** The row set is baseline-independent, so the
switch is a client re-paint, and it never re-derives equality: the server
stamps every row with equality GROUPS (`_diff_row_groups` — `groups[i] ==
groups[j]` iff json_diff's own `_eq` → `differ.compare_equal` says equal,
absent = −1) and the client (`diff-panes.js`, shipped in the 'compare'
bundle) classes each cell `dp-base` / `dp-diff` / `dp-same` by comparing
integers against the baseline's. A Δ (`window.ValueDelta`, docs/76) renders
ONLY on a cell that differs from the baseline and is numeric on both sides —
an equal cell would read "0", which the highlight already says (the first
real-Chrome pass showed exactly that "0" on every equal cell; fixed on both
render paths). The initial baseline is painted server-side (`base=`), so the
page is right before any script runs; a click updates `#diff-root[data-base]`,
the picker's hidden input, and `history.replaceState`. **htmx captures a
button's path at init**, so rewriting `hx-get` after a switch did nothing (the
node.json tab came back on baseline 0 — caught in real Chrome); the request
itself is now rewritten in `htmx:configRequest` for `/diff` requests issued
from inside the workbench. **Two vs three+.** Three or more sources are ALWAYS
panes (the tree reads A → B only; the 3-way list from 2026-08-27 is retired);
two default to the tree with a third toggle, Panes, beside Tree/List. The
picker row offers one select per slot in use plus the next empty one (A, B, C
for two; D appears once C carries a source; five at most). Real Chrome on the
2025-06-24 archive: 5 runs → 5 panes, 272 differing leaves of 3,844 (state),
119 (node.json), pane view reached 353 ms after the tab press; click B →
header/column re-marked, 305 differing cells each with a Δ against B, URL
`base=1`, node.json tab keeps baseline 1; 2 runs → Tree/List/Panes toggle,
Panes = 2 panes, 28 rows. Pinned by `tests/diff_panes_selfcheck.cjs` (21;
no-rewrite, Δ-on-equal and no-same-class mutations each fail it),
`tests/test_diff_panes.py` (`_diff_row_groups` rule), and the rewritten
`tests/test_diff_three_way.py` (three sources are panes whatever view is
asked, two can ask for panes, `base=` clamps, Δ against the baseline not the
previous column). Left as is on purpose: the Versions panel's own N-way
(`/diff/versions`, docs/128) — a different surface with its own pins.

## 4aa. The workspace root row: a long path no longer runs under the × (2026-08-29, user-directed)

The sidebar root row rendered the path as a plain inline span truncated
server-side at 35 characters — narrower than that and the text spilled under
the absolutely pinned × and the chevron (user screenshot). The row now reads
**folder name** first (whole, bold, the thing you actually recognise), then
the parent path dimmed and ellipsized by CSS, the full path in the row's
title; the summary reserves a 2.9 rem right column for × + chevron. First
attempt still overlapped in real Chrome: `#sidebar details > summary
{ padding }` is an id selector and beat the class rule on SPECIFICITY (the
day's fourth cascade lesson, a different axis from §4u/§4v's source order) —
the reserve is `#sidebar details.tree-root > summary.tree-root-label` now.
Measured after (`cdp_rootrow.js`, a 220 px-wide sidebar, four roots incl.
`D:\work\Customer_Codes\CQT\CS_installations`): label right edge 140–157 px
vs × at 186 px on every row, names whole, parent dirs clipped. Pinned by
`tests/test_sidebar_root_row.py`.

## 4ab. The pane view's Keys column is a tree (2026-08-29, user feedback on §4z)

Three items on the first real use of §4z: the key text was small and grey
("the one thing that matters is the hardest to read"), the column was called
Leaf, and a flat dotted path per row lost the hierarchy the 2-way diff tree
and the Explorer show. Now `_diff_tree_rows` (routes) folds the differing
leaves into their JSON hierarchy — one `dir` row per container on the way
down (toggle, name, the count of differing keys beneath it), one `leaf` row
per differing key, depth-first, list indices ordered numerically — and only
containers that lead to a differing key exist, exactly like the pruned
2-way tree. Container rows keep an empty cell per pane so the panes stay
aligned. Collapsing is client-side (`diff-panes.js`): a row is hidden when
ANY ancestor container carries `data-collapsed` (walk `data-parent` through
a path → row map — no re-ask), Depth 0/1/2/3/All buttons collapse every
container at depth ≥ d, and a baseline switch never touches visibility
(paint and visibility are independent). Keys render in the page's text
colour at 0.95 rem (values stay 0.8 rem), the header says **Keys**, leaves
are indented by depth (`--dp-depth`). Real Chrome, 5 runs of the 2025-06-24
archive: 205 container rows + 272 keys, `qubits 272 → q1 13 → gate_fidelity
1`, collapsing `qubits.q1` hides exactly its 21 descendants and nothing
else, Depth 1 leaves the qubit rows, All restores, key font 19.95 px in
`rgb(208,213,222)`, no console errors. Pinned by `diff_panes_selfcheck.cjs`
(now 32; the no-ancestor-walk and depth-off-by-one mutations fail it),
`TestTreeRows` in `test_diff_panes.py`, and the tree rows in
`test_diff_three_way.py`.
**Same-day amendment (user):** the monospace key one size up read as "too
big" beside the values — keys now inherit the value cells' face and size
(measured 16.8 px system sans on both) and differ only in weight (leaf 500,
container 600 vs 400). Pinned by `test_keys_share_the_values_face_and_size`.

## 4ac. Review round over §4m–§4ab (seven reviewers + seven verifiers + two critics, 2026-08-30)

Method: docs/141 §4l-review, one size up. Seven reviewers took a dimension each
(the /bulk document and its server-side virtualization; Chip Status + the run
watcher; the UI cascade fixes; Compare Selected; the pane view; **the document
itself**; and the seams between all of them), each in its own worktree with its
own server and real headless Chrome. Every finding then went to a **verifier**
whose brief was to REFUTE it — reproduce independently or mark it refuted — and
who also reviewed the proposed FIX for blast radius. Two critics closed the
round: a completeness critic (what did nobody run?) and a **fix-risk critic**
(which proposed fixes are wrong, and do any two conflict?).

**Raised: 5 CRITICAL, ~28 MAJOR, ~45 MINOR. Refuted: 2. Verifiers filed 12 of
their own; the critics 5 + 25.** Everything below was re-executed by the
coordinator before a line was changed.

**The fix-risk critic earned the round.** Five of the proposed fixes were wrong
in ways that would have shipped:

* **R4-2 and R4-9 cancel each other, and R4-9 alone destroys a selection HEAD
  keeps.** One reviewer wanted `persist()` to MERGE (keep what the filter
  removed from the DOM), the other wanted `restore()` to PRUNE (drop what the
  DOM cannot show). `restore()` runs on the very filter re-render the merge
  exists to survive, so it runs first and wins: applying both nullifies the
  first, and applying the second alone turns "filter, clear the filter, ticks
  intact" — a case HEAD handles correctly and `cdp_sidebar_diff.js` verifies —
  into a total loss. Taken: the merge only. Pruning happens where a path really
  goes away (Clear, a workspace root removed), never on a swap.
* **R2-1's server half without its client half is worse than the bug.** Bounding
  the long polls makes a refused one answer instantly, and `live-wake.js` infers
  "the wait really waited" from "the request succeeded": the critic measured
  **~73 requests/second from one tab**, against a design rate of 0.04. Shipped
  as three parts that only work together.
* **R2-5's jump-guard fix silently switches §4o's feature off.** It compared
  `pane.scrollTop` against the value recorded at `note()` time — but `note()`
  runs BEFORE the rAF that performs the smooth scroll, so the position always
  differs by the time `reanchor()` runs and the guard never fires again.
* **R1-1's fix for the CRITICAL search bug is a no-op as filed.** Verified by
  applying it verbatim: `applySearch` memoises the column haystacks on the
  hidden-column set alone, so the second call reuses the ones built while
  `_virt` was still null. `_hayCache = null` is mandatory, not cosmetic.
* **R5-3's `\u0000` row key** would put a NUL into an HTML attribute, where the
  tokenizer replaces it with U+FFFD and raises a parse error.

### The five CRITICALs

**① The pane view's only paging control destroyed the comparison.**
`_diff_panes.html` built the Show-more URL with `{{ _qs | join('&amp;') }}` —
a plain Python string holding `&amp;`, which Jinja's autoescape escaped AGAIN
to `&amp;amp;`. The browser resolves that to a literal `&amp;`, which is not a
query separator, so every slot after `a=` was dropped: one press replaced a
five-pane comparison with "Pick two sources to compare." The author's own
real-Chrome pass used 5 runs of one chip — 272 rows, under the 300-row page —
so the button was never present. `join('&')`.

**② A capped N-way diff invented its "agree" count.** `json_diff.diff_rows_n`
stopped emitting rows at `ROW_CAP` and then computed
`same = len(paths) - len(rows)`, so every path it never examined was counted as
agreeing. On the real chip-vs-#1226 pair the page read "5,957 agree" where the
truth was 890, hid 5,354 differing leaves, and — at `rows=5000`, where the
Show-more button disappears — read as complete. The same two sources one click
away (Tree or List) printed the honest 890, so the page contradicted itself.
The cap now bounds what is RENDERED, never what is counted: `counts.changed` is
every differing leaf, `counts.same` every leaf that truly agrees, `counts.shown`
what came back, and the note says "showing N of M differing keys" with the
collection limit named when it bites.

**③ A remembered search left the Live-Edit grid empty, permanently.** The
search box is restored from `localStorage` before `mount()`, and the mount's
first `applySearch` runs inside `_applyColumnVisibility` — BEFORE `_virtInit`,
while the cold cells' contribution to the haystack is still behind
`if (_virt)`. So a search for a value living only in a server-cold column
matched nothing: "0 of 20", every row hidden, still there at 7.5 s, with no
escape but clearing the box. Not an F5 edge case — the verifier showed the
ordinary htmx sidebar navigation fails identically, and the pre-range tree
answers "1 of 20" on the same steps.

**④ Four SM tabs froze the whole UI for 22 seconds.** `live-wake.js` is a CORE
script, so every open tab permanently holds one `/datasets/wait` for up to 25 s;
`qsm serve` and `qsm browser` run waitress at its default `threads=4`. Four
tabs took the pool and `GET /` measured 22.8 s. §4p's "the servers all run
`threaded=True`" is true of the desktop launcher and false of the two commands
that are actually installed as console scripts. Fixed in three parts: waitress
gets 16 threads, a per-app semaphore bounds blocked waits (with a floor, so
even a stale client cannot spin), and the client backs off on `saturated`.

**⑤ Coming back to Live State Edit painted the toolbar 3,000 px off screen.**
§4q made the bars' inline `translateX` a function of `#table-pane.scrollLeft`;
docs/110's PaneState parks the bars (they are the pane's children) but not the
pane's sideways position, and docs/139's skip-restore deliberately does not
re-run the mount. So the search box, the Properties / ⚏ Qubits / ⚯ Pairs
pickers, Apply all, the chip bar and the pair divider all rendered outside the
pane — no console error, no clue — until the user happened to scroll. Worse, the
docs/113 `/` shortcut "recovered" the search box by throwing the grid 2,515 px
sideways. PaneState now round-trips `scrollLeft` (which also gives the user back
the column they were looking at, silently lost since §4q) and the grid
re-derives the bars on `paneRestored`.

### The pane view (§4z / §4ab)

**Equality CLASSES cannot express the rule they claimed to.** `_diff_row_groups`
and §4z both stated `groups[i] == groups[j]` **exactly when** json_diff's own
`_eq` says equal. `differ.compare_equal` compares numbers with a relative
tolerance, so `_eq` is not transitive and induces no partition: with
`a=1.0, b=a(1+0.9e-9), c=b(1+0.9e-9)`, `eq(a,b)` and `eq(b,c)` hold while
`eq(a,c)` does not. First-match grouping answered `[0,0,1]` for (a,b,c) and
`[0,0,0]` for the same three values as (b,a,c) — so with B as the baseline the
pane painted C as differing from a value the app's one comparison rule calls
equal, and which slot a run was dropped into changed the picture. Rows now carry
the **pairwise matrix** (`data-eq`, at most 25 characters for five sources) and
the client reads the baseline's row out of it: the docs/118 rule asked directly,
for every baseline, with no transitivity assumption. `diff_rows_n` had the
mirror bug — every side compared against the FIRST present value only, so such a
triple was called "all equal" and the row never rendered at all.

Also: a key that is a leaf on one side and a container on another gave two rows
the same `data-path`, and the client's collapse map is keyed on it — the
container lost to its own value row, its toggle hid nothing, every descendant
resolved its ancestor to a row that can never be collapsed, and the value row's
parent was itself (a self-loop escaped only at the 64-step guard). The value row
has its own key now, the map lets a container win any collision, and the walk
stops at a self-reference. A container's count was the count **within the page**
(`ds_raw` read 70 on page 1 and 182 on page 2) under a tooltip stating it as
fact — counted over the whole diff now, and the tooltip says when the row cap or
the page bounds it. Clearing slot A or B blanked a five-pane comparison to "Pick
two sources" while the pickers still showed four selected — the slots are
compacted server-side, which also makes the pane letters and the picker letters
one alphabet (they disagreed whenever a middle slot was empty, and `base=`
indexes the compacted list, so a bookmarked baseline silently meant another
run). `+0 added −0 removed` beside thousands of one-sided keys is now a
one-sided count; five identical runs no longer read "These two are identical";
the tree walk moved INSIDE the route's never-500 guard, where a unicode digit
(`isdigit()` true, `int()` refuses) would otherwise have taken the page down.

### The grid (§4m / §4n / §4q / §4s)

Beside ③: a server-cold column's value map is a SNAPSHOT taken at render, and
it is the only search haystack a cold cell has. The undo repaint and the apply
echo deliberately skip remote columns — correct for the DOM, wrong for that map:
after an undo the whole-chip search matched a value the chip no longer held and
missed the one it did. The map is repaired per cell now, for no round trip.

`/bulk/cells`' `?chip=` gate compared the DISPLAY NAME, which for a plain folder
is the basename — so a chip and its backup, or two labs' `quam_state` folders
added as separate roots, were one chip to it: the completeness critic measured
B's values hydrating a page rendered from A, 20/20 rows, into inputs whose
`data-orig` still held A's. The gate carries a path-folded token now;
`QMETA.chip` stays the display name because it is also the picker's
`localStorage` prefix.

### Pins, and the one that had been red for eight commits

`tests/test_single_scroll.py::test_the_rows_between_the_grids_stay_put_sideways`
indexed a selector list §4s deleted, so it raised `ValueError` before reaching a
single assertion — **RED on the branch since 0c72989**, contradicting the
handoff's "green except the three known failures", and found independently by
four reviewers. Its second line asserted the very `will-change: transform` §4s
removed *because it was the bug*, so repairing the string would have re-asserted
the defect. Rewritten against what §4s shipped; the mutation check is why it
matters (deleting the mount-time `_pinBarsToScroll()` used to change nothing in
this file's output).

`version_diff_selfcheck.cjs`'s intermittency was **introduced by this range**,
not inherited: §4p's popup-poll baseline now fires unconditionally 1.5 s after
load, which is about when the harness reaches a `posted.length === 0`
assertion, so an unrelated background request decided the run. The handoff and
this round's own brief both called it pre-existing. The assertion counts the
take door now and the timer is gated on the popup existing — **10/10 green**
where it was failing about half the time.

Other blind pins closed: the §4s dirty-pair guard's set-level half (both
mutations stayed green — the row stayed visible because a SECOND guard masks
it, while the pair id was still persisted, so it would vanish the moment the
edit cleared); `_diff_row_eq`'s non-transitive case; the server-side Δ gate
(removing it fabricated a "0" on 708 equal cells with every test green, because
no fixture ever rendered an equal cell); `test_sidebar_root_row.py` was blind to
the name-first ordering that IS §4aa; the §4o jump guard's positive assertion
was vacuous (`WINDOW_MS = 0` stayed green — `note` and `reanchor` landed in the
same millisecond).

### The rest, briefly

§4u's "three tool windows" fix hard-coded the pair Calculator↔Settings, so the
Config Manual — the third window the same section ships the frame and the drag
core to — still closed the other two, exactly the bug the user reported; the
tool set is named once in the core now. A floated panel was never re-clamped, so
narrowing the window stranded it off screen for the life of the page (reopening
does not help: both owners skip re-anchoring while floating, and `unfloat()` has
no production caller). §4x's `fitTableToColumns` froze every unpinned column at
whatever `offsetWidth` happened to be at render — with one saved width, a
/pulses render made in a narrow window pinned the table there for good, leaving
630 px of the pane empty; and with no layout box it froze them at 0 px (the
author's own weak spot #1, mechanism confirmed, no reachable trigger found). The
sidebar's "Show all N" swaps an INNER `<ul>`, which the tick-restore listener
missed by testing `el.id === 'sidebar-tree'` — every tick vanished while the
button still claimed them and the press then hit the server's own refusal. A
shift-range clamp always dropped the highest indices, so sweeping upward from an
older run kept "the five newest" and dropped the run the sweep started from.
Two live buttons (State History rows, the Param History panel) still opened the
Compare hub §4y calls retired — repointed at the workbench, and the two hub-era
pins that guarded them were pinning unreachable UI. A NaN off the diagonal
passed the confusion-matrix validator (`nan < -1e-9` is False and
`abs(nan-1) > 0.02` is False), producing a confident green fidelity from a
matrix that is not a distribution; and a 2×2 stored under `gef_confusion_matrix`
was presented as three-state "Readout Fidelity (GEF)" and scored against its
deliberately lower thresholds.

**Deliberately NOT changed**, on the verifiers' evidence: the Alt+click dataset
basket still opens `/datasets/compare` (it is the only path that compares 6–8
runs, and it predates this range) and the inspector's "vs prev" still 302s there
(it targets `#inspector-pane`, where `_pane_redirect`'s hardcoded `#table-pane`
would land wrongly) — §4y's "no link reaches it" is corrected to name what was
actually swept, rather than the code being changed to fit the sentence.

**Corrections to the doc's own numbers**, from the doc auditor: the pair grid is
**3,330 cells / ~1.5 MB**, not "1,530 cells / ~0.6 MB" — it does not reconcile
with the 7,810 in the same sentence, and after §4n it is the LARGEST remaining
block of `/bulk` (53% — §4ae re-measured it at both candidate revisions; the 51%
first published here and in §4n was wrong), not a small leftover. "never fewer hot columns than
before" is false in two measurable configurations (the table-size slider below
its S preset, and the `vw` hint that a full page load never sends), and the
constant the whole conservatism argument rests on was unpinned. §4n's
`bulk_virt_server_selfcheck.cjs` executes 37 asserts, not 36;
`chip_density_selfcheck.cjs` 27, not 26. "Per cell 1,140 → 404 bytes" follows
from no denominator the section states (its own totals give 1,149 → 500).
`D:\2025-06-24` holds 103 run folders, not 101. §4r is superseded by §4y and
says the opposite of what ships, in this doc and in CLAUDE.md. §4p's honest-limits
list omits the flat-root shape and the fact that `DirEntry.stat()` on Windows is
a cache read, so the documented "second tick" never fires there.

## 5. Tooling that came out of the night

`scratchpad/cdp_dsearch.js` (the diff search box end to end, §4ad),
`cdp_ui4.js` (the three tool windows + the search box in one pass) and
`cdp_corner.js` (an A/B of ONE CSS rule inside a single page load, captured at
6x — the way to show a 1-pixel geometry fix) joined the set in §4ad/§4ae.

`scratchpad/cdp_measure.js` / `cdp_act.js` / `cdp_shot.js` (+ daytime: `cdp_profile.js` function-level CPU profile, `cdp_trace.js` per-phase trace of one keystroke, `cdp_type.js` char-by-char typing with a gap + debounce override, `cdp_undo.js` trusted Ctrl+Z/Ctrl+Shift+Z through the page's own UI, `cdp_virt.js` virtualization sampler): Chrome headless with the
DevTools protocol over Node's built-in WebSocket — real long-task + trace splits and
screenshots without the browser extension (which cannot reach this machine's
localhost). Used for every number above and for four visual checks.

## Pins

`test_safe_io_raw.py`, `test_deferred_index_join.py`, `test_key_manual_probe.py`,
`test_key_manual.py` (+ `TestReviewFixes`), `test_config_manual.py` +
`config_manual_selfcheck.cjs`, `test_undo_trail.py` + `undo_trail_selfcheck.cjs` +
`tree_search_list_selfcheck.cjs`, `bulk_search_selfcheck.cjs` (class-selector hide),
`ctrlz_selfcheck.cjs` (new fallback contract), `test_bulk_markup.py` +
`bulk_markup_selfcheck.cjs` (§4m), `diff_panes_selfcheck.cjs` +
`test_diff_panes.py` + `test_diff_three_way.py::TestSearchBox` (§4ad),
`test_search_hint.py` + `search_hint_selfcheck.cjs` (§4ae).
Every new pin mutation-checked
(3/3, 6/6, 7/7, 5/5); a wrong mutation and two vacuous pins were found and rewritten.

**§4n–§4ab** (this list stopped at §4m until §4ac):
`test_bulk_virt_server.py` + `bulk_virt_server_selfcheck.cjs` (§4n),
`test_chip_status_layout.py` + `chip_density_selfcheck.cjs` (§4o),
`test_run_watch.py` + `live_wake_selfcheck.cjs` (§4p),
`test_single_scroll.py` (§4q),
`test_bulk_pairs_picker.py` + `bulk_pairs_picker_selfcheck.cjs` (§4s),
`test_web.py::test_collections_without_any_tag_has_no_tag_row` (§4t),
`test_float_panel.py` + `float_panel_selfcheck.cjs` + `sidebar_tools_selfcheck.cjs` (§4u),
`test_modal_frames.py` (§4v),
`tree_help_hover_selfcheck.cjs` (§4w),
`test_col_resize.py` + `col_resize_selfcheck.cjs` (§4x),
`test_sidebar_compare.py` + `sidebar_compare_selfcheck.cjs` (§4y),
`test_diff_panes.py` + `test_diff_three_way.py` + `diff_panes_selfcheck.cjs` (§4z/§4ab),
`test_sidebar_root_row.py` (§4aa).

**§4ac** adds, to those same files: `Test4acRegressions` and the pane-view
paging / cap / slot cases (`test_diff_three_way.py`), `TestRowEq` and the
tree-row identity + whole-diff counts (`test_diff_panes.py`),
`TestChipGateIdentity` and `TestWidthMetricsMirror` (`test_bulk_virt_server.py`),
`TestPoolSafety` and `TestSecondTick` (`test_run_watch.py`),
`Test4acGefHonesty` (`test_chip_status_layout.py`),
`test_a_restored_pane_re_derives_the_bars` + the rewritten sideways-scroll test
(`test_single_scroll.py`), the re-fit and zero-freeze cases
(`test_col_resize.py`), the name-first ordering and the parent's minimum
(`test_sidebar_root_row.py`), the tool-set and resize-clamp cases
(`test_float_panel.py`), `test_no_history_row_opens_the_hub`
(`test_diff_workbench.py`), and new blocks in `diff_panes_selfcheck.cjs` (44),
`sidebar_compare_selfcheck.cjs` (35), `bulk_virt_server_selfcheck.cjs`,
`chip_density_selfcheck.cjs`, `live_wake_selfcheck.cjs` and
`bulk_pairs_picker_selfcheck.cjs`. **Mutation-checked: 11/11 (pane view),
5/5 (§4n grid), 5/5 (§4q + PaneState), 8/8 (§4y selection), 6/6 (§4p pool),
7/7 (§4o), 10/10 (UI cascade), 6/6 (the width-metric constants), 3/3 (the
dirty-pair set guard), 1/1 (the chip gate)** — every one of them written
because the mutation it catches passed before.

---

## 4ad. The pair grid virtualizes too — and the mechanism became a module first

**2026-08-31, user-directed ("페어 그리드 서버 가상화도 진행해줘").** §4n shipped
server-side column virtualization for the qubit grid and deliberately left the
pair grid whole, recording the follow-up as *"generalize the mechanism into a
shared module first."* §4ac then measured what deferring it cost: the pair grid
is **3,330 cells / ~1.5 MB, 53% of the `/bulk` document** — after §4n had made
the qubit half small, the pair half was the largest block left.

This section does the follow-up in the order §4n named: **extract, then reuse.**

### 1. What was actually shared, and what was one grid's DOM fact

The §4n virtualization was ~470 lines living inside `bulk-edit.js`, reachable
only through the closure it was written in. Reading it against what the pair
grid needs split it cleanly in two:

* **The mechanism** — a cold set, a remote set, a value map, width freezing by
  generated stylesheet, a scroll pass that computes a look-ahead window from
  header geometry, one request per pass with in-flight dedup, adoption of
  server-cold `<td>`s, `ensureTd` hydration on demand, the failure note.
* **The grid's own facts** — which table, which row attribute (`data-qubit` vs
  `data-pair`), which element ids, which extra URL parameters, what to do when
  a batch lands (the qubit grid recomputes its header statistics; the pair grid
  has none).

`web/static/grid-virt.js` (507 lines) is the mechanism. `GridVirt.create(opts)`
returns an instance; both grids pass their own facts in. Three rules the
extraction is built on, each of which a mutation now guards:

1. **The core carries no default for a grid's DOM fact.** `rowAttr` has no
   fallback — a shared core that defaults to `'data-qubit'` silently half-works
   for the second consumer. My own new pin caught exactly that leak while the
   extraction was in progress.
2. **Per-instance state is keyed by the instance.** The scroll listener's
   "already bound" flag is `wrap['_virtScrollBound_' + styleId]`, not one shared
   property — both grids scroll inside the same `#table-pane` (§4q), so an
   element-level flag would let whichever grid mounted first suppress the
   other's listener forever.
3. **The owner keeps a live mirror, not a copy.** `bulk-edit.js` still has
   `_virt` (many call sites read it), so the core announces **every** assignment
   of its state through an `onState` callback. The first extraction nulled the
   state inside the core's own scroll listener and the owner's mirror went
   stale — caught by an existing §4n pin, which is what pins are for.

`bulk-edit.js` is 3,641 → 3,328 lines (209,073 → 190,407 bytes on disk);
`pair-edit.js` 953 → 1,051; `grid-virt.js` is 28,171 new bytes. Script total
**+14.3 KiB (+14,595 B)** raw — the honest price of the extraction. Weighed
against the document it removes it is smaller than it looks and it is paid
differently: the script is fetched once and then cached for a year, the
document is fetched on every visit. §4 gives both, gzipped, which is the
only comparison that means anything.

(The first version of this paragraph said +19 KB. That figure came from
subtracting the old sizes as git stores them, LF, from the new sizes as they
sit on disk, CRLF — two different measures of the same files. Every byte
figure in this section is now a real byte count, `len(f.read())` in binary
mode, and the document figures below are too.)

### 2. The server half

`core/bulk_virt.py` needed **no change at all**: `plan(columns, n_rows, viewport)`
already takes any column list, and a pair column dict carries the same
`default_on` / `maxlen` keys the estimate reads. That is the measured evidence
that §4n's planner really was generic — verified before writing any code:

```
pair grid: 111 columns x 30 rows = 3,330 cells
  columns without default_on: 0
  columns without maxlen   : 0
  plan(vw=None): 91 of 111 columns cold (2,730 cells)
  plan(vw=1600): 94 of 111 columns cold
  plan(vw=2560): 86 of 111 columns cold
```

What did change:

* `routes._pair_grid_cached(store, modified)` memoizes the pair grid on the same
  `_bulk_grid_key` the qubit grid uses (`mutation_seq`, change-log length,
  `dynhide`), stored at `ctx["pair_grid_cache"]` — `/bulk/cells?grid=pair` must
  not rebuild the whole pair grid per hydration request.
* `/bulk` plans the pair columns and passes `pair_cold_keys` + `pair_cold_map`
  to the template.
* `/bulk/cells` gained `?grid=qubit|pair`. **An unknown grid is a 400, not a
  fallback** — a typo that silently served qubit cells into pair rows would put
  wrong numbers on screen, which is the one failure this whole mechanism must
  never have. No `grid=` at all still means qubit (every §4n client is
  unchanged), and the response echoes `"grid"` so a client can tell.
* The pair branch renders `macros.pair_cell(cell, col, rid)` — the same macro
  `_bulkedit.html` uses, which is what makes the golden below possible.

`_bulkedit.html` marks a cold pair `<td>` exactly as §4n marks a qubit one: the
identity survives (`ck-N`, flag classes, `data-col-key`), only the contents are
withheld, plus one `#bulk-pair-cold-map` JSON value map so the whole-chip search
still sees every value.

### 3. The client half

`pair-edit.js` binds its own `GridVirt` instance with its own element ids
(`bulk-pair-virt-width-style`, `bulk-pair-virt-note`, `bulk-pair-cold-map`,
`#bulk-pair-table`), `rowAttr: 'data-pair'`, and `urlParams` returning
`&grid=pair` plus the chip token. **The two grids share no element id** — pinned,
because a shared style element would mean one grid's width freeze silently
overwriting the other's.

Four places had to learn that a pair cell may not be here yet, each the mirror
of its §4n qubit counterpart:

* `applySearch` folds the cold map into `rowHay` (a value in a cold column is
  still a value on that row).
* `sort(key)` fetches a cold column before sorting by it.
* `_editableIn(td)` hydrates a cold `<td>` before treating it as editable — Tab
  navigation lands on cold cells constantly.
* `_revertPaths` patches the cold **map** for a remote column instead of fetching
  it; an undo must not cost a round trip (§4ac / docs/122 ③).

**The mount does not read geometry.** §4i's rule is that nothing during mount may
touch `offsetLeft` / `clientWidth` / `innerWidth`, because that forces a full
table layout. The first pair binding called `onScroll(true)` inline and my own
new harness caught four geometry reads; the pass is deferred now.

### 4. Measured

Same harness, same chip (PJ_10082026, 20Q, 452 qubit columns × 111 pair columns).
**Every document figure is given twice — raw, and gzipped, which is what
actually crosses the wire.** `/bulk` is compressed whenever the client
advertises it (`routes.py`, `compresslevel=5`) and every browser does, so a
raw-byte saving on this document is not the saving a user gets.

| | before §4ad | after | Δ |
|---|---|---|---|
| `/bulk` document, raw | 2,820,554 B | 2,304,399 B | −516,155 (−18.3%) |
| `/bulk` document, **gzipped — the wire** | **171,492 B** | **114,466 B** | **−57,026 (−33.3%)** |
| pair table block, raw | 1,497,500 B (53.1%) | 867,787 B (37.7%) | −629,713 |
| pair table block, gzipped | 94,497 B | 29,983 B | −64,514 |
| pair cold columns | — | 91 of 111 (2,730 of 3,330 cells) | |
| pair cold map, as served | — | 113,440 B raw / 7,233 B gzipped | |
| script bundle on disk | 261,720 B | 276,315 B | +14,595 (+5,128 compressed) |

**The script is paid once; the document is paid every visit.** SM serves
`/static` UNcompressed under `Cache-Control: public, max-age=31536000`
(verified against the running server — `grid-virt.js` comes back with no
`Content-Encoding`), so the +14,595 B is a one-time raw cost that a second
visit does not pay, against −57,026 B of wire on every `/bulk`. And the
worst case is still a saving: a user who scrolls the pair grid all the way
across pulls all 91 cold columns back — 762,627 B raw, **30,615 B gzipped**
— which leaves that visit **−26,411 B** net; a user who never scrolls past
the first screen pays none of it.

(The first version of this table gave the document saving raw only, and set
it against the raw script cost as if the two were the same kind of number:
−516 KB against +14.6 KB, per page load. On the wire the saving is ≈ 9×
smaller than that, and the script cost is not per-load at all. The trade was
never in doubt; the magnitude was out by an order of magnitude, in the
direction that flattered the change.)

Cumulative for the `/bulk` **htmx document** across §4m → §4n → §4ad:
**8.88 MB → 2.30 MB** raw (the same three points measured as a FULL PAGE
render: 8.98 → 2.40 MB).

**What the change is actually for — measured in a real browser.** Bytes were
never the point; DOM was. Headless Chrome 152 over CDP, one session, the two
servers side by side (parent `624a07a^` materialised read-only with
`git archive`), **14 interleaved full-page navigations to `/bulk` per side**,
the first run of each dropped as the server's cold cache. Median
[p25–p75] (min–max), ms:

| | before §4ad | after |
|---|---|---|
| first byte | 359 [316–420] (271–581) | **178** [170–188] (124–345) |
| DOM interactive | 1,716 [1,496–2,000] (864–2,398) | **1,231** [1,080–1,420] (605–1,729) |
| load | 1,955 [1,703–2,278] (1,109–2,706) | **1,405** [1,224–1,633] (766–2,039) |
| longest long task | 286 [216–439] (203–756) | **217** [178–276] (138–644) |
| long-task total | 792 [656–975] (477–1,412) | **541** [497–678] (390–1,203) |
| transferred (gzip) | 192,431 B | **135,054 B** |

The spread is wide — the slowest "after" navigation is slower than the
fastest "before" one, and a single number would hide that — but every
quartile moves the same way, and the two sides were measured alternately in
one browser so a drifting machine hits both equally.

And the DOM itself, read from the loaded page:

| | before §4ad | after |
|---|---|---|
| elements in the document | 17,276 | **13,964** (−19.2%) |
| `<input>` elements | 3,999 | **1,059** (−73.5%) |
| `<td>` elements | 7,875 | 7,875 (identity kept — §3) |
| empty `<td>` after mount | 4,260 | 7,200 |

That last pair is the mechanism stated honestly: the cells stay, their
*contents* do not, and the client's own virtualization then detaches more of
them than the server left cold. Three thousand fewer elements and 2,940
fewer live form controls is what the first-byte and long-task numbers above
are made of.

(Figures re-measured 2026-08-31 against the tree as it stands — which
carries the §4ae fixes as well, so its script bundle is 285,253 B, +23,533
raw / +8,149 compressed over the parent rather than the commit's +14,595 /
+5,128. The document and timing figures are unaffected: §4ae changed no
server-side rendering.)

Three corrections live in that table, all found by re-measuring rather than by
reading. The first figures published here were `len(html_str)` — characters,
labelled bytes; on this chip the two differ by ~11 kB. The cold map was
measured by re-serialising the Python object with `json.dumps`' default
separators and `ensure_ascii=True`, while the template emits compact,
non-escaped JSON — 122,095 claimed against 113,440 shipped, an 7.8% overstate
in a table whose whole point is bytes on the wire. And the cumulative line
compared §4m's FULL-PAGE 8.98 MB against §4ad's htmx FRAGMENT, which is not a
comparison; both forms are now given.

**A contradiction with §4n, resolved:** §4n's own paragraph said the pair grid
was "51% of the remaining document" while this section said 53%. Measured at
both candidate revisions (`5d7bbef` and `624a07a^`), the pair block is
**53.04%** of the document. §4n was the wrong one; it is corrected in place, in
both the §4n paragraph and §4ac's restatement of it.

**Three goldens, all at the cell-token level on the real chip:**

* Pair cold-render + hydration vs. the whole render: **3,330 cells, 0 differences.**
* Qubit cells across the change: **4,480 cells, 0 differences** (§4ad touches
  nothing on the qubit side).
* Every pair cell that differs from the pre-§4ad render (2,730 of them) is
  **exactly an empty cold `<td>`** — open tag carrying `bulk-td-cold`, close tag,
  no content. Zero wrong values.

**Real Chrome** (headless, CDP, the same 20Q chip): a search for a value that
lives only in a cold pair column finds its row; a hydrated pair cell arms and
disarms its row's Apply exactly like a hot one; a full-width scroll sweep ends at
570 cold / 2,760 hot with 11 `/bulk/cells` calls and 0 failures; no console
errors.

### 5. A 400 was retried forever

Chasing a `400 no known column named` seen **once** in real Chrome — and **not
reproduced** in three later runs including a cold server and a full-width sweep,
so it is recorded here as an open observation, not a diagnosis — established
something that does not depend on reproducing it:

`fetchCells`' catch kept a failed batch in the cold set so the next scroll pass
would retry. For a network error that is right; the next attempt may well
succeed. For a **400** it is a loop that cannot end, because the server's answer
cannot change without a new page — a column it does not know it will not know a
second later. Every scroll pass re-requested it, forever.

A 400 now retires those columns from the cold and remote sets (their values stay
in the map, so the whole-chip search still finds them) and says, once, what a
reload would fix. A network error or a 409 still keeps them cold, because those
answers **can** change.

### 6. Pins

* `tests/pair_virt_server_selfcheck.cjs` — 29 executed asserts driving the real shipped
  `grid-virt.js` + `pair-edit.js` under jsdom: adoption, no geometry read at
  mount, its own style/note/map elements, cold values in the search haystack,
  one request per pass, in-flight dedup, server markup adopted verbatim, header
  stats untouched, sort-then-fetch, undo repairing the map with no round trip,
  the failure note, Tab hydration.
* `tests/test_bulk_virt_server.py` — `TestGridVirtBinding` (the core names no
  grid's DOM fact; the qubit binding names its own; the two grids share no
  element id; the scroll flag is per instance) and `TestPairGridVirt` (the
  planner reads pair columns; cold pair `<td>`s are empty and mapped; no shared
  map; the pair macro; an unknown `grid=` is 400; no `grid=` still means qubit; a
  pair column key is unreachable from the qubit grid; a small chip is untouched;
  memoized and invalidated). `TestWidthMetricsMirror` follows the width metrics
  into `grid-virt.js`.

**Mutation-checked — and the figure first published here, "18 of 19 caught",
was not evidence of anything.** The sweep ran ONE trial per mutation against a
pytest set that included `test_bulk_virt_server_selfcheck`, which this same
commit made fail about one run in four (§4ae B1). So every sweep line that read
`pytest[1 failed, 116 passed]` is indistinguishable from that flake firing, and
at least one of them WAS the flake.

Re-run on a copy with the flaky test deselected, the honest figure is **9 of 11
re-verifiable mutations caught, and two caught by nothing**:

- **Serving the pair branch through the QUBIT macro is caught by no test at
  all** — the very failure the commit message calls "the one failure this
  mechanism must never have". `test_the_route_serves_the_pair_grid_through_the_pair_macro`
  asserts only that `"bulk-cell"` appears in the joined markup, a string the
  qubit macro emits too. On the real 20Q chip the mutation makes **1,660 of
  2,910 hydrated pair cells differ**; three reviewers reproduced it
  independently, one of them through the sharper form: `pair_cell`'s `row_id` is
  load-bearing on a `kind == 'list'` cell
  (`onclick="return BulkPairEdit.openPair('{{ row_id }}')"`), and a CONSTANT
  `rid` is green everywhere while putting seven wrong pair targets on the page.
- **Removing the `onState(v)` announcement in the 400-retire branch is caught by
  nothing.** (The other four announcement sites are pinned.)

Both are fixed in §4ae. The nine that are genuinely caught: pair never planned,
no map emitted, `grid=` ignored, grid not memoized, cold `<td>` still rendered,
shared map id, undo leaves a stale map, pair uses the qubit style id, pair uses
the qubit map.

The lesson is not about these two pins. It is that **a mutation sweep run
against a flaky suite measures the flake, not the pins** — a single-trial
"1 failed" is a coin toss reported as a verdict. A sweep must either exclude
the known-flaky tests or repeat each trial until the answer is stable.

The nineteenth — make `pair-edit.js` pass `rowAttr: 'data-qubit'` — **stayed
green, and should have.** A real pair row is
`<tr data-qubit="{{ row.id }}" data-pair="{{ row.id }}">`
(`_bulkedit.html:266`): both attributes carry the same value on every row, so
the two selectors are the same string and the mutation changes nothing
observable. A pin cannot fail on a mutation that does not mutate anything, and
writing one that did would mean pinning a coincidence.

The rule that mutation was *meant* to test is a **design** rule — a shared core
must not name one consumer's DOM fact — so it is guarded by a design pin
instead: reintroducing `var rowAttr = opts.rowAttr || 'data-qubit';` in
`grid-virt.js` fails
`TestGridVirtBinding::test_the_core_knows_nothing_about_qubits_or_pairs`,
verified by mutation. That is the honest shape of it: the *behaviour* is
identical here by coincidence of the markup, and only the *design* is
enforceable — which is exactly why the default was removed rather than
corrected. A third grid whose rows carry only their own attribute would have
been silently half-broken by it.

A fixture lesson worth recording, because it cost two rounds: the planner's own
floors mean a small fixture proves nothing. 40 columns × 12 rows is 480 cells,
under `MIN_CELLS` 600 — nothing goes cold and every assertion passes vacuously.
40 × 30 gives 26 cold × 30 = 780, under `MIN_COLD` 800 — same. The fixture is
30 pairs × 14 macros.

### 7. A harness lesson (mine, not the product's)

The wiring script that added `grid-virt.js` to the harnesses that load
`bulk-edit.js` truncated a two-line `const BULK_JS = fs.readFileSync(` in four
files, leaving them syntactically dead. **The loop I was checking them with used
`tail -1` to spot failures, and a hard Node crash's last line is
`Node.js v24.18.0` — so two of the four read as green.** The canonical loop in
CLAUDE.md uses the **exit code**; this is why. All four are repaired and 95/95
harnesses are green over two consecutive full runs.

---

## 4ae. The review round over §4ad — and what it cost the section that invited it

**2026-08-31, user-directed.** The same method as §4ac: several reviewers, each
claim actually re-executed, then a verifier per dimension whose brief is to
REFUTE, then a completeness critic and a **fix-risk critic**. Nine agents over
one commit (`624a07a`, 20 files) — a much smaller target than §4ac's eighteen,
and it returned proportionally more, because a small commit gets read closely.

**Raised: 6 CRITICAL / 19 MAJOR / 13 MINOR.** Two conflicts between reviewers,
both adjudicated by measurement. **Five proposed fixes were wrong** — the same
count as §4ac, found by the same brief. And the round's sharpest finding was
about §4ad's own verification: the mutation figure it published was not
evidence of anything.

### 1. The dimensions, and what each returned

Six reviewers: the shared core (`grid-virt.js`) and whether extracting it
changed the QUBIT grid; the pair client's call sites; the server half; **the
pins themselves** (46 mutations); every claimed number; and the seams with
§4q / PaneState / bundles / undo / auto-apply / the chip gate / the Pairs
picker / Tab focus / two windows. Then a fix-risk critic over the proposed
fixes, and a completeness critic over what nobody had run.

Three of the four behaviour CRITICALs were found **independently by two
reviewers**, each with its own harness, each measured on the real 20Q chip.
That is what the verifier stage exists to produce, and it arrived for free.

### 2. Four CRITICALs, all the same shape

Every one is *the pair grid lacking a guard the qubit grid has had since §4n*.
§4ad's own §1 says the extraction's whole point was that the mechanism became
shared — but the mechanism moved and the **call sites** did not.

**① `sort()` on a cold column is an unbounded request loop.**
`pair-edit.js` re-entered `sort(key)` unconditionally when the hydration
promise settled. `fetchCells` always resolves, and a network error or a 409
deliberately leaves the column cold, so the chain never ends. Real Chrome, one
header click: **401 requests in 4 s** (the reviewer's own cap, not an end).
jsdom, uncapped: no output and **148 s of CPU** — a frozen tab, not a busy one.
The qubit twin has had `if (!(_virt && _virt.remote.has(key))) sort(key)` since
§4n.

**② A search or a column reveal never hydrates.**
`applySearch` ends without the qubit grid's `if (_virt) _virtOnScroll();`
(whose own comment says "hydrate the surviving cold columns that the narrowed
grid puts ON SCREEN"). Real chip: open `/bulk` with a remembered search, clear
the box, and **110 of 111 pair columns are visible-but-blank** until you happen
to scroll. Five realistic queries, all blank, zero fetches. Also reached by the
Properties menu, the docs/85 "N hidden columns match — Show" chip, Show all and
Reset.

**③ An undo into a client-detached column leaves the pre-undo value, marked
clean.** `_revertPaths` patched the cold map for a `remote` column only. A
client-detached column's stashed fragment holds the old value AND the old
`data-orig`, so the cell reads clean; the path is reported `missing`, not
`uncovered`, and only `uncovered` schedules a resync. Nothing ever repairs it.
**Severity corrected by the fix-risk critic**: the first report said
client-detached is "the norm — 92 of 111", and the measurement is **1 column of
111** at the default table size (0 at the smallest, 6 at the narrowest
viewport). The kind of defect — a wrong number on screen, unmarked — is why it
is still fixed.

**④ 🕘 Column History is dead on 92 of 111 pair columns** (and 198 of 224 qubit
ones). `ColumnHistory.open` builds its path map from `td[...] .bulk-cell`; a
cold cell has no input, so the map is empty and the button — rendered on every
header — answers with a toast. A §4n gap that §4ad widened to 82% of a grid,
and that neither section named.

### 3. Two CRITICALs about verification, and one of them is about §4ad's own

**⑤ The extraction made a shipped pin ~25% flaky, and the cause is a bug that
was never there.** The parent bound `wrap.addEventListener('scroll',
_virtOnScroll, …)` — so the DOM passed the **Event object** as the function's
`immediate` argument, which is truthy, and every pass ran **synchronously**.
The `requestAnimationFrame` throttle sitting right beside it was dead code for
the whole of §4n. §4ad wrapped the listener (`function () { onScroll(); }`),
`immediate` became `undefined`, and the throttle came alive. Hydration settle
moved from a **79 ms median to 106 ms**, past `bulk_virt_server_selfcheck.cjs`'s
`await tick(90)`.

Four reviewers measured the failure rate independently (8/30, 2-3/25, 15/75,
7/10) against a parent that is 0/67. The new behaviour is the better one — a
forced layout inside a scroll handler on a 452-column grid is exactly what §4i
removed — but it was unintended, unstated, and it left a pin racing a clock.

**⑥ §4ad's "18 of 19 mutations caught" was not evidence.** The sweep ran ONE
trial per mutation against a pytest set containing the test ⑤ had just made
flaky. Every line that read `pytest[1 failed, 116 passed]` is a coin toss
reported as a verdict. Re-run with that test deselected, on a copy:

- **Serving the pair branch through the QUBIT macro is caught by nothing** —
  the very failure §4ad's commit message calls "the one failure this mechanism
  must never have". `test_the_route_serves_the_pair_grid_through_the_pair_macro`
  asserts only that `"bulk-cell"` appears in the joined markup, a string the
  qubit macro emits too. On the real chip the mutation makes **1,660 of 2,910**
  hydrated pair cells differ. The pins reviewer found the sharper form: a
  `kind == 'list'` pair cell carries
  `onclick="return BulkPairEdit.openPair('{{ row_id }}')"`, so a CONSTANT `rid`
  is green everywhere while putting seven wrong pair targets on the page.
- **Removing the `onState(v)` in the 400-retire branch is caught by nothing.**

**The honest figure is 9 of 11 re-verifiable, not 18 of 19.** A mutation sweep
run against a flaky suite measures the flake.

### 4. The two conflicts, adjudicated by measurement

**Does a partially-unknown batch retry forever?** The route 400s only when
*every* asked column is unknown; a mixed batch is a 200 carrying
`unknown: [...]`, which no client read. The server reviewer said forever; the
seams reviewer measured it self-healing in two requests. Both critics then
measured the real client, and the answer is *both, depending on the gesture*:

| gesture | requests | retired? | note |
|---|---|---|---|
| parked at one position | 2 | yes | present |
| coarse sweep (scrollbar drag) | 7, all 200 | not during | absent |
| fine sweep (wheel) | 25 | at #6 | **erased by #7** |

Convergence is guaranteed — a column never re-enters `cold` once hydrated — so
"forever" is refuted. What is real is bounded waste and, worse, **silence**:
during a sweep the column is blank with no explanation, and the note that does
fire is wiped by the next success. The more serious half of that finding turned
out to be a different finding.

**Reachability is not theoretical.** On the real chip **57 of the 111 cold pair
columns carry exactly one non-null value**, and `pair_columns` drops a column
the moment its last value goes null — while `type_policy` makes `null` always
writable. One ordinary cell edit removes a whole column server-side while the
page that rendered it cold is still on screen.

**Is `rowAttr` a pin gap?** No — §4ad said it was a no-op and two reviewers
re-derived that independently. A real pair row is
`<tr data-qubit="{{ row.id }}" data-pair="{{ row.id }}">`, both attributes
identical on all 30 rows of the real render, so the mutation changes nothing.
The design rule it was meant to test is guarded by a design pin.

### 5. Five wrong fixes, caught before they shipped

The fix-risk critic's brief is to read the proposed fixes against each other,
not to find new bugs. It paid for itself again:

- **The `unknown`-retirement fix, applied literally, is a measured partial
  no-op**: it bumps `failed`, and the very next statement in the same callback
  resets `failed` to 0 and calls `note('')`. Note empty in all four scenarios —
  the user left strictly worse off than before.
- **The generation-gate fix** (a structural hash of row ids + column keys) has
  the same false-positive its own author rejected for `seq`: the pair grid's
  columns are derived from real leaves, so **creating** a leaf adds a column,
  the hash mismatches, and every later hydration 409s until reload — punishing a
  benign act. The shipped fix is one line instead (`if (html == null) return;`).
- **Loading `grid-virt.js` into five more harnesses is a measured no-op**:
  those fixtures hold 2–9 tds against a `MIN_CELLS` floor of 600, so `init()`
  bails three orders of magnitude early. Zero coverage gained.
- **The "tighter" apply-echo fix is worse than the broad one**: `byPath` is
  single-valued and last-writer-wins, and **264 real pair paths are claimed by
  more than one column** (every `coupler__coupler_operations_cz_*` aliased with
  its `gate_cz_*__macros_*` twin), so a per-path fix skips the detached twin
  exactly when the twins are what must agree.
- **One severity was overstated by two orders of magnitude** (③ above).

### 6. What the completeness critic found that nobody had run

Seven modalities, none of them covered by any reviewer:

- **The headline byte numbers are uncompressed.** `/bulk` is gzipped to every
  browser: **171,492 → 114,466 B on the wire (−57 KB)**, script **+5,128 B
  gzipped**, one-time and cached. §4ad's "+19 KB against −511 KB per page load"
  is right in direction and **~9× off in magnitude**. And §4ad reports no load
  timing at all, unlike §4n — so the metric that overstates was the only one
  given.
- **The failure note is off screen at the moment it fires.** §4q moves the
  toolbar rows by `translateX(scrollLeft)`, and that code only ever runs from
  the scroller's listener — which the failure path never reaches. Measured at
  `scrollLeft = 23,697`: every other bar transformed, the note at **−23,410 px**.
  Invisible exactly when it fires, because the user had scrolled right and that
  is *why* hydration ran.
- **Retirement keyed on one status where its principle covers a class.**
  Offline: **118 requests/second**. A 304 from an intermediary: **72.7/s**. A
  **409 was retried forever while its own note said "reload the page"** —
  message and behaviour disagreeing. And there is no `AbortController`, against
  a rule docs/37 §4.1 established and five other call sites follow; a hung
  request leaves those columns blank forever, because the in-flight dedup that
  correctly suppresses a duplicate is what makes a never-settling request
  permanent.
- **`app.config["contexts"]` is never popped** — three assignments, zero
  removals — so the LRU frees nothing: 13 contexts, 65.8 MB, against a
  documented ~40 MB budget. §4ad adds 1.69 MB per context on top.
- **`grid-virt.js` is a silent single point of failure.** Blocked: **6,690 of
  7,810 cells permanently blank, no console error, no note, no recovery.**
  Before the extraction the same code lived in `bulk-edit.js`, where losing it
  broke the grid visibly and entirely.
- **`pxPerChar` can never see the real root font.** It reads
  `documentElement.style.fontSize`, which nothing writes; the real root is
  Pico's breakpoint ladder (21 px at ≥1536 px — exactly the case the function's
  comment says it exists for). Measured −14.3% at the default and −24.4% at the
  "small" table size, and on a full sweep **206 of 289 cold columns grew on
  hydration**, pane width +9.1%. That is the layout churn the freeze exists to
  remove.
- **Accessibility has zero pins anywhere in this project**, and §4ad raised
  cells that read as blank to assistive tech by 69%: **7,200 of 7,810 (92%)**
  are `role=cell name=""` under fully-named column headers.
- **`bulk_cells` logs nothing**, so §4ad's own unreproduced 400 was
  structurally undiagnosable — and the next one will be too.

**And a CRITICAL in a neighbour**: PaneState's `_park` moves every child out of
the pane **before** reading `scrollTop`/`scrollLeft`, and an emptied element
reports 0,0. So every keep-route restore lands at the top-left, and §4ac's
`scrollX` addition — added at that same already-broken read — is a **measured
no-op**.

### 7. What the round REFUTED, which is worth as much

Measured and found fine: printing (`/bulk` never printed its value columns —
2 of 335 fit the page, and Chrome does not shrink-to-fit); every export path is
model-derived, not DOM-derived, so no export can ship a blank cell; **Ctrl+F
loses nothing to §4ad** (values live in `<input value>`, which browser find
never saw — the real loss was §4n's, and is stated nowhere); copy/paste (no
copy handler exists; native selection yields zero values, hot or cold, and the
feared wrong-cell-write does not exist); a wide monitor does not defer a
megabyte (3840 px, no `vw` hint: 2 requests, 7.6 KB gzipped); a pair-heavy chip
does NOT silently disable virtualization (`len(pair_rows)` is passed correctly
— 3,330 cold cells where the hypothesis predicted 222); degenerate chips (zero
pairs, one pair, 2×302, 300×5) all behave; twelve hostile pair ids round-trip.

And the central safety property holds structurally: **pair∩qubit column-key
intersection = 0 and row-id overlap = 0**, so a mis-routed response can only
blank a cell, never put a wrong number on screen.

### 8. The fixes, in the order the fix-risk critic set

Nothing could be trusted until the harness was, so that went first.

1. **Harness integrity.** `bulk_virt_server_selfcheck.cjs`'s six post-scroll
   waits became **settle conditions** rather than a wall clock — the pin
   measures what happened, not how fast, with a 600 ms deadline against a 60 ms
   mock so a genuine slowdown still fails. **3/10 → 35/35 green.** And the pair
   harness's in-flight-dedup assert waited `tick(5)` while `onScroll` schedules
   through a ~16 ms rAF, so it passed with the dedup deleted outright:
   `tick(40)`, mutation-verified red.
2. **Every number corrected** (§9 below), and the mutation claim replaced with
   the honest one and the reason it was wrong.
3. **A1 + A4.** The sort re-enters only if the column arrived. Column History
   hydrates a cold column once and retries — on **both** grids, with the
   one-shot flag set *before* the call (a double click would otherwise start
   two) and stored as an expando on the button rather than in module scope (a
   module-scoped set survives a re-render, which is exactly when the column may
   have become hot on its own).
4. **The retirement chain, as one commit** — C1, C3, C4 and the 409 are one
   mechanism and the critic ruled they ship together or not at all. A `v.dead`
   set; the instance stays alive while anything is in it (so a retired column's
   value stays in the whole-chip search); a success clears a *retryable*
   failure and never a retirement; the 200's `unknown` list is read and retires
   those keys — **keyed on `unknown`, never on a missing `cells[k]`, because
   absence is also what a legitimately empty answer looks like**; a 409 retires
   like a 400; and the note is pinned to `scrollLeft` when it is written.
5. **A row the answer omits stays cold** instead of being blanked and un-marked.
6. **A2, A3, C9-broad**, and a cold guard on the pair grid's `_recomputeStats`
   — the identical defect §4l-review fixed on the qubit grid, which the pair
   grid never got.

### 9. The numbers, corrected

Every one re-measured by two parties independently.

| claimed in §4ad | true |
|---|---|
| `/bulk` 2,809,432 → 2,297,984 **B** | those are **characters**; bytes are 2,820,554 → 2,304,399 (−18.3%) |
| pair cold map 122,095 B | **113,440 B** as served (the claim re-serialised the object with different JSON separators) |
| script total +19 KB | **+14,595 B (+14.3 KiB)** — the claim subtracted git's LF sizes from disk's CRLF sizes |
| `pair-edit.js` 953 → 1,049 | 953 → **1,051** |
| cumulative 8.98 MB → 2.30 MB | mixes a full page with an htmx fragment: **8.88 → 2.30** (fragment) or 8.98 → 2.40 (full page) |
| §4n: the pair grid is 51% of the document | **53.04%**, measured at both candidate revisions; §4n was wrong and is corrected in place |
| mutation-checked 18 of 19 | **9 of 11**, and two mutations are caught by nothing |

Confirmed unchanged: 91 of 111 columns cold, 2,730 of 3,330 cells, every `vw`
planning figure, `grid-virt.js` 507 lines / 28,171 B, `core/bulk_virt.py`
untouched, and the qubit table block **MD5-identical across the commit** —
stronger evidence than the "0 diffs" §4ad claimed. The unreproduced-400
passage is honestly hedged in all four places it appears.

### 10. The follow-up round: seven of the eight open items, closed

§4ae's first pass fixed the behaviour CRITICALs and left eight things
recorded-not-dropped. Eight agents then took one each, on their own copies,
each required to return a patch, a pin, and a **mutation that fails without
it**. Seven came back `PATCH` with the mutation verified; the eighth ran out of
session before its sweep finished, and is the one thing still open.

**The silent failures now speak (B-7 / B-9 / B-10).** Blocking `grid-virt.js`
left 6,690 of 7,810 cells blank with no console error and no note — the
extraction's own cost, since before it the same code lived in `bulk-edit.js`
where losing it broke the grid visibly. Both grids now detect a missing core
and say so through one shared `window.GridVirtMissingNote`. `bulk_cells` logs
its `unknown` columns through the house idiom (`logger.warning("...%s", ...)`),
which is what §4ad's own unreproduced 400 needed and did not have. And
`.bulk-td-cold` finally has a rule, so *loading*, *retired* and *genuinely
absent* stop being one appearance.

**PaneState's park order (B-6)** — the CRITICAL in a neighbour. `_park` moved
every child out **before** reading `scrollTop`/`scrollLeft`, and an emptied
element reports 0,0; so every keep-route restore landed at the top-left and
§4ac's `scrollX` addition was a measured no-op. Reordered and pinned.

**`pxPerChar` sees the real root font (B-8).** It read
`documentElement.style.fontSize`, which nothing writes; the true root is Pico's
breakpoint ladder — 21 px at ≥1536 px, exactly the case the function's own
comment cited. The memo is primed once at script evaluation, so the
no-geometry-at-mount rule (§4i) still holds, and the harness's own
`__geomReads` counter is what proves it.

**The context leak (B-5).** `app.config["contexts"]` was assigned in three
places and popped in none, so the LRU freed nothing: 13 contexts, 65.8 MB
against a documented ~40 MB budget, of which §4ad's pair memo was 1.69 MB per
chip. Fixed with the dirty-context invariant (`test_state_coherence.py`'s
`TestEvictionNeverLosesEdits`) kept intact.

**Accessibility (B-1)** — the project's first a11y pins, on a page where 7,200
of 7,810 cells read as `role=cell name=""`. The live region is created empty at
mount so its message can actually be announced (content present when a live
region enters the DOM is not).

**The three pins that were missing (C5 / C12 / C13).** The C5 work found what
the fix-risk critic predicted and one better: rendering the same 3,000 cold
pair cells through both macros differs on 1,660 — **1,653 `missing` + 7 `list`,
and ZERO scalars**. A scalar-only fixture measures no difference at all, so
*both* proposed pins would have been vacuous on the fixture they were written
against. Hence `_mixed_chip`, which hangs an `extras` band (it sorts last, so
it lands cold) carrying one column of every kind `pair_cell` branches on. Both
pins ship, because they catch different things: the byte-identity golden reds
on the macro swap, but a `row_id` wrong in BOTH renders reds only the absolute
`openPair` identity assert. C12 gives the pair harness the pytest driver it
never had (`pytest tests/` was running none of its 39 asserts). C13 pins that
`window.__bulkChipKey` publishes the chip TOKEN — a second publication that
only `pair-edit.js` reads, so the existing chip-gate test never touched it and
changing it to the display name was green everywhere.

**Corrected numbers, again (B-2).** §4ad's byte figures were uncompressed:
the wire truth is **171,492 → 114,466 B (−33.3%)** with **+5,128 B of gzipped
script**, one-time and cached. The §4ad table is now labelled for what it
measures, and the load timing §4ad omitted is recorded with its spread rather
than as a single flattering number.

**Still open at the time of writing: the faithful pair fixture.** The agent
rebuilding it hit its session limit mid-sweep. (§4af below does it, and the
rebuild immediately found two dead-code defects in §4ae's own fixes plus one
vacuous pin of its own — which is the argument for rebuilding it, made by the
rebuild.) The rest of this paragraph describes the state §4af inherited. It remains the reason most of §4ae's own
first-draft pins stayed green under mutation — static header geometry, zero
client-detached columns, one cold-td class shape, `search-query.js` not loaded.
`_mixed_chip` closes the *server-side* half of that gap (all four cell kinds
now render cold in a pytest fixture); the jsdom half is untouched. A first
attempt was reverted for a real reason worth recording: once a search hydrates
the column it narrows onto, the fixture's fetch mock has to render from a
working copy the way the server does, or the test asserts a race the real
server cannot lose.

Also still deferred, deliberately: one Tab press firing one request per cold
column (§4n's own pattern, capped by in-flight dedup, not a §4ad regression).

---

## 4af. The fixture was the finding

**2026-08-31, user-directed** ("나머지 진행해"). §4ae's own closing section left
one thing open, and it was the structural one: **11 of its 16 mutations stayed
green**, not because the pins were badly worded but because
`tests/pair_virt_server_selfcheck.cjs`'s fixture could not reach the states the
fixes were about. This section rebuilds it and measures what that buys.

### 1. What the fixture could not express

Measured against the real PJ_10082026 render:

| | real page | fixture |
|---|---|---|
| CLIENT-DETACHED ("local") cold columns | always present | **zero** |
| `bulk-col-hidden` column | reachable | none |
| cold-td class shapes | 5 (`bulk-td-ro` ×1700, `bulk-td-pointer` ×331, `bulk-col-group-start` ×150) | 1 |
| cell kinds | 4 (`missing` 1653 / scalar 1152 / runtime 98 / list 7) | scalar only |
| the fetch mock | — | always answers with the value it was born with |

The first line is the one that mattered. `hydrateLocal`, the fragment
stash/restore and the local `byPath` are the three places §4ae's A3 and C9 fixes
live, and **none of them ran in this harness even once.**

### 2. Why the first attempt failed, and what it taught

§4ae tried to make the header geometry answer to the hidden state — a hidden
`th` is `display:none`, so it has no width and every column right of it moves
left. That is faithful, and it broke two existing undo asserts **for a real
reason**: once a search narrows onto a column, §4ae's own A2 fix hydrates it, so
the mock has to render from a working copy the way the server does or the test
asserts a race the real server cannot lose. The attempt was reverted.

This round takes the shorter road. `init()` calls a hidden column cold
(`thHidden(h) → cold.add(k)`) and a server cold map bypasses the `MIN_CELLS` /
`MIN_COLD` floors entirely, so **one hidden column reaches the local path with
no geometry change at all.** Geometry stays static; the working copy is modelled
anyway, because the C2 pin needs it.

**The hidden set is STORAGE, not markup.** Writing `bulk-col-hidden` into the
fixture's html achieved nothing: `_applyColumnVisibility` recomputes that class
from `quam_bulk_hidden_cols_pair_v2` on every pass and erased it at mount. That
cost a probe to find, and it is the kind of thing a fixture built from markup
alone will always get wrong.

### 3. What it caught immediately

**Two dead-code defects in §4ae's own fixes, and one vacuous pin of this
section's own.**

**`grid-virt.js`'s note-pinning fix had never run.** §4ae B-3 pinned the failure
note to `scrollLeft` so it would not be created off screen — and it called
`scroller(t)`, which is not a binding; the function is `scrollerOf`. The
`try/catch` around it swallowed the `ReferenceError`, so the fix was dead from
the moment it was written, shipped that way in `eb29645`, and no pin could
notice because no fixture could show the note's position. The rebuilt one can.

That prompted an audit of the same class across the whole committed module —
every identifier CALLED inside a `try/catch` that the file does not define. It
returned two real candidates: `scroller` (the above) and `getComputedStyle`,
which the docs/123–125 standing rule flags as exactly the shape that dies in a
Node realm. **The second one is fine, and it was proved rather than assumed**: a
probe loading the committed blob through `new win.Function(...)` — which
compiles in the jsdom *window* realm — reports `typeof getComputedStyle ===
"function"` and `pxPerChar() === 9.1264`, the computed path, not the ladder
fallback. One suspect confirmed, one cleared, neither guessed.

**The pair grid's stats guard was defined and never called.** `_skipStat` sat in
`_recomputeStats` as a function expression with no call site — so a cold
column's server-rendered min/max were still being wiped, which is the defect
§4l-review had already fixed once on the qubit grid. The mutation sweep is what
found it, by reporting its anchor MISSING rather than by any assert failing.

**And this section's own first STATS pin was vacuous.** It seeded the stat on
the *hidden* column, and `_recomputeStats` blanks a hidden column and returns
**before** it reaches the cold guard — so the pin passed with the guard deleted.
A server-cold column is cold and not hidden, which is the state the guard is
actually for; the pin uses one now and reds on the mutation.

### 4. Measured

The same 16 mutations, before and after the rebuild:

| | caught |
|---|---|
| §4ae's fixture | **5 of 16** |
| rebuilt | **10 of 16** |

Newly caught, all five on the client-detached path the harness could not reach:
the sort re-entry loop (A1), an undo into a local cold column (A3), the apply
echo's stale twin (C9), a row the answer omits (C2), and the note's scroll
pinning (B3) — plus the stats guard, whose pin is mutation-verified separately
after the vacuous first draft was replaced.

Asserts: **29 → 39 → 53.**

### 5. What is still not caught, and why

Honestly enumerated rather than rounded away:

- **`C3` (the instance stays alive while columns are dead) and `C4` (a success
  does not erase the retirement note)** need a 400 *and* a later success *and* a
  search, in one world. The fixture can now produce all three; the assert
  chaining them is not written.
- **`A4` (Column History hydrate-and-retry) ×2** lives in `app.js`, which this
  harness does not load. It belongs in an app.js harness, not here.
- **The qubit-side stats guard's `dead` half** needs the qubit harness to reach a
  retired column, which is the same gap this section closed on the pair side.
- **`B1`** (the settle-vs-clock mutation) went from caught to green, because §4ae
  B-8 primed the root-font memo at script evaluation and the harness got fast
  enough that `tick(90)` suffices again at that one site. The other five settle
  sites still protect; the mutation simply stopped expressing the flake.

### 6. The lesson worth keeping

Three defects this round, and **none of them was found by a pin failing.** One
came from an audit for a syntactic shape, one from a mutation sweep reporting a
missing anchor, one from a mutation on a pin that had just been written. A green
suite says nothing about the states a fixture cannot enter — and the cheapest
way to find those is to mutate the code the fixture is supposed to be guarding
and watch what does not move.

---

> **Numbering note (merge, 2026-08-31):** two concurrent sessions both
> allocated §4ad–§4af. The pair-grid virtualization line (above) keeps
> those numbers — it carries ~106 in-code references against this line's 23.
> The diff-search session's §4ad/§4ae/§4af are §4ai/§4aj/§4ak here (its
> §4ag/§4ah were never in conflict and keep their numbers). That branch's
> COMMIT MESSAGES still use the old spellings for those three; every
> in-tree reference is remapped.

## 4ag. A QDAC-II has four trigger sockets, so a chip cannot have twelve (2026-08-31, user-directed)

> "qdac2는 external trigger를 총 4개만 받아 … 즉, 우리는 digital port 4개만 쓰고,
> 각각의 digital port에 중복해서 qubit을 할당해야해. qdac이 1대만 있거든."

`_allocate_qdac_triggers` gave every QDAC-biased qubit its OWN dedicated digital
output — a docs/119 decision recorded in its own docstring as "simpler, and
correct per the 'no port sharing' UI decision". It is not correct: a QDAC-II has
exactly four external trigger inputs, and an ext input is a physical socket, so
every qubit armed on the same ext is BY DEFINITION on the same cable. Measured on
the 17Q reproduction: **12 biased qubits produced 12 digital output ports** for
four sockets — a chip that cannot be built. (The pinned path already shared
correctly, from docs/135 ⑤; only the auto path invented ports.)

The auto pass now allocates **one port per distinct ext** and maps that ext's
other qubits onto it, and says so: `ext1 -> 4 qubits, ext2 -> 3 qubits, …` plus
the reason (qubits on one cable arm together — the operational limit of owning a
single QDAC). Re-measured on the same chip with all 13 qubits biased: **4 digital
ports, port N ↔ extN**, 3–4 qubits each, `generate_config()` declaring digital
outputs 1–4 on one FEM. A qubit that declares no ext still allocates its own port
— there is nothing to group it with, and guessing a socket is worse than a port.

## 4ah. One label colour for every port (2026-08-31, user feedback)

> "노란색에 검은색 폰트로 q2, q3 이렇게 써있는데, 이거 좀 더 진한 dark yellow 색상으로
> 하고, font color는 다른 port와 동일하게 white로 통일해줄수 있어?"

docs/136 r2 gave the bias-tee port an amber fill (`#f1c40f`) bright enough that a
white label failed contrast, so that one port was lettered black while every
other port in the rack is white. On the real rack that reads as a rendering
fault, not as a role — the fill is already carrying the role. The fill is
`#a9791c` now (4.6:1 against white, AA for the bold 700 label) and the label
colour branch is gone: `UI_CONFIG.instrumentWiring.portLabelColor` for every
port, no exceptions. `/flux`'s `.flux-src-tee` chip follows the same value, since
its whole point is that "bias tee" is ONE colour app-wide. Verified in real
Chrome on the 17Q chip: 12 tee ports at `#a9791c` with `#ffffff` labels, a plain
z port at `#3498db` with the same `#ffffff`. Pinned by
`instrument_qdac_selfcheck.cjs`, whose label assertion now compares the two ports
to each other rather than pinning two different literals.
## 4ai. The diff had no search (2026-08-30, user-directed)

> "잘 되었는데, 검색 feature가 전혀 없네? live edit이나 json tree view에 있는 검색말이야."

Correct, and it was the one surface with hundreds of rows and no way to reach
one: the pane view (§4z/§4ab) and the list view had no search box at all. The
2-way TREE view has had one since docs/84 (`jsonTreeSearch`), which is exactly
why the gap read as an inconsistency rather than as a missing feature.

**One box, one grammar, both views.** `_diff_search.html` is a single partial
included by `_diff_panes.html` and by the list branch of `_diff_workbench.html`
— two copies would drift the way the five search boxes docs/96 unified did. It
is `input[type=search]`, so `/` (docs/113's focus-search) reaches it with no new
wiring, and matching is `window.SearchQuery` (space = AND, a standalone `|` =
OR, tight-binding — docs/96), never a private tokenizer.

**Client-side, because the rows are already here.** Every row the page holds is
in the DOM, so a keystroke costs no round trip and no re-render. A leaf shows
when the AND-of-OR groups all match its haystack — its dot path plus every
pane's value in BOTH forms, the raw one (`data-v`) and the grouped one on
screen, so `7003542323` and `7,003,542,323` find the same row. A container shows
when a matching leaf is beneath it (an ancestor walk over `data-parent`, the
same map the collapse walk uses), and its count chip re-counts to the matches
(`3` → `1 of 3 differing keys match`), restoring `data-count` when the box
clears.

**A hit you cannot see is not a hit.** A search expands the containers on the
way to a match, and restores the collapse state it found when the box is cleared
— unless the user collapsed something themselves during the search, in which
case their state wins and nothing is restored. Measured on the real chip: Depth
0 (80 containers collapsed, 1 row visible) → typing `q11` opens exactly the 13
rows of that subtree and leaves 73 containers collapsed → clearing puts all 80
back.

**The query rides the URL, and the server still does not filter.** `?q=` is
echoed into the box (escaped, capped at 200 chars) and carried onto every /diff
request the workbench issues, as a PARAMETER only (no button's URL holds one, so
htmx appends it exactly once) — the same `htmx:configRequest` channel §4z uses
for the baseline. So a tab switch, a source change or a "Show more" comes back
with the box still filled and the filter re-applied. What the server must NOT do
is filter by it: the counts above the table (`28 changed · 3,816 identical`)
describe the whole diff, and a server-side filter would silently make them
describe what someone typed instead. Pinned by an equality test over the two
rendered pages.

**Honest about what it could not see.** The row set is paged
(`_DIFF_LIST_PAGE = 300`), so with rows still unloaded the count reads
`21 of 91 keys · 1,957 more not loaded` — the "Show N more" button below is
still the way to load them, and it now carries the query too. And a
filtered-to-nothing table is a header over blank space, which reads as broken:
`.dp-empty-note` says `No key matches "zzzz" — the search reads key paths and
the values on screen` under the table, where the user is looking.

**One §4ab defect fell out of writing this.** The collapse walk keyed its
path → row map last-wins, so for a leaf that is ALSO a container on another side
(both rows carry the same `data-path`, and the leaf's parent IS its own path) the
walk found the leaf, read its parent as itself, and spun to the 64-guard —
collapsing that container hid its children but not its own value row. The map now
prefers the `dp-dir` row, which is the only row that can be collapsed.

**Real Chrome, the PJ_10082026 chip against 3 runs of the 2025-06-24 archive**
(171 rows / 91 differing keys): `/` focuses the box; typing through Chrome's own
input pipeline gives `q11` → `6 of 91 keys`, `RF_frequency` → 21,
`q11 resonator` → none (AND), `RF_frequency | T1` → 21 (OR), a pasted value
`1.6717958988072346e-05` → the one row that holds it; Escape clears from inside
the box; switching to node.json keeps `&q=resonator` in the URL and re-applies it
(`1 of 107 keys`); a baseline switch leaves the filter alone; zero console
errors. Two sources, List view: `q11` → `3 of 45 rows`, and the query carried
across the view switch.

Pinned by `diff_panes_selfcheck.cjs` (32 → 60 assertions), `TestSearchBox` in
`test_diff_three_way.py` and the partial/grammar pins in `test_diff_panes.py`.
Mutation-checked 13/13 client (filter ignored, no auto-expand, no restore, a
manual toggle no longer winning, the map regression above, the unloaded rest
unnamed, the query not riding the request, chips keeping the full count, Escape
dead, the list filter hiding nothing, no empty note, the note never leaving, AND
silently becoming OR) and 8/8 server (query not echoed, unbounded, the server
filtering, either view dropping the box, the chips losing `data-count`, the
paging truth unpublished, and `data-more` decorative rather than the real
remainder — the number the search reports as unsearchable).

## 4aj. Four window-and-search reports (2026-08-30, user-directed)

> "1. settings의 저 메뉴의 양쪽 위에 있는 모서리가 끊겨있어 · 2. calculator는 크기
> 조절이 안되고 있어 · 3. calculator 내부의 각 섹션 … 너무 밋밋해서 전혀 구분이
> 안가 … gray 색상은 주석이나 쓰일 곳이지 … 4. 돋보기가 내부 글자랑 겹친다 …
> 이제는 내부 SM 전체에 걸쳐서 문구를 이렇게 바꾸자. Search: space = AND, | = OR"

Each was reproduced in real headless Chrome first, and the first three all had a
cause other than the obvious one.

**① The corner was a child painting over its parent.** The three tool windows
(§4u) share one 10 px radius + 1.5 px SM-blue border, and each has a sticky
header with its own opaque background. A rounded corner clips its children only
where the box has an `overflow` — the Calculator has `overflow-y: auto` and the
Config Manual `overflow: hidden`, but Settings is `overflow: visible`, so its
square header simply painted over both top corners. Measured: panel radius
10 px, header radius 0, panel overflow `visible`. Rounding the HEADER instead
(8.5 px = 10 − 1.5, so the curves stay concentric) fixes it independently of any
overflow, and is applied to all three so the next window to lose its clip does
not re-open the bug. A/B at 6× on the real page: a hard right angle with the
border broken, versus one continuous curve.

**② The Calculator was the one tool window that could not be resized.**
`resize: none`, `display: block`, one scroll for the whole popover. It is now
the Config Manual's shape: a flex column with `resize: both` on a clipped box
(CSS `resize` is inert while `overflow: visible` — the trap worth naming), the
BODY scrolling so the header and the expression footer stay put, and the size
remembered in `quam_calc_size` under the same contract manual.js uses — only a
size the USER set is stored, so opening on a small screen (where the restore
clamps to the viewport) never shrinks what was remembered.

**③ The section titles were grey because the rule that says otherwise never
won.** `.calc-sec-label { color: var(--pico-contrast) }` is specificity (0,1,0);
Pico paints `details summary:not([role])` at (0,1,1) and
`details[open] > summary:not([role]):not(:focus)` at (0,3,1), both from its
ACCORDION variables. So every section header rendered in the muted grey
(measured `#98a1b3` against the page's `#d0d5de`) that a 2026-era comment in the
stylesheet claimed it had escaped — the fourth time this project has been bitten
by "the rule I can see is not the rule that wins". Fixed through Pico's own
mechanism rather than a specificity war: the two accordion variables are set on
`.calc-popover`, so every summary inside inherits the right colour and nothing
has to out-specify anything. Sections became CARDS (border, 8 px radius, their
own surface) with the open one's header tinted SM-blue 10%, which is what makes
"which section am I in" visible at a glance. Grey was then put back where grey
belongs: the row labels (`.calc-field`) and result labels (`.calc-rlabel`) are
what the row IS, so they read in the page colour; the help lines, the units and
the "or" connector are annotations and stay muted; result VALUES got weight 600
and full contrast. The before-shot also showed TWO disclosure marks per header —
Pico draws its own chevron as `summary::after` beside the app's caret — so the
Pico one is hidden and the left caret (the same one the Json tree and the Config
Manual use) stands alone.

**④ There were two magnifiers, and one of them was Pico's.** Pico gives every
`input[type=search]` a background magnifier plus a `padding-inline-start` that
reserves room for it. §4ai's box carries the app's own 🔍 span AND is
`type="search"`, and its compact padding shorthand overrode the reserve — so
Pico's icon landed on the first letter of the placeholder while ours sat
correctly outside. The fix is stated as a selector so the pairing cannot come
back: `.tree-search-icon + input[type="search"]` draws no background image.
(Worth recording: the first measurement said "no overlap", because it compared
the two ELEMENTS' boxes. The second icon was not an element.)

**The placeholder, everywhere.** Every search box carried its own
hand-written string — "Search keys or values...", "Search all pulses…",
"Filter qubits..." — and only two of them mentioned the AND/OR grammar the whole
app shares (docs/96). Examples are guessable; operators are not. `search_hint()`
/ `search_title()` in `core/search_query.py` (Jinja globals) are now the ONE
source: **`Search: space = AND, | = OR`**, with a surface's own scopes appended
after the grammar and never instead of it (`…, tag:, is:`), and the full
sentence in the `title` so nothing is lost to the compaction. Twenty-three
call sites across twenty templates use it. A template that hand-writes a search placeholder is now a test failure.

**Three of those boxes could not keep the promise, so they were fixed too.**
`filterTable` (every component table: Qubits, Pairs, Flux, Couplers,
Resonators, QDAC), `filterDetailPanel` (the inspector's in-panel search) and the
all-values grid were AND-only private splits. They now compose through
`SearchQuery` — the plain surfaces via `groups`/`matchesHay`, the scoped grid via
`groupBy` at the group level exactly as the bulk grid does. Three boxes stay
exempt BY ID and are pinned as exactly three: `sort-key-filter`,
`sort-param-filter`, `sched-lib-filter` are pickers over a short list of names,
not document search.

**And a standing-rule sweep fell out of it.** Eleven sites across app.js,
bulk-edit.js, pair-edit.js and dataset-virtual.js read `window.SearchQuery` in
the guard and a BARE `SearchQuery` in the call. In a browser those are the same
binding; in a Node realm the call throws instead of degrading — the docs/125
`CSS`-global trap. All eleven now name `window.` on both sides, and the pin
fails on any `window.SearchQuery ? SearchQuery.…` that comes back.

Verified in real Chrome on the PJ_10082026 chip: settings corner A/B at 6×, the
Calculator resizable with its body scrolling and its size surviving a close,
section titles at `#d0d5de` on a tinted card header, one magnifier, the house
placeholder rendering on every page, zero console errors. Pinned by
`tests/test_search_hint.py` (19) + `tests/search_hint_selfcheck.cjs` (17),
mutation-checked **22/22** (each of the four fixes reverted one way at a time:
the header radius, `resize`, the overflow that makes it work, the scrolling
body, the size store / apply / clamp-guard, the accordion variables, the card,
the open marker, the duplicate chevron, the muted labels, Pico's magnifier, a
hand-written placeholder on two surfaces, dropped scopes, a hint that stops
naming the OR, scopes replacing the grammar, each AND-only regression, and a
bare global creeping back). One thing to record for the next person: writing
`.calc-sec-label { color: … }` inside a CSS *comment* broke an existing grep pin
in `test_calc.py`, which slices from the first occurrence of that literal — the
comment was rephrased, not the pin.

## 4ak. A declared pair that built nothing, and said nothing (2026-08-31, user-directed)

Found while reproducing the customer's SNU_17Q chip for KH_20260824. That chip's
16 `FluxTunableTransmonPair`s all carry `coupler: None` — their CZ moves one
qubit's own flux — so the spec was written with the 16 pairs declared and no
coupler line. The build reported **`ok: True`, no warnings**, and produced a chip
with **zero pairs**.

**I first told the user SM's line vocabulary could not express this. That was
wrong, and worth recording as the actual lesson.** A pair materialises from a
pair LINE (`coupler` / `cross_resonance` / `zz_drive`, each of which allocates a
DC channel) **or** from the `pair_gate: "cz_fixed"` gate, which
`_finalize_pair_gates` creates with `coupler: None` and seeds the CZ macros on
the moving qubit's z. That is exactly the coupler-less pair. Setting it produced
all 16 pairs with the same five macros the real 17Q pairs carry
(`cz_unipolar`, `cz_flattop`, `cz_bipolar`, `cz_SNZ`, `cz_flattop_erf`),
`coupler: None`, `moving_qubit: control` — from SM's own generator, with no
post-step.

So the gap was never the vocabulary; it was the **silence**. Declaring pairs and
giving neither a line nor the gate is a spec that cannot do what it says, and
nothing said so — the chip looked built, and the absence only surfaces when
someone runs a two-qubit node on it. `_declared_pairs_not_built(spec, machine)`
now reports those ids in the build result (`pairs_declared_not_built`) and raises
a warning naming BOTH ways out, including that the gate's pair has no coupler —
because sending a chip that has no couplers down the line route costs a DC
channel per pair it does not have. On the 17Q spec the LF budget makes that fatal
rather than merely wasteful: 3 LF-FEMs are 24 channels, 13 go to flux, and 16
couplers do not fit in the remaining 11 (the original attempt died as
`NotEnoughChannelsException`, which named a channel shortage rather than the
design mistake behind it).

Pinned by `tests/test_pairs_declared_not_built.py` (11), mutation-checked 7/7 —
including the two the first version of the pins MISSED: a warning whose guard is
disabled, and one that stops naming the coupler-less route. A message in the
source proves nothing if nothing reaches it.

