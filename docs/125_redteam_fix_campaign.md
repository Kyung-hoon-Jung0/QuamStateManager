# docs/125 — the red-team fix campaign (docs/124 findings)

One section per fix, committed one fix per commit, each verified in real Chrome
on the real 20-qubit chip before landing. The findings ledger is docs/124; this
file records what was done about each and what the verification measured.

---

## Fix 1 — disarm htmx attribute settle for class/style (docs/124 §1.1)

**Findings closed:** M-7 (Trends class strip), M-13 (Explorer scroll restore
structural failure), M-15 (wiring-tab total blank), the *retheme-ability* half
of M-6. Root mechanism: htmx 2.0.4's attribute settle restores an id-matched
element's `class`/`style` to the incoming MARKUP values ~20 ms after every
swap, wiping anything a script set during the swap window — and this app
renders Plotly charts and the Explorer tree DURING the swap (inline fragment
scripts), so the wipe was deterministic on every reload after the first.

**The change (one line of production code):** `base.html` ships
`<meta name="htmx-config" content='{"attributesToSettle":["width","height"]}'>`
before the htmx `<script>` tag. Config-level rather than per-surface on
purpose: nothing in this app wants class/style settling (verified below), and
a per-surface fix (render after `htmx:afterSettle`) would leave the rake in
the grass for every future render-during-swap site — `#pulse-detail-plot`
already stands on it and survives only because an unrelated 250 ms re-render
re-adds its class.

**Pin:** `tests/settle_config_selfcheck.cjs` — 13 checks. Config half: the
meta exists, parses, excludes class+style, and PRECEDES the htmx script tag
(htmx reads the meta once, at load — placement is load-bearing). Behavior
half, under the REAL bundled htmx booted as a parse-time script in jsdom: a
class added and a style shown during the swap window survive settle with our
config, and a CONTROL run under default config still strips both — so the pin
is provably testing a live mechanism, and an htmx upgrade that changes settle
semantics fails the control loudly instead of making the fixed-side asserts
vacuous. (Two harness traps recorded in the file: htmx builds its hx-on
scanner via `new XPathEvaluator` — jsdom's XPath shim cannot run it, so the
harness stubs that constructor; and jsdom fires DOMContentLoaded on a task
AFTER the JSDOM constructor returns, so reading `htmx.config` synchronously
measures the pre-merge state — the first version of this pin did exactly that
and "failed" against a working fix.)

**Verified (three independent agents, real Chrome, own server + chip copy each):**

- *Trends (port 5361):* two full metric-chip on/off cycles → **0/3 charts
  stripped** (pre-fix: 3/3 after one cycle), all with live `_fullLayout`;
  theme toggle rethemes all three in BOTH directions (font `#444 ⇄ #373c44 ⇄
  #d0d5de`); zero console errors.
- *Explorer (port 5362):* **15/15 assertions.** After a production-equivalent
  rebuild (`htmx.ajax GET /explorer → #table-pane innerHTML`), `.json-tree`
  survives the whole settle window (10 samples, +0…+1000 ms), the tree stays
  the scroller (range 20,279 px vs the pane's 119), scroll **6000 → 6000 in a
  single write @743 ms** (pre-fix: four retries all clamped to 0), mono font
  intact. Wiring tab after the same rebuild: wiring tree visible, state tree
  hidden (pre-fix: BOTH `display:none`, empty pane).
- *Regression sweep (port 5363):* every settle-keyed site enumerated with a
  verdict — zero CSS rules on `htmx-settling`/`htmx-added`, no `settle:`
  modifiers, both swap-window class READERS target id-less elements (settle
  never applied to them), the four stable-id elements whose class/style vary
  across renders have no transition on the varying property, and the one
  transitioned element (`#tray-drawer`) gets its client-only class re-applied
  pre-paint at afterSwap so no animation ever depended on the settle copy.
  Dynamic: 9-page click-through (qubits/bulk+Ctrl+Z/explorer/topology/
  datasets/run-detail Data+Interactive/pulses/state-history/param-history),
  zero console errors, every pane rendered. Two apparent failures were probe
  artifacts, recorded: the tray drawer does not render at 0 changes, and
  headless Chrome defaults `prefers-reduced-motion: reduce` which routes the
  dataset row flash into its designed static-tint branch.

**Suite state after:** selfchecks **63/63** (was 62 files; the new pin is the
63rd). `test_web.py`: 2 failed — both on the CLAUDE.md Windows environmental
baseline (`TestPhase4QuamCacheConcurrency`, `TestDatasetSelectionFix`), not
regressions.

**Still open from the same findings:** the *first-paint* half of M-6 — dark
theme's Trends charts render Plotly light defaults until the first retheme
trigger, because ChipTrends never applies the house theme at initial render
(chip-status.js:2748). That is a separate fix (theme at render, or retheme
after build), not a settle issue. plot-theme.js's bare-class selection (fix
direction: `PlotHost.graphDivs`) also remains, now mostly defanged since
classes survive.

