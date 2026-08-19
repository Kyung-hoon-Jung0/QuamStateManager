# docs/122 — four customer reports: measurement, then the fixes

Reported 2026-08-17, four items:

1. the dataset **Raw data** tab shows a 2-D map transposed against the **Interactive** tab
2. the **Json Tree View** search resets by itself, and diff mode leaves it broken
3. **Ctrl+Z / Ctrl+Shift+Z** is slow and unstable
4. the **Topology / Chip Status Trends** figure sometimes breaks

Phase 0 changed no code; Phase 1 (below) is the four fixes it licensed. Its output is numbers: every claim below was either executed
against the real 20-qubit CQT chip in real Chrome, or executed against the real
839-run customer archive. Where a hypothesis did not survive its measurement it is
recorded as overturned, including four of my own.

Environment: a private copy of the customer chip served on its own port
(`tests/browser/serve.cjs`, 5,470 grid cells), Chrome via `tests/browser/harness.cjs`,
archive `D:\work\Customer_Codes\CQT\data\{2026-08-13,-14,-16}` (839 runs, 1,687
`ds_*.h5`, 15,470 plottable cubes). Probes are the gitignored `tests/browser/_p0_*.cjs`.

---

## 1. The axis transpose

**Confirmed, and the mechanism is exactly one line.**

The user settled the direction: Interactive is correct, because it uses the same axis
scheme as the qualibrate-generated figure. So the recipes hold the ground truth and
`ndview` must follow them.

`ndview._default_view` (`ndview.py:572`) sorts sweeps by **array size** and takes
`x = sweeps[0]`. Nothing else in that function reads a dim's name, units or
`long_name`. The recipes instead orient by NAME, per family, mirroring the lab's own
plotting modules (`resonator_2d.py:224` carries an explicit unconditional `.T` and
says so in its docstring).

Measured over **53 executed 2-D runs across 10 families** — for each run the
Interactive figure was built through the app's own code path and the ndview cube
through `ndview.build_cube`, and both axis dim names recorded:

| | runs | ndview x == lab x |
|---|---|---|
| lab-convention x dim is the **larger** array | 30 | **30 / 30** |
| lab-convention x dim is the **smaller** array | 20 | **0 / 20** |
| the two sweep dims are exactly equal | 3 | 3 / 3 |

**24 of 53 runs disagree** (20 on the x axis alone). The separation is total: ndview's
x is literally `argmax(size)` over the sweep dims. This is why the customer sees it
"sometimes" — the same family transposes or not depending on how many points that
particular run swept. `05_resonator_spectroscopy_vs_power` appears on both sides of
the table within the sampled runs.

### The design question, and how it was decided

Proposal: replace the size sort with a curated name ranking, in the spirit of the
existing `_SHOT_DIM_NAMES` / `_ENTITY_DIM_NAMES` lists in the same file. That only
works if one ranking can satisfy every observed convention.

**It can — but only at exact-spelling granularity.** 27 constraints were extracted
from the recipes and the lab's own `x=`/`y=` arguments, then topologically sorted:
0 cycles over 21 distinct dim names. Grouping spelling variants into physical
quantities — which is what I sketched during the discussion (`flux > freq > power`) —
**creates cycles**:

- `DURATION > FREQ` (19a/21b) vs `FREQ > DURATION` (11b_rabi_chevron)
- `AMPSCALE > DURATION` (23_zz_off_jazz) vs `DURATION > AMPSCALE` (2Q_19/31_chevron)
- and `qubit_flux > coupler_flux` (18a) orders two members of the same physical group,
  which a grouped ranking cannot express at all

So the ranking must be by exact dim name. My grouped sketch would have been wrong.

### What the adversarial pass killed

Three refuters attacked the ranking from different angles; **two succeeded**, and the
survivor is not the ranking but two specific over-reaches:

- **Refuter 1** searched for an inversion across 1,979 two-sweep cubes in 947 runs and
  found **none** — every one of the 16 two-sweep families matches the proposed order.
  What it did find is an **omission**: `27_single_qubit_randomized_benchmarking` has
  neither `nb_of_sequences` nor `depths` ranked, so it keeps the size sort and shows
  `x=nb_of_sequences` while the lab *averages that dim away* and plots Clifford depth.
- **Refuter 2** attacked the one rank the derivation itself flagged as
  "UNCONSTRAINED by any observed figure — placed by analogy": `probe_flux`. On
  `50a/50b_flux_crosstalk_dc` run with a probe, the dataset carries four dims and the
  lab **reduces `probe_flux` away** (`isel(probe_flux=k)`) rather than plotting it. A
  rank placed by name-shape analogy would put it on y instead of `detuning`.
