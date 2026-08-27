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
  search clear / column re-tick runs the hydration pass synchronously so a
  hidden-at-mount cold column never paints one frame of empty tds.

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
row's checkbox. The table's own trigger is filtered
(`pulses-changed[!(event.detail && event.detail.paths)]`) so it re-fetches
only for a structural change (create / delete / rename / duplicate / a state
restore) or a legacy plain trigger. More than 24 touched rows ⇒ the server
says structural instead. Pinned by `TestPulseRow` (route, one-template
identity, the trigger payload after an edit and its undo including the
pointer-linked sibling) and `undo_pages_selfcheck.cjs` (the client patcher).

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
`ctrlz_selfcheck.cjs` (new fallback contract). Every new pin mutation-checked
(3/3, 6/6, 7/7); a wrong mutation and two vacuous pins were found and rewritten.
