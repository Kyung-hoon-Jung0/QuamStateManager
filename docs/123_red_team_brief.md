# docs/123 — red-team brief for the docs/122 campaign

You are reviewing eleven commits on `integrate/customer-feedback`
(`aeb0669` … `59f0d2a`), 55 ahead of `main`, nothing pushed. This document is
written to make you effective, not to make the work look good. Where a claim is
weak, it says so. Where something passed only by luck, it says that too.

**Your job is to find what is still wrong.** The most useful thing you can do is
disprove something in §4 or §5.

---

## 1. What the campaign was

Four customer reports, 2026-08-17:

1. the dataset **Raw Data** tab shows a 2-D map transposed against the
   **Interactive** tab
2. the **Json Tree View** search resets by itself, and diff mode leaves it broken
3. **Ctrl+Z / Ctrl+Shift+Z** is slow and unstable
4. the **Topology / Chip Status Trends** figure sometimes breaks

Then one follow-up sweep: extend the container-resize handling built for (4) to
the app's other Plotly surfaces.

Method used throughout: **measure on the real 20-qubit customer chip in real
Chrome, or execute against the real 839-run customer archive, before attributing
anything.** Read `docs/122_four_reports_phase0.md` first — it is the primary
record and it contains the numbers.

---

## 2. How to reproduce anything here

```
repo        D:\work\statemanager-cfb          (git worktree, branch integrate/customer-feedback)
python      D:\miniconda3\envs\cqt\python.exe  — always PYTHONUTF8=1, call it DIRECTLY
                                                 and redirect to a file. `conda run … | grep`
                                                 loses output on this machine.
suite       PYTHONUTF8=1 <py> -m pytest tests/ -q --timeout=900 --timeout-method=thread
selfchecks  npm install && npm run selfcheck        (62 files; they SKIP silently without jsdom)
dev server  node tests/browser/serve.cjs --port <p> --tag <t>
            → prints {port, root, chip, instance, pid}; serves its OWN COPY of the real chip
browser     tests/browser/harness.cjs  (puppeteer-core + installed Chrome)
```

Real customer data, **read-only, never write into these**:

```
chip     D:\work\Customer_Codes\CQT\CS_installations\qualibration_graphs\superconducting\quam_state
archive  D:\work\Customer_Codes\CQT\data\{2026-08-13, 2026-08-14, 2026-08-16}   839 runs
```

Verification probes are gitignored under `tests/browser/_*.cjs`. The ones for
this campaign: `_p0_explorer`, `_p0_autosync`, `_p0_diffsearch`, `_p0_undo`,
`_p0_undo2`, `_p0_trends`, `_v_final` (all four reports in one run).

### Things that will waste your time if nobody tells you

These each cost a full run. They are not app bugs; they are properties of the
environment that make a probe lie.

- **A fresh instance dir has no history**, so Chip Status Trends renders
  "nothing recorded yet" and there is no figure to measure. Fix:
  `POST /api/history/snapshot` a few times — and it needs an `Origin` header or
  it 403s.
- **`explorerLiveDiff(true)` is a silent no-op when the chip has no drift.** It
  toasts "No incoming changes" and returns WITHOUT rendering, so anything you
  measure "in diff mode" was never in diff mode. Assert the toggle is `active`
  before believing the measurement.
- **Writing the live files BEFORE navigating does not create drift**: the page
  load runs `reconcile_with_live`, which auto-pulls over a provably-clean
  working copy (docs/86 `RECONCILE_SYNCED`) and adopts your write. Open the page
  first, then move the chip.
- **`/datasets` is a virtual table** — `curl` sees zero rows and that is
  correct. Rows exist only after the client renders.
- **`elementHandle.screenshot()` perturbs the viewport** and wakes Plotly's
  window-resize handler. It once turned a broken figure into a healthy-looking
  one and nearly produced a false "it heals itself".
- **An occluded Chrome sets `document.hidden` and stops IntersectionObserver**
  entirely (docs/118). The harness passes
  `--disable-backgrounding-occluded-windows`; do not remove it.