---

## Fix 2 — the Live-diff ✓/✗ buttons work, both layers (docs/124 C-1)

**Finding closed:** C-1 (critical, pre-existing on main). The tree renderer
wires the per-row ✓ Accept / ✗ Reject to `_acceptLiveValue`/`_rejectLiveValue`
— locals of the live-diff IIFE, invisible from the renderer's IIFE — so every
click threw ReferenceError and ✓ Accept was a **silent no-op**: the user
believed Qualibrate's value was staged, applied to live, and the value they
explicitly accepted never reached the hardware.

**The change:** the two handlers are exported on `window` (they close over the
live-diff IIFE's state — `_liveDiffDone`, the remaining count, the retrying
fetch — so unlike the `_deepEqual` precedent they cannot be copied into the
caller's scope; export is the correct inverse), and the renderer's onclick
closures call the `window.`-qualified names.

**The pin found a SECOND layer of the same defect before it ever shipped:**
with the calls fixed, clicking ✓ staged the edit and then threw again —
`_acceptLiveValue` itself calls `_formatValue`, a local of the *renderer*
IIFE. The request fired, but the pending mark, the incoming-marker clear, the
`_liveDiffDone` entry, the tray swap and the bar-count decrement were all
dead code after the throw — an accept that LOOKED ignored while it had
already staged. `_formatValue` is now exported beside `renderJsonTree` (one
definition, one formatter — the value element being repainted is the
renderer's own DOM) and the handler calls it qualified. `_swapPendingTray`
was audited too: top-level script scope, reachable from every IIFE, no change
needed.

**Pin:** `tests/livediff_buttons_selfcheck.cjs` (12 checks) — real app.js
under jsdom, real `renderJsonTree` in livediff mode, expansion driven by
clicking the real toggles (children are lazy). Asserts at the observable
level, where the pre-fix behavior produces nothing: ✓ issues exactly one
`/field/edit-batch` with the row's dot_path, the row turns pending, the
buttons are removed (that removal is why the row must be captured before the
click — the first draft read `.parentElement` of a detached button); ✗ clears
the incoming marker and updates the bar count. Suite: **64/64 selfchecks.**

---

## Fix 3 — the value-history mini trend renders where it lives (docs/124 M-18)

**Finding closed:** M-18 (major, pre-existing). `FieldHistory.renderChart`
bailed on `!window.Plotly` — a synchronous return, unlike every other mount,
which goes through the lazy loader. Plotly is lazy-loaded and the popover's
home surfaces (the qubit/pair inspectors, the bulk grids) mount no other
chart, so on any fresh page load the docs/20 mini trend was dead on arrival:
panel opens, table renders, mount + data in the DOM, no chart, no error — it
only ever appeared if the user had visited a plotting surface earlier in the
same tab session, which read as intermittent.

**The change:** the bail becomes `window.requirePlotly().then(render again)`
(panel-connected re-check; the panel is never detached, only hidden, and
renderChart re-queries its own mounts, so a panel that moved on to another
path renders that one — correct either way).

**Pin:** `tests/fh_chart_selfcheck.cjs` (5 checks) — real `FieldHistory.open`
against a stubbed `/field/history` response with NO Plotly present: the
loader is asked, the chart renders into `#fh-chart` when the library lands,
and the already-loaded control adds no loader round-trip. Two harness traps
recorded in the file, both the CLAUDE.md bridge-every-bare-global class: this
harness runs app.js through Node-realm eval, so bare `getComputedStyle` and
bare `Plotly` resolve via Node's `global`, and both misses were SWALLOWED by
FieldHistory's fetch `.catch` — the panel showed "Could not load history."
with nothing wrong on the wire. Suite: **65/65 selfchecks.**

---

## Fix 4 — scroll-restore abort: per-restore state, every scroll input (docs/124 M-16/M-17)

**Findings closed:** M-16 (shared abort flag resurrection) and M-17 (abort
blind to scrollbar interaction and ArrowUp/ArrowDown/Space). The settle fix
(fix 1) already removed the STRUCTURAL restore failure; these two were the
remaining defects in the retry mechanism itself.

**The change:** `_armScrollAbort` returns a per-restore `{aborted}` state and
`_restoreExplorer` stamps a generation (`_restoreGen`); a retry writes only
when its own state is un-aborted AND its generation is current — so arming
restore B can never resurrect restore A's timers (the executed four-yank
ping-pong), and a superseded restore's timers never write even without a user
scroll. The abort's event set gains `ArrowUp/ArrowDown/Space` (skipped while
typing in an input — arrows in the search box are typing) and `mousedown` on
the scroller element itself — Chrome hands a scrollbar track click or thumb
drag to the page as exactly that and nothing else — plus middle-click
autoscroll. **Deliberately NOT a raw `scroll` listener**: the browser fires
the same event when it CLAMPS `scrollTop` while the filter settles, which
would read as a user scroll and abort the very restore the retries exist for.