- **Refuter 3** (blast radius) could not refute: the fallback claim holds. **14,820 of
  15,470 cubes are untouched**; the 650 that change are all inside families the
  constraint list names; every change is a pure x↔y swap and the payload is otherwise
  byte-identical, verified by rebuilding real cubes both ways including one where
  decimation fires.

Both successful refutations are the same shape, and it is a shape docs/82 already
knows: **sometimes the convention is not "which of two dims goes on x" but "this dim
should not be an axis at all."** That is a reduction rule, not an ordering rule.

Consequences for the fix, decided here:

- ship the ranking, but **only ranks backed by an observed figure** — drop `probe_flux`.
  An unranked name falls back to the size sort, i.e. today's behaviour, so refuter 2's
  counterexample becomes *unchanged* rather than *wrong*.
- the RB case is **pre-existing and not made worse**: it is the size sort today and
  stays the size sort. Recorded as a separate known gap, not fixed here.
- the ranked names do conflate physically different quantities (`detuning` covers a
  readout frequency and a qubit drive frequency; `flux_bias` covers qubit and coupler
  bias). All three refuters raised it; **no inversion follows from it in this archive**.
  It is a latent risk if a future family disagrees, not a present defect.
- docs/82's shot rule stays structurally above the ranking: `_default_view` buckets by
  `kind` before sorting, and the ranked names intersect `_SHOT_DIM_NAMES` and
  `_ENTITY_DIM_NAMES` in the empty set (verified programmatically).

Also: ndview has **no axis control at all** — `renderControls` emits entity/slider chips
and the IQ component selector only. Whatever the default, the user cannot correct it.

---

## 2. The Json Tree View search

Two failures, one root, and the unattended trigger was found in a place I had not
looked.

### 2a. A plain external write does NOT rebuild the pane

Overturning my own hypothesis: rewriting the live `state.json` out of band
(`qubits.q1.f_01` 4,333,000,000 → 4,334,000,000) with no Auto-Sync session armed
produced **0 `/explorer` refetches over 120 s**; search text, row counts and expansion
were untouched. `routes._auto_pull_due` only signals a pull when an Auto-Sync session
is armed, so the plain drift path raises a banner and touches nothing.

### 2b. Armed Auto-Sync rebuilds it, unattended

With `pull` armed, the same external write fired a pull at **~25 s** and one
`/explorer` refetch. At that moment the tree was **unfiltered and collapsed** —
129 visible rows, 11 expanded — recovering to 1,362/855 about 2.5 s later when
PaneState's SOFT tier re-dispatched the box's `input` event. **Scroll was lost**
(119 → 37).

### 2c. Live-diff kills the filter outright, and `/workbench` turns it on by itself

`explorerLiveDiff(true)` calls `renderJsonTree` and `_autoExpandAndTag` and **never
calls `jsonTreeSearch`**. Measured at row-content level, with `diffOn` asserted true
and 3 rows tagged incoming:

| | diff | tree rows | visible | rows NOT matching the query |
|---|---|---|---|---|
| search `amplitude` applied | off | 7,808 | 1,362 | 846 (62 %, ancestors) |
| **⇄ Live diff ON** | **on** | 189 | **189** | **189 (100 %)** — `octaves`, `mixers`, `twpas` |
| re-type the same query | on | 7,808 | 1,362 | 846 (62 %) |
| Exit diff | off | 7,808 | 1,362 | 846 |

Every row on screen fails the query while the box still reads `amplitude`; re-typing
the *same value* restores the filter **without leaving diff mode**, so the search
function is fine and simply is not called.

The unattended enable path is `workbench.html:512` → a 3 s poll → `onLiveChanged`
(`:407`) → `showLiveDiff()` (`:434`) → `sm.contentWindow.showLiveDiffInline()`
(`:438`) → `explorerLiveDiff(true)`. **On `/workbench`, every qualibrate write flips
the Json Tree View into diff mode by itself** — and diff mode is what breaks the
search. That is the customer's two sentences as one chain, and re-reading their
Korean confirms 그것 refers to the *search*, not the diff:
"search box 모드가 꺼지고 그것이 또 다시 켜진다".

Two measurement traps cost a run each and are recorded so nobody repeats them:
with no drift on the chip `explorerLiveDiff(true)` finds nothing, toasts "No incoming
changes" and **returns without rendering** — the tree never enters diff mode; and
writing the live files *before* navigating lets the page load's `reconcile_with_live`
adopt them (docs/86 `RECONCILE_SYNCED`), again leaving nothing to diff. The probe must
open the page first, then move the chip, and must assert `diffOn` before believing
anything measured inside.

