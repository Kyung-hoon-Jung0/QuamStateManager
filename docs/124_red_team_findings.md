# docs/124 — red-team findings on the docs/122 campaign

The docs/123 brief was executed in full by an adversarial multi-agent review:
**12 attackers** (one per brief surface: §4.1–4.8, the §7 desync, plus a
fresh-eyes diff review, a fix re-verification pass, and a claim/pin audit),
followed by **independent verifiers** who re-ran every critical/major finding
on their own server instance with their own probes. Everything below the
"minor" line was re-executed by a second agent before being written here.
Probes are gitignored under `tests/browser/_rt_*.cjs`; each finding names its
repro.

**Scoreboard: 2 critical + 19 major, all independently re-executed and
CONFIRMED (0 refuted); 16 minor (attacker-executed, unverified); 22 notes.**
The brief asked to be disproved (§0: "the most useful thing you can do is
disprove something in §4 or §5") — that happened repeatedly, and the negatives
in §6 below record what survived attack, which is just as load-bearing.

---

## 1. Three root mechanisms explain most of the board

### 1.1 htmx 2.0.4 attribute SETTLE — the §4.3 mystery, solved, and it is bigger than the mystery

htmx's `handleAttributes` runs on every swap: for each `[id]` element in the
incoming fragment with a same-tag/same-id predecessor, it snapshots the
incoming **markup** attributes, copies the OLD element's settle attributes
onto the new node, and queues a settle task (default **20 ms**) that restores
the markup snapshot. `attributesToSettle = ['class','style','width','height']`.
Consequence: **any class or style a script sets within ~20 ms of an id-matched
swap is silently wiped.**

Executed consequences (each confirmed by an independent re-run):

- **The class strip (brief §4.3/§10.2), EXPLAINED.** `_topo_trends.html:58`
  gives each trend chart a stable id; `ChipTrends._reload` re-swaps
  `#topo-trends` outerHTML; the fragment's inline script renders Plotly
  DURING the swap (the class-add is synchronous); the settle task then
  `setAttribute('class','topo-trend-chart')` — stripping `js-plotly-plot`
  while `_fullLayout`/`.data`/svg survive. Captured stack:
  `setAttribute ← htmx cloneAttributes ← settle task`. Causal proof: setting
  `htmx.config.attributesToSettle=['style','width','height']` in-page stops
  the strip. Why it looked intermittent: the FIRST section build swaps into an
  empty placeholder (no old id → no settle task); **every subsequent reload
  strips deterministically** — one metric toggle strips all three charts for
  the life of the page. Brief hypothesis (1) (a wholesale `className=` write
  in SM code) is refuted by execution: the instrumented setter captured zero
  such writes.
- **Explorer scroll restore (1d26600) fails structurally on the first
  unattended rebuild** — see finding M-13. `renderJsonTree` sets
  `container.className='json-tree'` during the swap; settle strips it;
  `.json-tree{overflow:auto;max-height:…}` stops applying; the scroller flips
  from the tree (21,082 px range) to `#table-pane` (~118 px range); the
  captured tree-relative target becomes unreachable **by geometry**, and all
  four retries clamp to 0. The campaign's shipped "scroll 420→420" was
  measured in the post-strip pane regime — the same instrument-both-ends
  error class as §5.3's 119→119.
- **The wiring tab blanks completely** (M-15): `_restoreExplorer`'s tab
  restore runs inside the settle window; settle re-hides
  `#explorer-tree-wiring` (markup `display:none`), and — a second htmx defect,
  observed — `cloneAttributes` iterates the LIVE NamedNodeMap while removing,
  so removing `class` **skips the following `style` attribute**, leaving the
  state tree hidden too. Both trees end `display:none`; the header still says
  wiring.json; recovery requires a manual tab click.
- `plot-theme.js` re-themes over the bare class, so stripped charts never
  re-theme (confirmed, worse than the brief stated — see M-6), and
  `#pulse-detail-plot` has the same exposure but self-heals only because an
  unrelated 250 ms re-render re-adds the class (note).

### 1.2 Plotly 2.35.2's width relayout — resizeWithin permanently freezes every chart it touches (campaign regression, c4df8c7)

Executed against the bundled Plotly: a width-only `relayout` runs the implied
edits `autosize=null` **and pins the OTHER dimension** (`height` gets written
even though the caller never sent it). `Plots.resize` — which IS the
`responsive:true` window handler — **hard-rejects when both dims are
explicit.** So ONE `resizeWithin` touch kills window-resize adaptation for
that chart forever, whether or not it declared a height.

Mainline executed regression on the real chip (M-1): `/topology` at 1050 px
crosses the 900 px is-narrow threshold → `settle()` (chip-status.js:78-81)
touches all 6 metric bar charts (deliberately observer-less per brief §7) →
shrink to 880 px → 578 px holders hold 746 px SVGs — **168 px of every chart
(the outside value labels) clipped, unhealed** by resize events or time.
Pre-campaign this exact sequence self-healed: the old call's selector matched
nothing and Plotly's window handler still worked. **c4df8c7 ("make the dead
resize calls real") introduced the freeze — the dead call was accidentally
protective.** The freeze radiates through `base.html`'s onDragEnd pane-wide
touch to `/trends` (220 charts), Param-History trend charts, Raw-Data h5
plots, pulse-compare and Config-Viewer waveforms (mechanism executed, vector
source-read). Screenshot: `tests/browser/_shots/rt_resizecontract_frozen_bar.png`.

### 1.3 The undo repaint's contract holes — the repaint and the server speak different languages

- **C-2 (critical).** `/undo` entries carry the **resolved** dot_path
  (routes.py:10760); `BulkEdit.revertPaths` selects by `data-dot-path` — the
  **alias**. On the real chip, 40 of 260 curated editable qubit cells are
  pointer aliases, and they are exactly the **x180/x90 amplitude columns for
  all 20 qubits**. Ctrl+Z reverts the server (tray 1→0, peek returns the old
  value) but the visible alias cell keeps the undone value, **clean-marked**;
  worse, the miss triggers `_virtHydrateAll`, which materializes the cold dyn
  column whose direct cell absorbs the repaint and reports the entry COVERED —
  so the fallback rebuild is never scheduled. Two cells for one leaf disagree
  on screen; the docs/109 phys sub-line shows −4.0 dBm where the truth is
  −10.0; a later edit typed over the stale cell commits from the stale
  baseline. **Regression:** at aeb0669 the handler unconditionally dispatched
  `quam:state-changed` and the re-GET healed it. (The apply path already
  matches by `data-resolved` — the undo repaint should too.)
- **M-9.** Every repaint writes `_fmt_val` (`%.6e`, 7 sig figs) into cells the
  grid renders losslessly via `group_digits`: after one Ctrl+Z,
  `4,333,001,234.5678` becomes `4.333001e+09` on screen AND in `data-orig` —
  so the truncated string is the next edit's baseline, destroying the sub-kHz
  tail `group_digits` exists to protect. All five emit sites
  (undo/redo/discard/discard_all) share it.
- **M-8.** Pair-grid alias twins: `_mirrorLinked` heals the twin's value but
  not `data-orig` → a phantom-dirty cell that (a) shows a false unapplied-edit
  state, (b) fires the leave-confirm with zero real edits, and (c) — because
  the `quam:state-changed` listener refuses rebuilds while any `.dirty` cell
  exists — **permanently vetoes the resync that is the only healing path for
  uncovered undo entries.**
- **M-10.** Type-change undo (docs/56 str↔real): the repaint writes only
  value/data-orig, so the quote spans / amber / tooltip vanish — a string
  renders as a clean plain number until a manual reload. The docs/77 repair's
  advertised "one Ctrl+Z" produces N such lying cells at once.

---

## 2. Critical findings

| id | where | finding |
|---|---|---|
| **C-1** | app.js:5826/5835 | **Live diff per-row ✓ Accept / ✗ Reject are DEAD** — wired to bare `_acceptLiveValue`/`_rejectLiveValue`, which are locals of a *different* IIFE and never exported. Every click throws ReferenceError; the accept is **silently lost** — the user then applies to live believing the incoming calibration was merged. This is exactly the cross-IIFE class the branch itself fixed for `_deepEqual`; the sibling functions were left behind. **Pre-existing on main**; zero test coverage anywhere; also invalidates the earlier probe's `accepted: true` step (`button.click()` returning true never meant the handler ran). Only "Accept all" and the inline editor work. |
| **C-2** | bulk-edit.js:2771 | The alias/resolved repaint mismatch above (§1.3) — flagship x180/x90 amp columns permanently stale + clean-marked after Ctrl+Z, rebuild suppressed by the hydration-absorbed "covered" verdict. Campaign regression vs aeb0669. |

## 3. Confirmed major findings (19, all independently re-executed)

Grouped; each is one confirmed finding with its own repro probe.

**Resize / PlotHost**
- **M-1** app.js:8807 — the §1.2 permanent freeze (c4df8c7 regression, 168 px clip executed).
- **M-2** app.js:8913 + 11334 — **purge-then-cancel**: the global beforeSwap purge (and the docs/110-era bare-class listener at :292) run before the pinned-run interceptor sets `shouldSwap=false`, so clicking the already-pinned run **purges the kept layout's figures** (svg 3→0, `_io` disconnected, `data-rendered` still "1" → tab re-click rebuilds nothing). Blanking pre-exists at aeb0669; the branch doubled the purge, added `unobserveWithin` on the same path, and shipped the "about to be replaced anyway" claim.
- **M-3** app.js:8841 + chip-status.js:91 — **"single owner per subtree" is false as shipped**: `ChipStatus.layout` holds an unmarked RO on `#table-pane` co-owning the same charts as two `_phRo` observers (three observers, one subtree — today benign via the &lt;2 px gate), and registration order defeats the walk: observe(inner)-then-observe(outer) SUCCEEDS (ancestor-only walk; self checks only `_phRo`). `pulses.js` and `scheduler.js` hold further unmarked observers.

**Explorer / live diff**
- **M-4** app.js:13718 — **the §7 desync is real**: the flag is a closure survivor while `_explorer.html` always renders the toggle inactive; three deterministic producers of a dead first click (PaneState seq-mismatch refusal after a grid edit; a held live-diff response committing flag-only against a detached pane; `stateRestored` soft-refresh) — all ordinary daily sequences.
- **M-5** app.js:14044 — **reverse desync**: the zero-pairs no-op flips the flag but not the DOM. The workbench auto-open path drives it with the server gate PASSING: the toggle ends stuck-lit, toasts "No incoming changes" while the server reports a real divergence, and **its own button can never turn it off** (only the bar's Exit diff). Client verdict runs against stale `_treeData`; server gate runs against live diff — structurally disagreeing.
- **M-6** plot-theme.js:104 + chip-status.js:2748 — retheme misses stripped charts (brief admitted) **and** ChipTrends never applies the house theme at first render — dark-theme users get Plotly light defaults at first paint, and the strip closes the only path that could ever fix them.
- **M-7** _topo_trends.html:58 — the settle strip itself (§1.1), deterministic after the first reload.

**Undo tier**
- **M-8/M-9/M-10** — §1.3.
- **M-11** app.js:4581 — **queued presses silently swallowed during an in-flight tray apply**: `applyEditsToLive` and the auto-apply flush share source `#pending-tray` with the queue's `/undo` POSTs; htmx's per-element sync (strategy "last") replaces queued requests and resolves the pump instantly; the sole survivor re-issues against the detached old tray element and dies on the isConnected guard. Executed: 3 presses in an apply window → **0 `/undo` requests**, no toast; natural window on this chip 557 ms, 0/2 presses landed. Chronic under an armed auto-apply session — the original customer symptom reintroduced through a side door.

**ndview / axis rank**
- **M-12** ndview.py:574 — **executed inversion inside shipped code**: `17b_ramsey_vs_coupler_flux` — ndview (rank: idle on x) and the Interactive recipe (flux on x) render **transposed maps of the same run**, deterministically, for the whole family (17b/21a/10b). Ground truth (the lab's own figure, xarray default + `plotting.py`) sides with the **rank**; the **recipe is the deviant** (its docstring's "flux on x is the lab's convention" is false — the sibling 17a recipe itself plots idle on x). The rank table cites 17b as support for BOTH sides (:574 vs :579), and both wrong halves are green-pinned (`test_ramsey_vs_coupler_flux.py:123` pins the deviant orientation; no cross-surface pin can notice the tabs disagree). Customer report #1's shape survives the fix in this family — visible side-by-side in the same Interactive tab against the lab's static PNG tile.

**Scroll restore (1d26600)**
- **M-13** app.js:3947 — structural failure on first unattended rebuild (§1.1); the shipped 420→420 was measured in the post-strip regime.
- **M-14** htmx.min.js — the settle mechanism finding itself (root cause; includes the NamedNodeMap skip defect).
- **M-15** app.js:3922 — the wiring-tab total blank (§1.1).
- **M-16** app.js:3898 — **shared abort flag**: arming restore B resets `_restoreScrollAborted`, resurrecting restore A's already-aborted 1400/2400 ms timers — executed ping-pong between A's stale target and the user's position, four yanks over 2 s. Fix shape: per-restore generation token.
- **M-17** app.js:3905 — the abort listens for wheel/touchmove/PageUp·PageDown·Home·End only: a **real scrollbar track click** (executed, headful Chrome — Chrome consumes scrollbar input, zero page events) and ArrowDown/Space scrolling are invisible, and the retry — which compares against the TARGET with no memory of its own writes — yanks the user back. A `scroll` listener ignoring self-writes covers every input class.
- (note) retries burn all four attempts against an unreachable target with no bail-out.

**Claim audit**
- **M-18** app.js:14628 — **`#fh-chart` never renders on its primary surfaces**: `renderChart` bails on `!window.Plotly` instead of `requirePlotly()`, and the popover's home surfaces (inspectors, bulk grids) load no other plot — so the docs/20 mini-trend is dead on arrival unless the user visited a plotting surface earlier in the tab session. Pre-existing; brief §7's "the value-history popover needs nothing" is disproved by the first execution it ever got. One-line fix.
- **M-19** (diff-review) bulk-edit.js:2785 — the `%.6e` repaint (§1.3 M-9's diff-review twin; same defect found independently by two attackers, counted once in fixes).

## 4. Minor findings (16, attacker-executed, not independently verified)

- base.html:1238 — the freeze radiates via the Split-gutter onDragEnd pane-wide touch (vector for M-1).
- app.js:8884 — a parked pane loses every `_phRo` observer on the first routine swap anywhere; restore hands back zero observers, nothing re-observes (latent: `/explorer` is the only keep route, and it has no plots).
- app.js:3867 — four purge sites still select by the bare `.js-plotly-plot` class the campaign itself proved unreliable; a stripped chart dies un-purged.
- app.js:12166 — the Param History drawer's destructive path (raw fetch + innerHTML) bypasses the purge choke point; destroys the live `#phd-chart` unpurged.
- chip-status.js:2748 — dark-theme first paint uses Plotly light defaults (the minor half of M-6).
- app.js:8909 — htmx **history restore** is a destructive body swap that bypasses "the ONE place every destructive swap goes through"; a live chart is destroyed with zero purge/unobserve.
- bulk-edit.js:2784 — `revertPaths` counts a path covered when every matching cell is readOnly: nothing repainted, rebuild suppressed, the readonly cell keeps the undone value.
- chip-status.js:2556 — metric toggle double-renders every chart (the settle fallback tests `.js-plotly-plot` before the async render chain ran).
- app.js:4591 — MAX=20 is reachable by a held key in under a second; the refusal is invisible (preventDefault already fired, push() return discarded).
- app.js:4585 — a `/undo` that never settles wedges the server undo/redo tier for the session (no timeout at any layer).
- app.js:4617 — mixed-tier interleave: Ctrl+Shift+Z during an in-flight server undo answers from the client redo stack first — reverses the wrong press.
- ndview.py:605 — the full-coverage gate is leaky: an unranked sweep diverted to the overlay bucket escapes the gate, so a partially-ranked cube can still be rank-ordered.
- app.js:14613 — the value-history popover and PH drawer leak one window-resize listener + a detached Plotly subtree per open.
- app.js:14590 — window shrink strands the `position:fixed` popover fully off-screen (`config.responsive` covers only the plot).
- explorer_search_selfcheck.cjs:152 — pin gap: the re-apply pin is source-shape-only; gutting the function body passes.
- app.js:8770 — pin gap: the root-self branch of `_graphDivs` (the exact §5.2 repaired defect) has no pin.

## 5. Notes worth keeping (selection of 22)

- **ctrlz_selfcheck pins a stubbed `revertPaths`** — the C-2 alias mismatch and the M-8 data-orig gap are structurally invisible to it.
- The two purge layers disagree on the keep-route rule (app.js:292 has no isKeepRoute skip and runs before PaneState's park).
- OOB swaps fire `htmx:oobBeforeSwap`, not `beforeSwap` — the choke point never sees them; append-style purge-through is structural in htmx (beforeSwap fires before the swap style is resolved) — currently moot: **zero append swaps exist in the templates** (executed grep, a §4.8 negative).
- `PlotHost.unobserve` (singular) still has zero callers — the leak fix's real mechanism is `unobserveWithin` at the global hook.
- Plotly's own responsive path deletes a caller's explicit `layout.height` on plain window resizes of untouched charts — §5.2's min-height coincidence is **load-bearing today**.
- `pump()` dumps the entire queue if `#pending-tray` is missing at pump time (currently unreachable).
- docs/122's Phase-1 table still cites the discredited "scroll 119→119" as after-fix evidence.
- docs/123 §8's Node-24 stub warning is half-right: only `navigator` fails to install; the `localStorage` stubs ARE effective in the eval'd scope.
- **Environment traps for future probes**: port **5357 is Windows WSDAPI** (PID 4 — never assign it); the `cqt` env's site-packages holds a **stale non-editable quam_state_manager 0.9.7** that shadows the worktree for `python -c` imports (run from repo root or set PYTHONPATH); headless Chrome uses overlay scrollbars (no hit target — scrollbar probes need headful).

## 6. What survived attack — verified negatives

- **The chain armor (§4.1c) held**: six adversarial orderings against the real `__plotlyRenderChain` all pass — width is read at flush time, never captured at queue time; detached elements skipped; re-attached elements get their current width.
- **No height-follow surface exists (§4.1b)**: all 15 Plotly mounts enumerated; every one fixes height in layout or holder CSS; the app's one height-changing trigger (the vertical split gutter) needs no chart height change.
- **Observed surfaces track correctly**: Trends `_fullLayout.width` 609→921→1215 across window resizes, registry exactly `[topo-histograms, topo-trends-grid]`, no leak over toggles.
- **§4.2 as posed has no live victim**: no non-resize `_ro` exists on any DOM element; no observe target sits under a marked ancestor; "refused forever" is impossible (every disconnect path nulls the marker); a real RO survives detach/re-attach (fires 300→0→500).
- **§4.8's append attack has no target**: zero append-style swaps in all 63 templates.
- The `#field-history-panel` popover is body-level — outside every pane-level resize/purge blast radius (its real defects are M-18 and two minors, different mechanisms).
- The screenshot-perturbation trap did not contaminate M-1 (re-measured after screenshot: clip persists).
- `test_ndview_axis_order.py` and the axis-rank derivation survive as measured over this archive **except** the 17b family (M-12): the "no inversion over ~1,900 cubes" sweep was honest but blind to recipe-vs-rank disagreement, which needed both surfaces built for the same run.

## 7. Campaign claims now overturned (add to the §5.1 ledger)

| the campaign said | the truth |
|---|---|
| "the container observer covers window resizes" (§4.1) | one `resizeWithin` touch permanently disables the window path (Plotly implied-edit + `Plots.resize` reject); on observer-less surfaces this is a shipped regression |
| "Ctrl+Z presses are queued not dropped" (89f2714) | presses are silently swallowed whenever a tray-sourced apply is in flight; chronic under auto-apply |
| "scroll 420→420, restore retried and verified" (1d26600) | measured in the post-strip scroll regime; the fresh-page case loses the position by geometry — the §5.3 error class, repeated |
| "the axis ranking has no inversion" (§4.6) | 17b/21a/10b: ndview and Interactive are deterministically transposed; the recipe is the deviant and its own test pins the deviation |
| "the value-history popover needs nothing" (§7) | `#fh-chart` never renders on its primary surfaces without a prior Plotly load |
| "one global beforeSwap purge is where every destructive swap goes through" | pin-cancelled swaps purge kept DOM; history restores and OOB swaps bypass it entirely; the drawer's innerHTML path bypasses it too |
| "the class strip cause is unknown" (§4.3) | htmx settle, proven causally; and it also breaks the scroll restore and the wiring tab |

## 8. Fix directions (none applied by the red team)

Highest-leverage first; several are one-liners once the mechanism is known:

1. **Settle**: render after `htmx:afterSettle` (or exclude the affected ids
   from `attributesToSettle`, or re-assert class post-settle) for
   `#topo-trends` charts and the Explorer tree; this alone clears M-7, M-13,
   M-15, and the retheme half of M-6.
2. **resizeWithin**: restore `autosize:true` alongside the width write (or
   explicitly re-write height from the caller's intent) so `Plots.resize`
   keeps working; re-check the §5.2 height-destruction pin still holds.
3. **C-1**: export/rename the two handlers (the `_deepEqual` fix pattern).
4. **C-2 + M-8/M-9/M-10**: repaint by `data-resolved` (the apply path's own
   idiom), ship `group_digits` strings in the payload, mirror `data-orig`,
   and carry `stored_kind` so the str-numeric decorations re-render.
5. **M-11**: give the queue's `/undo` its own sync source (or
   `hx-sync:queue all` semantics) so tray applies can't swallow presses.
6. **M-4/M-5**: derive the flag from the DOM (or stamp the toggle state into
   the rendered markup) and make the zero-pairs branch also reset the DOM.
7. **M-12**: swap the 17b/21a/10b fringes orientation recipe-side (the lab
   figure sides with the rank) and fix `test_ramsey_vs_coupler_flux.py:123`.
8. **M-2**: register the pin interceptor before the purge hooks (or have the
   purge hooks re-check `shouldSwap` on a microtask).
9. **M-16/M-17**: per-restore generation token + a `scroll` listener that
   ignores self-writes.
10. **M-18**: `requirePlotly().then(renderChart)`.

## 9. Test-state impact

No production code was changed by the review. New knowledge for the pin
inventory: ctrlz_selfcheck (stubbed revertPaths), explorer_search re-apply
(source-shape), `_graphDivs` root-self branch (unpinned), and
`test_ramsey_vs_coupler_flux.py:123` (pins the deviant orientation) should be
treated as **gaps**, not green lights, until the fixes land with real pins.