**Pin:** `tests/scroll_abort_selfcheck.cjs` (9 checks) — real app.js driven
through real `htmx:beforeSwap/afterSwap` events with a scrollTop write
ledger: baseline restore lands; wheel abort; **zero zombie writes of A's
stale target after B arms**; B completes; ArrowDown aborts while ArrowDown
*inside an input* does not; mousedown on the scroller aborts while mousedown
on a row does not; listener hygiene (wheel/touchmove delta zero; mousedown
pinned as no-growth because another app.js path lazily registers one
unrelated window singleton). One stale source-shape pin in
`explorer_search_selfcheck.cjs` (grepping for the old variable name) was
updated to the new mechanism — the docs/123 §8 pin class, met again.
Suite: **66/66 → 67/67 selfchecks** (with fix 5's new pin).

---

## Fix 5 — resizeWithin: snapshot-restore (docs/124 M-1, the campaign's own regression)

**Finding closed:** M-1 (major). Plotly 2.35.2's width relayout implies
`autosize=null` AND pins the other dimension, and `Plots.resize` — the
`responsive:true` window handler — permanently rejects once `layout.width &&
layout.height` are both set. One `resizeWithin` touch therefore killed a
chart's window-resize adaptation forever (executed: 168 px clipped off all six
`/topology` metric bar charts after an is-narrow crossing + shrink).
`c4df8c7` introduced it by making a dead call real — the dead call had been
accidentally protective.