- **`beforeunload`** fires when the working copy holds unsaved edits and freezes
  the renderer until answered. An unhandled dialog looks exactly like a hang.
- **`networkidle2` never settles** on pages that poll (`/pulses`, `/datasets`,
  `/state-history`). Use `domcontentloaded` + an explicit wait.

---

## 3. What changed, and the claim each change makes

| commit | claim |
|---|---|
| `89f2714` | Ctrl+Z presses are queued not dropped, and an undo repaints named cells instead of rebuilding the grid |
| `3ab9c31` | the Explorer search survives live-diff and an unattended rebuild; the diff bar admits what the search hides |
| `b35f64e` | ndview orients 2-D maps by the lab's convention, not by array size; a swap control exists |
| `84bac19` | `PlotHost` + a global purge-on-swap; the Trends figure follows its container |
| `1d26600` | the Explorer scroll restore is retried rather than written once |
| `c4df8c7` | three defects in `84bac19` repaired; four dead resize call sites made real |
| `303505c` | ndview, the histogram grid and the Param History drawer observe their containers |

Diffstat: 17 files, +1,868 / −67. Production code touched:
`core/ndview.py`, `web/static/{app.js, bulk-edit.js, pair-edit.js,
chip-status.js, ndview.js, pulses.js}`, `web/templates/{_explorer.html,
base.html}`.

**`app.js` took +539 lines and is where most of the risk lives.**

---

## 4. Where to attack first — claims I am least sure of

Ranked by how much damage a defect would do, not by how likely I think it is.

### 4.1 `PlotHost.resizeWithin` sets an explicit width and never releases it

`app.js`. It now does one thing: `relayout(el, {width: el.clientWidth})`,
chained through `el.__plotlyRenderChain`. It deliberately does NOT release back
to `autosize`, because releasing implies `height: null` in Plotly's implied-edit
table and destroyed the caller's height (see §5.2).

Consequences I believe but have not exhaustively tested:

- setting an explicit width turns Plotly's own `responsive` handling off for
  that div. I claim the container observer covers window resizes because a
  window resize changes the container. **Find a container that does not change
  on a window resize while the plot should still adapt.**
- I only ever set width. **Find a surface where the height must follow the
  container** (an aspect-ratio box, a flex child with `height:100%`).
- the chain is per element. **Find an ordering where a queued resize resolves
  after the element has been re-rendered with different data**, so it writes a
  width computed against the old layout. There is a `document.body.contains`
  and an `offsetParent` re-check inside the chain; decide whether that is enough.

### 4.2 The observer's single-owner rule walks ancestors

`PlotHost.observe` refuses if `container` or **any ancestor** already carries
`_phRo` or `_ro`. This was added so docs/118's interactive container and a new
per-tile observer could not fight. **Find a legitimate case where an outer
observer exists but does not cover the inner region**, so the inner one is
refused and that surface silently never resizes. The refusal is silent — there
is no log.

### 4.3 `_graphDivs` selects structurally, not by class

It unions `.js-plotly-plot` with the parents of `.plot-container.plotly`, plus
the root node itself. This exists because a real Trends holder had
`_fullLayout`, populated `.data`, three `svg.main-svg` children and Plotly's own
`.plot-container.plotly` — while its class attribute was exactly
`topo-trend-chart`. **I never found out WHY the class was missing.** That is an
unexplained fact in a shipped codebase.

Attack it: find what strips the class (SM code, Plotly version behaviour, an
htmx settle path). If the cause also strips something else, the union may be
covering a bigger problem. Also: `plot-theme.js:106` re-themes over the bare
`.js-plotly-plot` class, so any chart whose class did not stick **silently never
re-themes on a dark/light toggle** — that is a live bug this campaign did not
fix.

### 4.4 The undo repaint decides when a rebuild is still needed

