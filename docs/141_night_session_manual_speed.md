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
cells (qubit grid 4,480 + pair grid 1,530 in the live render, more in-process
with every column), **1,140 bytes per cell of which ~250 were information**;
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
6,000 (the remainder is between rows and header cells). Per cell 1,140 → 404
bytes.

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
600-cell / 800-cold gates), deliberately CONSERVATIVE — px/char is the
client's 16-px-root fallback (the smallest glyph it ever assumes) and an
absent hint means a 1,920-px screen — so a server-cold column is always one
the client would have detached, never fewer hot columns than before. The
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

**Not in this cut, on purpose.** The PAIR grid renders whole (1,530 cells
on this chip, ~0.6 MB): `pair-edit.js` has no virtualization at all and the
qubit grid's mechanism is the one worth generalizing into a shared module
before a second consumer appears — a follow-up, with the same golden. The
cold td skeleton itself is ~0.5 MB (long derived keys in `data-col-key`,
76 JS consumers); the 329 headers ~0.5 MB. Navigation into a cold column
is one keypress late (the fetch), never wrong.

Pinned by `tests/test_bulk_virt_server.py` (planner mirror + gates +
hint clamping, the cold render and its map, hydration byte-identity against
the hot render, the memo and its invalidation, the chip guard, gzip) +
`bulk_virt_server_selfcheck.cjs` (36 asserts: adoption without geometry,
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
`chip_density_selfcheck.cjs` (26 asserts: per-panel size, the fine slider, the jump guard);
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
announced by the 60 s poll, not the wake. A root that cannot be read never
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

## 5. Tooling that came out of the night

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
`bulk_markup_selfcheck.cjs` (§4m). Every new pin mutation-checked
(3/3, 6/6, 7/7, 5/5); a wrong mutation and two vacuous pins were found and rewritten.