**Method — measure before choosing:** a design-probe agent measured SIX
candidate payloads × THREE chart shapes (declared-height / fully-auto /
CSS-height holder) in real Chrome against the bundled Plotly, then acceptance-
tested the winner by runtime patch on the real `/topology`
(`tests/browser/_shots/fix4_lab.json`, `fix4_accept.json`). Winner: **P2
snapshot-restore** — snapshot `gd.layout`'s `{width,height,autosize}` inside
the render chain, `relayout({width})`, restore the three keys with no redraw.
`fullLayout` keeps the correction; `gd.layout` returns **byte-identical to
what the caller wrote**, so the window path behaves exactly as on an untouched
chart and a DECLARED `layout.height` survives (stock Plotly itself loses that
key on ordinary window resizes — the restore is strictly better). Rejected
with executed evidence: P3 (two relayouts — still freezes declared-height
charts, i.e. the `/topology` bars themselves), P5 (`height:null` destroys the
declared key — §5.2's class), P4 (`Plots.resize` after clearing keys — works
but rewrites the caller's layout). Key 2.35.2 gotcha recorded:
`relayout({autosize:true})`'s implied `width:null` **never deletes** an
existing `layout.width` key — only `Plots.resize`'s own `delete` does.

**Overturned along the way:** docs/122's "`Plots.resize` is a no-op on these
charts" did not reproduce in the probe's lab — resize recomputes from the
CONTAINER and is 100 ms debounced, so the old synchronous measurement read as
a no-op. The container-observer architecture stands regardless (nothing fires
resize on a container-only change), but the §6 evidence row is corrected.

**Verified, shipped code, no patching** (second agent, port 5365): the M-1
repro heals end-to-end — s2's 578 px holders hold **578 px** SVGs (was 746,
168 clipped), extra resize events and regrow track, heights `flh=520` at
every step, `layout.width` absent at every step; Trends-toggle and ndview
canaries green (settle fix and resize fix compose); zero console errors.

**Pin:** `tests/plothost_selfcheck.cjs` (11 checks) — the contract against a
2.35.2-faithful fake whose width relayout performs the implied edits exactly:
the three shapes' layouts return byte-identical, the `<2px` idempotence gate,
repeated touches, and the §8 pin-gap (`_graphDivs` includes its own root
node) closed while here.

---

## Fix 6 — the undo repaint cluster (docs/124 C-2 / M-8 / M-9 / M-10)

**Findings closed:** C-2 (critical — alias cells permanently stale after
Ctrl+Z), M-8 (phantom-dirty pair twins that also vetoed every later rebuild),
M-9 (`%.6e` repaint truncation becoming the next edit's baseline), M-10
(type-changing reverts silently losing the docs/56 stored-as-text
decorations), plus the readOnly-only coverage gap (diff-review minor).

**Server half:** `_revert_entry_payload` (routes.py) is now the ONE builder
for every cellsReverted/cellDiscarded entry — six hand-built sites had
already drifted once, which is how M-9 shipped. Each entry adds
`old_value_disp` (the grids' own lossless `group_digits` string — pinned:
`4,333,001,234.5678`, not `4.333001e+09`) and `old_kind`
(pointer/str_numeric/str/bool/num/null/other; bool before num — Python bools
ARE ints). `old_value_str` keeps the inspector-input format unchanged.

**Client half (both grids):** `revertPaths` matches
`data-dot-path` OR `data-resolved` — the apply path's own idiom — so the
alias cells the server's resolved paths name (every x180/x90 amp column on
the real chip; the pair grid's operations-over-macros twins) repaint value
AND baseline; prefers `old_value_disp`; and **coverage became a promise**:
an entry counts covered only when at least one writable cell was repainted
AND the repaint can stand in for a fresh render — a pointer revert, a
str-numeric decoration flip, or a readOnly-only match now report uncovered,
so the caller's existing debounced rebuild repaints honestly (the value
itself still updates immediately).

**Pins:** `tests/test_revert_payload.py` (17) for the server shape;
`tests/undo_repaint_selfcheck.cjs` (13) driving the REAL bulk-edit.js +
pair-edit.js: the C-2 alias repaint + coverage, M-9 lossless value AND
baseline, M-10's three coverage verdicts, readOnly refusal, M-8 twin heal
with zero dirty cells. Suite: **68/68 selfchecks**, grid pytest drivers
green (90 passed).

---

## Fix 7 — the 17b family's Interactive tile matches the lab (docs/124 M-12)

**Finding closed:** M-12 (major, executed inversion inside shipped code).
`recipes/ramsey_vs_coupler_flux.py` put flux on x citing "the lab's
convention for every flux sweep" — false for this family: the lab's own
plotting puts **idle time on x** (xarray default, xlabel "Idle time (ns)"),
as does the sibling 17a recipe and ndview's docs/122 rank. The Interactive
fringes tile therefore rendered transposed against BOTH the Raw-Data tab and
the lab's static PNG in the same menu — customer report #1's exact shape
surviving inside the campaign's own fix, deterministic for all three
generations (17b/21a/10b). And both wrong halves were green-pinned: the
recipe test froze the deviant orientation and no pin spanned the two
surfaces.