`app.js` `cellsReverted` + `BulkEdit.revertPaths` / `BulkPairEdit.revertPaths`.
The rebuild is skipped when every entry was covered by a repaint and no entry is
`created`/`deleted`. **Find a state change an undo can produce that a
value-repaint cannot express and that is neither created nor deleted**:

- a value whose *type* changed (docs/56 str↔number), so the cell's quoting or
  amber marking should change
- a pointer edit (docs/40 three-mode), where the cell should show a link, not a
  value
- the docs/109 physical-units sub-line — I dispatch `input` to recompute it;
  check it actually recomputes for a *reverted* value and not just a typed one
- an FSP compensation group (docs/r12), where one undo moves a port field and
  many amplitudes at once
- the docs/107 journal path (`jrn:` gids), where an undo STAGES an inverse
  instead of reverting

### 4.5 The undo queue is bounded at 20 and refuses past it

`window.UndoQueue`. Past 20 queued, `push` returns false and the press is
refused. **There is no user-visible signal.** That is the same silence the bug
was about, at a different threshold. Decide whether 20 is reachable by a held
key (auto-repeat is ~30/s after the initial delay) and whether the refusal
should say something.

### 4.6 The axis ranking is exact-spelling and gated on full coverage

`core/ndview.py` `_AXIS_RANK` + `_order_sweeps`. Applied only when EVERY sweep
dim in the cube is ranked. Derived from 27 constraints, 0 cycles, and re-measured
over the whole archive: 1,805 two-sweep cubes, 1,803 fully ranked, 0 mixed, 650
changed, 14,820 of 15,470 untouched.

Known-and-accepted gaps, do not re-report unless you can show they are worse
than stated:

- `27_single_qubit_randomized_benchmarking` keeps the size sort and stays wrong;
  the lab AVERAGES `nb_of_sequences` away and plots Clifford depth. That is a
  *reduction* rule, not an ordering rule.
- `probe_flux` is deliberately unranked (the lab reduces that axis away too).

Worth attacking: the ranked names **conflate physically different quantities**
(`detuning` covers a readout frequency and a qubit drive frequency; `flux_bias`
covers qubit and coupler bias). Three independent agents raised it; none could
produce an inversion in this archive. **Produce one, or show a family outside
this archive where it inverts.**

### 4.7 Explorer scroll restore retries four times

`app.js` `_restoreExplorer`, attempts at 260/700/1400/2400 ms, each checking
whether the previous took, abandoned if the user scrolls (wheel / touchmove /
PageUp·PageDown·Home·End). **Find a case where 2.4 s is not enough** (a very
large tree, a slow filter) **or where the abort listener leaks** — it removes
itself on a 2600 ms timer and on first fire; check both paths under rapid
repeated rebuilds.

### 4.8 The global `htmx:beforeSwap` hook purges and unobserves

`app.js`. Skipped when `PaneState.isKeepRoute()` — i.e. `/explorer`, whose DOM
is parked alive. **Find a swap where PaneState parks something that DOES contain
a live plot**, or where the hook runs on a target whose subtree is about to be
re-attached rather than destroyed.

---

## 5. Things that were wrong and are now fixed — verify the fixes, not the bugs

These are listed because a red team should know where the author already made
mistakes; the same reasoning error may survive elsewhere.

### 5.1 Five hypotheses I asserted and measurement overturned

| I said | truth |
|---|---|
| a qualibrate write rebuilds the Explorer pane | **0 refetches in 120 s** unless Auto-Sync is armed |
| the customer's "it turns on again" means the diff mode | it is the **search**; diff-ON kills the filter, diff-OFF restores it |
| rapid Ctrl+Z races and an older response wins | peak concurrency **1**; six of ten presses were **dropped** by htmx before a request existed |
| the Trends breakage is leaked WebGL contexts | **8/8** fresh contexts after 20 toggles; every chart is SVG |
| the lab convention is a physical-quantity ordering | grouping creates **three cycles**; only exact spellings sort |

### 5.2 Three defects I shipped in `84bac19` and repaired in `c4df8c7`