Also found in code and not yet reproduced: the tree containers lose their
`class="json-tree"` — the element that carries `overflow:auto` — on every same-route
`/explorer` swap; and `_explorerLiveDiffOn` can desync from the DOM ("dead first
click"). Both are candidates for the scroll loss above.

Lost across a rebuild today: expanded node set (recorded nowhere), active
state/wiring tab (`_explorer.html:10` hardcodes `active` on state.json), scroll, and
— across `explorerLiveDiff(true)` — the filter itself. Preserved: search box text and
its filter across an ordinary same-route swap, tree scale, and the ⚠ spec marks
(re-derived, not preserved).

---

## 3. Ctrl+Z

Two facts, both measured, and both different from what I predicted.

### 3a. The lag is one full grid rebuild

One press on `/bulk`, with the request ledger instrumented in the page:

| | |
|---|---|
| `POST /undo` itself | **55 ms** |
| tray count moved | 152 ms |
| **`GET /bulk` — the whole grid, again** | **2,418 ms** |
| `/diagnostics/findings.json` · `banner` · `summary` | 556 · 333 · 326 ms |
| `/type-alert` · `/type-alarm/banner` | 104 · 73 ms |

The A/B settles it: the **same press on `/explorer`, which has no grid, moves the tray
in 56 ms and issues no `/bulk` at all.** The whole delay is the rebuild.

The rebuild is `bulk-edit.js:2366` — `cellsReverted` (`app.js:3070`) dispatches
`quam:state-changed`, whose grid listener re-GETs `/bulk` wholesale. The `/undo`
response already carries every affected `dot_path` and its old value; `_revertCell`
patches inspector inputs in place but cannot reach grid cells, so the code falls back
to discarding and refetching the entire pane. The diagnostics follow-ups are all
`delay:300–500 ms` htmx debounces, so a burst collapses them to one — they are not
the problem.

### 3b. Presses are silently swallowed — not raced

I predicted concurrent POSTs landing out of order. Measured: **10 presses → 4
`htmx:beforeRequest` for `/undo`, 3 completed, peak concurrency 1, tray count never
went backwards.** Every one of the 10 reached the server tier
(`tierPerPress: ['server'] × 10`), so `LiveEditUndo` did not consume them — htmx's own
per-element bookkeeping **dropped six requests before they were ever created**.

There is no application-level guard anywhere between the keypress and the POST
(`app.js:4419-4461`), and `#pending-tray` declares no `hx-sync`, nor does any ancestor.
The tray's ↶ button issues the identical unguarded call.

Redo is not symmetric: 5 presses → 4 requests, **peak concurrency 2**.

So the fix is not serialization — it is to stop *dropping*: queue the presses, and
stop paying 2.4 s per press. A `/undo?steps=N` batch is feasible (`/discard_all`,
`routes.py:11062`, is the working precedent) but must re-evaluate the log tip after
**every** step: an ordinary step consumes `change_log` while a journal step *grows* it
and decrements `undo_cursor`, so it can never be "N × `undo_group`".

---

## 4. The Trends figure

Six candidates; **two of my four were refuted, and two I had not considered rank
above them.**

| | cause | status |
|---|---|---|
| 1 | `ChipTrends`' `htmx.ajax` calls pass **no `source`**, so htmx queues them against `document.body` and against every other body-sourced request — the toggle's request is **never sent** | measured, agent |
| 2 | `_chipSectionBuilt[key] = true` is set **before** the fetch (`chip-status.js:1716`) | measured, both |
| 3 | a late Trends response swaps into a **detached** target (htmx resolves the target eagerly) and is silently dropped | measured, agent |
| 4 | no ResizeObserver; Plotly 2.35.2's `responsive:true` listens to **window** resize only | measured, both |
| 5 | `scattergl` can render an empty div with no canvas (needs > 4,000 nodes; this chip has 20) | latent |
| 6 | ~~un-purged `outerHTML` swaps leak WebGL contexts~~ | **REFUTED** |

Cause 4, sampled every 100 ms for 6 s after each real geometry change:

| container change | holder | SVG | healed within 6 s |
|---|---|---|---|
| sidebar collapse | 742 px | **609 px** | **no** (133 px gap) |
| split-pane drag | 665 px | **609 px** | **no** (56 px gap) |
| window resize | 851 px | 851 px | yes, immediately |

Cause 2 is worse than "sometimes": aborting the *first* `/topology/trends` request
leaves `#topo-trends` completely empty (`innerHTML` length 0) and scrolling away and
back does **not** retry — Trends is gone until the page is reloaded.

Cause 6 was my leading candidate and it is false. After 20 metric toggles the browser
still granted 8 of 8 fresh WebGL contexts, and live `.js-plotly-plot` nodes went
10 → 8 and never grew (agent: 8 purged, 0 detached renders). Every Trends chart on
this chip is SVG — 20 nodes against a 4,000-node GL gate — so the missing purge, which
is a genuine violation of the app's own rule at 7 of 15 mount points, produces **no
symptom here**. It stays worth fixing; it is not the customer's bug.

One self-correction: an intermediate run reported 742/742 and nearly became "the
figure heals itself". That was my own instrument — puppeteer's element screenshot
perturbs the viewport and wakes Plotly's window-resize handler. The time series above
was taken without it.

**13 of 15 Plotly mounts in the app have no container-resize handling; 7 violate the
purge-before-destroy rule.** Trends is where it was reported, not where it is rare.

---

## What Phase 0 overturned

| hypothesis | verdict |
|---|---|
| a qualibrate write rebuilds the Explorer pane | **false** — 0 refetches unless Auto-Sync is armed |
| "그것이 또 다시 켜진다" means the *diff* re-enables | **false** — it is the *search*; diff-ON kills the filter, diff-OFF restores it |
| rapid Ctrl+Z races and an older response wins | **false** — peak concurrency 1; six of ten presses are dropped before a request exists |
| the Trends breakage is leaked WebGL contexts | **false** — 8/8 contexts granted after 20 toggles; the charts are SVG |
| the lab convention is expressible as a physical-quantity ordering | **false** — grouping creates three cycles; only exact spellings sort |

## Phase 1 — what shipped, and what each fix cost

| | commit | before → after, measured on the same chip |
|---|---|---|
| 3 · Ctrl+Z | `89f2714` | 10 presses → **4 requests → 10**; one press → **2,418 ms of grid rebuild → none** |
| 2 · Explorer | `3ab9c31` | diff ON → **189/189 rows fail the query → filter applied**; expansion **lost → 855 kept**; scroll: ~~119→119~~ **retracted** — that probe asked for 600 and was clamped to 119 at BOTH ends, so it proved nothing (docs/123 §5.3); the honest measurement is the later 420→420 (`1d26600`), itself superseded by docs/124 M-13/M-14 — the 420→420 was measured in the post-settle-strip scroll regime, and the real fix is the settle disarm (docs/125 fix 1: 6000→6000 in one write) |
| 1 · Axis | `b35f64e` | 650 of 1,805 two-sweep cubes re-oriented to the lab's convention; 14,820 of 15,470 untouched |
| 4 · Plots | `84bac19` | sidebar collapse **609 in 742, never healed → 1531 = 1531 in 500 ms**; aborted fetch **never recovers → recovers** |

### Three things the implementation found that Phase 0 had not

**A repaint is not a substitute for a rebuild — except where it provably is.**
The undo response names every path it reverted, so the grid can repaint those
cells. It cannot express a `created`/`deleted` entry (a restored subtree adds
columns; an undone creation turns a cell back into "not set") and it cannot
speak for a path that has no cell. Those two cases keep the rebuild, debounced.
Two traps inside that: `querySelectorAll`, not `querySelector` — two columns can
resolve to the same leaf through an alias pointer or a linked shared-port pair,
and patching only the first leaves the twin showing the undone value; and
coverage is per **entry**, not per surface — a qubit leaf is legitimately absent
from the pair grid, so summing each surface's misses would have demanded a full
rebuild for every ordinary edit, i.e. kept the 2.4 s.

**`.js-plotly-plot` is a class, and it does not always survive.** The first
version of the resize fix attached a ResizeObserver that fired correctly
(verified: 2 hits) against **zero** targets: a Trends holder carried
`_fullLayout`, a populated `.data`, three `svg.main-svg` children and Plotly's
own `<div class="plot-container plotly">`, while its class attribute was exactly
`topo-trend-chart`. `PlotHost` selects structurally instead. Then the second
wall: **`Plotly.Plots.resize` no-ops on these charts** — holder 1531,
`_fullLayout.width` 1265, `autosize: true`, element displayed, still 1265 after
`Plots.resize` *and* after `relayout({autosize:true})`. An explicit
`relayout({width})` moves it, and releasing the width restores autosize. That is
also why docs/118's helper, which used `Plots.resize`, could not have covered
this surface even if it had been pointed at it.

**A pin can pin the wrong thing.** `interactive_stability`'s E1 asserted *which
Plotly entry point* was called rather than which plots move, so a correct fix
failed it. `ctrlz_selfcheck` asserted the unconditional grid re-GET that was the
bug. The axis re-apply pin was first written as a `<600`-character distance and
the real distance is 601; it is now an order contract (after the tagging, before
the toggle is armed).

## Phase 2 — extending PlotHost, and what the survey refused

Item 4 left `PlotHost.observe` wired at exactly ONE site while 14 other Plotly
mounts had no container-resize handling. The obvious move — install the observer
centrally inside `window._plotlyRender`, the app's own render choke point — was
surveyed and then attacked. **All three attackers refuted it**, each on a
different axis, each verified in source:

- `_plotlyRender(divId, data, layout, config)` receives only a graph div, so it
  is not a place where a CONTAINER can be chosen at all — and the right
  container is surface-specific (`.ds-interactive-plot` for a tile,
  `#ndv-root` for ndview, `#topo-histograms` for the histogram grid).
- `/trends` renders **220 charts** for one real experiment on the customer's own
  data (`11_power_rabi`: 20 qubits × 17 metrics, no cap anywhere).
- the two-step release `relayout({width:null, autosize:true})` **destroys the
  caller's explicit height** — `autosize` implies `height: null` in Plotly's
  implied-edit table.
- and it would have missed two surfaces outright: `#phd-chart` and `#fh-chart`
  call `Plotly.newPlot` directly (13 of 15 mounts go through the door, not 15).

### Three defects in what item 4 had already shipped

The survey was run to extend the code and found the code wrong first.

| | |
|---|---|
| **height destroyed** | Verified in a browser: after one sidebar collapse the Trends chart's `layout.height` was GONE. It stayed 300 px on screen only because `.topo-trend-chart { min-height: 300px }` happens to equal the 300 the caller asked for — safe **by coincidence**, and the coincidence does not hold on ndview (asks 420, CSS min 200) or the Chip Status bar charts (computed 160–640, no CSS height). Fixed: width only, no release. |
| **raced the render** | `resizeWithin` called `Plotly.relayout` directly and never touched `el.__plotlyRenderChain` — the per-element chain `_plotlyRender` exists to serialise. Now chained, re-reading `clientWidth` inside the chain. |
| **leaked an observer per toggle** | `PlotHost.unobserve` shipped with ZERO callers while `ChipTrends._reload` swaps its observed grid with `outerHTML` on every metric toggle, and a ResizeObserver holds a STRONG reference to its target. `unobserveWithin` now runs beside `purgeWithin` at the global `htmx:beforeSwap` hook, off a registry rather than a DOM query. Measured after: 1 observer across six swaps, zero stranded. Also `_graphDivs` used `querySelectorAll` only, which never returns its own context node — so an `outerHTML` swap replacing exactly the graph div was skipped by the purge. |

### Four resize call sites that already existed and did nothing

All four used `Plots.resize` (measured no-op) over `.js-plotly-plot` (the class
that does not always survive). Routing them through `PlotHost` fixed several
surfaces with no new observer at all — including **the Split.js gutter drag
(`base.html`), the app's single most important resize trigger**, and
`chip-status.js:76`, whose selector was a DESCENDANT match while the chart *is*
the `.topo-metric-bar-chart` element.

### Newly observed, measured on the real chip

| surface | container | width | height |
|---|---|---|---|
| Chip Status histograms | `#topo-histograms` | 636 → 507 in 500 ms | 344 → 344 |
| Param History drawer | `#param-history-drawer` | 1248 → 1514 in 500 ms | 340 → 340 |
| ndview Raw Data | `#ndv-root` | 1292 → 1558 in 500 ms | **420 → 420** |

The ndview line is the proof that the height fix mattered: it is the surface the
survey predicted would collapse to its 200 px CSS floor.

### Deliberately not wired, with the reason

- **Chip Status metric / 2Q-RB bar charts** — the driver is the density slider,
  which reflows siblings without changing any outer container's box, so an
  ancestor observer would never fire. They need the density setter to call
  `resizeWithin`, not an observer.
- **hero chip map** — pure SVG at `width:100%; height:auto`; CSS already does it.
- **value-history popover** — `position:fixed` with a JS-set pixel width, so only
  a window resize moves it and `config.responsive` covers that.
- **`/trends`** — 220 charts for one experiment. An observer there needs a
  batching story first.

## What is still unknown

- the exact cause of the Explorer scroll loss (two candidates: the lost `json-tree`
  class on same-route swaps, the `_explorerLiveDiffOn` desync) — not reproduced
- whether the customer's flow is `/workbench` (which auto-enables diff) or a bare
  `/explorer` with Auto-Sync armed. Both are broken; the fix is the same, but the
  first is the one that needs no user action at all.