**The change:** the fringes heatmap orients idle-on-x / flux-on-y (dims
matched by NAME, both cube generations), the docstring's false claim is
corrected in place, the test pins the lab's orientation, and a
**cross-surface assert** ties the recipe to `_AXIS_RANK["idle_times"] <
_AXIS_RANK["coupler_flux"]` — the two tabs can never silently disagree on
this family again. The real-archive golden's candidate list gained the CQT
date-dir layout (`<root>/<date>/#N_…`) and the golden re-ran green against
the real run the red team's inversion was executed on (#490, 2026-08-14).
The `_freq` curve tile (frequency vs flux) is untouched — a 1-D curve over
flux is its own convention and was never in dispute.

---

## Fixes 8–11 — the four-major batch (docs/124 M-11 / M-4+M-5 / M-2 / M-6)

One commit (app.js hosts three of the four), each verified in real Chrome on
the real chip by an independent agent (all four PASS, zero console errors
across every probe; probes `tests/browser/_rt_fix7_{a,b,c,d}_*.cjs`).

**Fix 8 — M-11, the undo queue's own sync lane.** `UndoQueue` issued `/undo`
with `source: "#pending-tray"` — the same htmx per-element sync lane
(strategy "last") the grid ⚡ apply and the armed auto-apply flush use, so a
press queued behind their in-flight request was replaced-and-dropped and the
lone survivor re-issued against the tray element the apply's swap had
detached (executed pre-fix: 3 presses in an apply window → 0 requests). The
queue now issues from its own body-level `#undo-sync-src`, and an in-flight
apply (`window._applyInFlight`) HOLDS the press — ordered execution is also
the docs/107 model. Verified both ways: held window → 0 requests +
`depth()==3`, release → 3 in order; natural 2.5 s apply window → same, with
the tray ending at the 3 journal-staged `jrn:` inverses (docs/107 semantics,
expected). Two ctrlz pins that asserted `source === '#pending-tray'` — the
bug's own vector — were updated (§8's pin class again).

**Fix 9 — M-4/M-5, diff-mode truth is the DOM.** The closure flag survived
every pane swap while the toggle's class did not, so a fresh render with
diff previously ON produced flag=true/DOM=inactive — a silent dead first
click, reproduced through three ordinary sequences — and the zero-pairs
branch flipped only the flag, leaving a stuck-lit toggle that toasted "No
incoming changes" against a real divergence and could not be turned off by
its own button. `_explorerLiveDiffOn` is now a DOM derivation and
`_setLiveDiffUi` sets both halves everywhere, including zero-pairs. Verified:
the PaneState seq-mismatch producer arms on the FIRST click
(`/state/live-diff` fired, deadClick:false), and the stuck-lit sequence
unlights toggle + hides bar with the honest toast. Recorded observation (not
a regression): zero-pairs leaves the PRIOR overlay's amber row marks until
the next render.

**Fix 10 — M-2, the pinned-run interceptor speaks first.** It now registers
BEFORE the purge/teardown listeners (hoisted function; only the registration
moved), so its `shouldSwap=false` is visible to them; the `_io` teardown
listener gained the shouldSwap gate it never had (it also fired on failed
swaps); and the two-column build branch purges the pane itself, since the
choke point now correctly skips. Verified on the real run: pin → same-run
re-click keeps svgs 3→3 (pre-fix 3→0 unrecoverable) with `_io` alive, and a
different-run click still builds the two-column layout with both columns
rendering.

**Fix 11 — M-6 first paint.** ChipTrends renders through
`PlotTheme.houseLayout` (deep-merged UNDER its own overrides), so dark-theme
users get house colors at the FIRST paint instead of Plotly light defaults
held hostage to a retheme that only a theme toggle triggered. Verified:
fresh dark `/topology` → font `#d0d5de`, grid `rgba(140,150,165,0.18)` on
all three trend charts with no toggle; both toggle directions still track.