- **the height destruction.** `relayout({width:null, autosize:true})` implies
  `height:null`. On Trends this was invisible **only because
  `.topo-trend-chart { min-height: 300px }` happens to equal the 300 the caller
  asks for.** Safe by coincidence. ndview asks 420 with a CSS min of 200; the
  Chip Status bar charts compute 160–640 with no CSS height at all.
- **the render race.** `resizeWithin` called `relayout` directly, bypassing
  `__plotlyRenderChain`.
- **the observer leak.** `PlotHost.unobserve` shipped with **zero callers**
  while `ChipTrends._reload` swaps its observed grid with `outerHTML` on every
  metric toggle.

Also `_graphDivs` used `querySelectorAll` only, which never returns its own
context node, so an `outerHTML` swap replacing exactly the graph div was skipped
by the purge.

### 5.3 A claim I reported more strongly than the evidence supported

I reported Explorer scroll as preserved on the basis of a "119 → 119"
measurement. That probe had asked for 600 and been **clamped to 119 at both
ends** — it proved nothing. A later run asking for 420 got 119 and exposed it.
If you find a measurement in `docs/122` whose before and after are suspiciously
equal, check whether both were clamped.

### 5.4 Four resize call sites that existed and did nothing

`base.html` (the Split.js gutter drag — the app's most important resize
trigger), `chip-status.js:76` (whose selector was a DESCENDANT match while the
chart IS that element), `pulses.js:267`, `setInteractiveCols`. All used
`Plots.resize` over `.js-plotly-plot`. **Both halves were wrong**, which is why
nobody noticed: the call is a no-op on this app's charts AND the class does not
always survive.

---

## 6. Verified how, exactly

Do not treat "verified" as uniform. This is the actual evidence grade per claim.

| claim | grade |
|---|---|
| Ctrl+Z: 10/10 requests, 0 grid rebuilds | **executed**, real chip, request ledger in-page |
| Explorer filter survives diff-ON | **executed**, row-content level (189/189 → 1362 @ 62%) |
| Explorer expansion 863→863, scroll 420→420, search kept | **executed**, unattended Auto-Sync pull |
| diff bar names what the search hides | **executed** ("— 3 of them are hidden by your search") |
| ndview x = flux_bias on the real 06 run | **executed**; control: `detuning` (150) is the LARGER dim |
| Interactive vs Raw Data agree on x | **executed**, both read off the rendered SVG titles |
| 650 of 1,805 cubes re-oriented, 0 mixed | **executed** over the whole archive by the shipped code |
| Trends / histograms / drawer / ndview follow their container | **executed**, 100 ms sampling, width AND height |
| observers 1→1 over six swaps, 0 stranded | **executed** |
| the axis ranking has no inversion | **executed** over ~1,900 cubes by three agents — but see §4.6 |
| `Plots.resize` is a no-op on these charts | **executed** on Trends only; generalised by source-reading |
| `.js-plotly-plot` does not always survive | **executed** on Trends only; cause UNKNOWN |
| the two `_plotlyRender` bypasses (`#phd-chart`, `#fh-chart`) | **source-read**; `#fh-chart` never exercised in a browser this campaign |
| `_explorerLiveDiffOn` desync ("dead first click") | **NOT reproduced, NOT fixed** — see §7 |

---

## 7. Open, unresolved, and deliberately not done

- **`_explorerLiveDiffOn` desync.** An agent found in code that the JS flag can
  disagree with the DOM, producing a dead first click on ⇄ Live diff. I could
  not reproduce it and did not fix it. No customer has reported it. **This is
  the single most interesting unclosed thread.**
- **Chip Status metric / 2Q-RB bar charts do not resize.** No ancestor observer
  can work: the driver is the density slider, which reflows siblings without
  changing any outer container's box. They need the density setter to call
  `resizeWithin`.
- **`/trends` (Datasets → Trends) does not resize.** `11_power_rabi` on the
  customer's own data produces **220 charts** (20 qubits × 17 metrics, no cap
  anywhere). Needs a batching story before an observer.
