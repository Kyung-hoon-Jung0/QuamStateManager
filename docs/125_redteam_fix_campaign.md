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