**Pins:** ctrlz_selfcheck +6 (own-lane + hold/release), livediff_buttons +4
(M-4 first-click arm, M-5 both-halves-off) **+1 behavioral re-apply pin**
closing the claim-audit gap (explorer_search's source-shape-only pin —
gutting the helper used to stay green; now the rebuilt tree must actually
hide non-matching rows). Two harness rules recorded in the files: Node-realm
eval does not expose window properties as bare globals (`renderJsonTree`
bridged), and `_activeTreeId` belongs to `_explorer.html`'s fragment script,
not app.js.

---

## Fix 12 — the ndview rank gate sees every sweep (docs/124 ndview minor)

The full-coverage armor gated on the x/y candidates only; an unranked sweep
short enough for the overlay bucket slipped past and the cube was
rank-ordered on partial evidence — exactly what the docstring promises
cannot happen. `_order_sweeps` now takes the gate population explicitly
(`sweeps + small_sweeps`). Archive impact bounded by the measured
1,803-of-1,805 full coverage. Pin includes a ranked-short control proving
the block comes from the unranked NAME, not from the overlay diversion.

---

## Round 2 — the surviving minors, plus docs/123 §7's bar charts

Seven small fixes in one batch (docs/124 minors + one docs/123 §7 leftover),
each browser-verified on the real chip (probes `_rt_fix8_{a,b,b2,c,d}_*.cjs`,
zero console errors across every run):

- **retheme selects structurally** — `plot-theme.js` rides
  `PlotHost.graphDivs` (bare class only as the no-PlotHost fallback); pinned
  in `plothost_selfcheck` with a class-stripped chart.
- **The Param History drawer purges before it destroys** — open/close/no-data
  all purge through the choke point they used to bypass (a `responsive:true`
  chart's window handler kept the detached subtree alive per open). Verified:
  exactly one purge per replacement, target still connected and live.
- **The value-history popover**: purge-per-reopen (leak measured healed —
  resize-listener count 3,3,3,3,3 across five opens; pre-fix would be
  3..7) and a singleton re-clamp on window resize. The verification caught
  the FIRST clamp guaranteeing only the top edge (bottom overhang up to the
  panel height, 270 px worst / 10.6 px realistic) — it now clamps by the
  panel's own height, floored at 8.
- **htmx history restore** gets the reachable half of a purge: the observer
  registry sweep on `htmx:historyRestore`. Plotly's own responsive handlers
  on the dropped graph divs are NOT reachable after the body swap — recorded
  in the comment, not hidden; all charts are SVG so the residue is plain
  memory, not WebGL contexts.
- **The undo queue finishes its honesty story**: a full queue toasts
  (throttled — a held key hits it ~30/s); a `/undo` that never settles gives
  up after 20 s with the queue dropped and an error toast instead of
  wedging the tier for the session; and Ctrl+Shift+Z while server ops are
  in flight/queued joins the server queue instead of answering from the
  client redo stack (the mixed-tier reversal). Pinned in `ctrlz_selfcheck`
  §7.
- **ChipTrends never double-renders a toggle** — `_reload`'s settle fallback
  gates on `__plotlyRenderChain` presence (set synchronously at render call)
  instead of sniffing the strippable class. Verified: exactly one render per
  chart per toggle (pre-fix: two), and the fallback stays alive for the
  late-response case it exists for.
- **The density slider drives `resizeWithin(.topo-dashboard)`** (debounced
  150 ms) — the docs/123 §7 wiring, safe since fix 5. The verification
  returned a first-class negative on the §7 PREMISE: on this chip the
  slider cannot change a bar-chart holder's width at ANY geometry (5
  viewports × 3 densities measured — `flex: 0 1 350px` + `flex-wrap` wraps
  instead of shrinking; only the grid reflows). What the wiring provably
  does: heals a desynced chart (injected 250 px → 350 == holder), preserves
  heights (M-1 contract), and leaves window-resize adaptation alive
  (350 → 698 tracked on all six). The trigger is correct armor even though
  the reflow the plan assumed does not occur on this CSS.