- **`plot-theme.js` re-themes over the bare class** — see §4.3. Live bug.
- **RB axis** and **`probe_flux`** — §4.6.
- The hero chip map and the value-history popover need nothing (CSS /
  `position:fixed` + `responsive` already cover them). Do not "fix" them.

---

## 8. Test state, and how to read it

```
suite        37 failed, 5,853 passed, 247 skipped        (~25 min, env `cqt`)
selfchecks   62 passed, 0 failed
```

The 37 are an environmental baseline in exactly eleven files, listed in
`CLAUDE.md`. **Per-file counts matter** — check them, not just the total:
16 `test_autofit_gates` · 4 `test_state_coherence` · 4 `test_autofit_synth` ·
3 `test_runner_p2` · 2 each `test_web`/`test_safe_io`/`test_capabilities_routes` ·
1 each `test_scanner`/`test_node_scan_cache`/`test_config_generator`/
`test_compare_hub_routes`.

A heavily-loaded 34-minute run produced **38**. The extra was
`test_live_drift::test_repeated_live_changes_accumulate`, which `CLAUDE.md`
already documents as a load-induced flake; it passed 21/21 in isolation
immediately afterwards. If you see 38, check that file before concluding
anything.

**The selfchecks skip silently without `node_modules`.** A machine without them
reports a clean run while every DOM-level pin is dormant. Run `npm install`.

New pins this campaign: `tests/test_ndview_axis_order.py` (12),
`tests/test_explorer_search.py` (3) + `tests/explorer_search_selfcheck.cjs`
(~28), plus extensions to `ctrlz_selfcheck.cjs`, `ndview_selfcheck.cjs` and
`interactive_stability_selfcheck.cjs`.

### Pins that had to be rewritten because they pinned the wrong thing

Worth knowing, because it tells you what kind of pin this repo tends to get wrong:

- `interactive_stability` E1 asserted **which Plotly entry point** was called,
  so a correct fix failed it.
- `ctrlz_selfcheck` asserted the **unconditional grid re-GET that was the bug**.
- an axis pin was written as a **`<600`-character source distance**; the real
  distance is 601. It is now an order contract.
- three selfchecks had to become async because behaviour moved onto a microtask.
  A synchronous jsdom file cannot see past one, and a stub that resolves
  synchronously would pin a completion path the real htmx/Plotly does not have.
- the older selfchecks' `window.localStorage = …` / `global.navigator = …`
  assignments are **silent no-ops** under Node 24 (those files are sloppy-mode).
  Their stubs are not actually installed.

---

## 9. Rules that constrain any fix you propose

- **The covenant (user-stated, binding):** a direct live write happens on an
  explicit Apply press **or** inside a user-enabled auto-apply session. Nothing
  may write `state.json` / `wiring.json` outside that.
- **SM never swaps what you are looking at** (docs/87) — a chip that moved
  out-of-band raises a banner and asks; it does not adopt silently on the
  user-facing path.
- **Never modify** `D:\work\Customer_Codes\CQT\...` or
  `D:\work\documentation-website`. Every verification uses a private per-run
  copy of the chip.
- **Never touch `master` on `qua-platform/CS_installations`.**
- Nothing here has been pushed. Do not push or merge without asking.

---

## 10. If you only do three things

1. **Attack §4.1 and §4.2** — the resize contract and the single-owner rule are
   the newest, widest-blast-radius code, and both rest on reasoning rather than
   exhaustive measurement.
2. **Explain §4.3** — why does a live Plotly graph div lose its
   `js-plotly-plot` class? An unexplained fact is where the next bug hides, and
   `plot-theme.js` is already broken by it.
3. **Reproduce or kill §7's `_explorerLiveDiffOn` desync.** It is the one thing
   an agent asserted and nobody could confirm.

And when you report: give the file:line and the executed measurement. Every
overturned claim in §5.1 was overturned by running something, not by reading it.
