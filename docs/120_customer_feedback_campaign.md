# 120 — Customer feedback campaign: 11 items

*2026-08-16. A customer round of feedback arrived as 11 items (9 numbered, 2
raised mid-discussion). Each was worked through with the user until intent and
design decision were explicit; this file is the binding record, and each large
workstream gets its own implementation doc (docs/121+). Same shape as the
docs/110 daily-flow campaign.*

## What the customer is actually asking for

The through-line is that the daily loop costs too much: typing the same search
terms over and over, two chip maps where one would do, per-qubit trends when
the question is chip-wide, a sync model that asks permission the user already
wants to grant standing, and no way to get back to a known state quickly.

## Decisions the user made (binding)

| # | Item | Decision |
|---|---|---|
| 1 | Standalone folder | Indicate it; editing was never blocked |
| 2 | QDAC generation | Done — docs/119 |
| 3 | Search syntax panel | `?` is enough; make it toggle |
| 4 | Live-Edit chips | Chip-DERIVED sections + validated leaf keywords; AND/OR toggle leftmost, default **AND** |
| 5/9 | Chip Status Trends | **Any numeric parameter**, conditional on the overhead investigation (§ below) |
| 6 | Anharmonicity | Investigated — no bug |
| 7 | FSP amps | Editable before commit |
| 8 | Auto-Sync | Ticking "auto replace" **is** the consent ⇒ live wins silently; unticked + local edits ⇒ banner, never silent |
| 10 | Version id + history | Advanced UX over the existing State History |
| 11 | One chip map | Keep the hero, absorb the cards, C/T/M + frequency chevrons |
| — | Order | Quick wins first, then big |

## The verification target

The real customer chip, measured — this is what every item is checked against:

- 20 qubits, 30 pairs, **all** with `grid_location` → the strict grid gate
  passes, so it renders in `physical` layout mode.
- **Mixed architecture**: 11 × `QdacBiasedFixedFrequencyTransmon`
  (`QdacBiasLine`) + 9 × `FluxTunableTransmon` (`FluxLine`), plus a top-level
  `state["qdac"]`. Exactly the chip item 2 was built for.
- Every pair carries `qubit_control` / `qubit_target` **and an explicit
  `moving_qubit`** ∈ `{"control","target"}` (15/15 split), one-hop pointers.
- All 30 pairs: both `f_01` numeric, |Δ| 93–280 MHz.
- **433 run folders, each carrying its own `quam_state/{state,wiring}.json`**.
- `T1` / `T2ramsey` / `T2echo` / `gate_fidelity` are **null** chip-wide (early
  bring-up) — which is why the curated-11 metric tier would render a nearly
  empty Trends page, and the single strongest argument for item 5/9's scope.

## The item 5/9 overhead investigation — the user's condition, answered

The user approved "any numeric parameter" *conditionally*: **investigate the
performance, speed and stability overhead, report, then decide.** Measured by
running the real `core/leaf_index.py` against all **433 real snapshots** into
an in-memory SQLite with the production schema:

| measurement | result |
|---|---|
| numeric leaves per snapshot | **2,892** (+1,675 non-numeric kinds) |
| distinct indexed paths | **3,887** |
| change points, 433 snapshots | **24,851** (first snap 3,742, **median 7** after) |
| **whole index size** | **1.21 MB** |
| ingest | ~20–28 ms/snapshot |
| **all-20-qubits, one metric, overlay query** | **0.32 – 0.92 ms** |
| the same query, batched `IN(...)` | 0.93 ms — **no gain** |
| path typeahead (`search_paths`) | 0.6 – 4.8 ms |
| full rebuild (out-of-order penalty) | ~10 s |

A caution raised during planning was **wrong and is corrected here**: that
`leaf_index.series()` answers one path at a time, so a 20-qubit overlay means
20 queries. Measured, those 20 queries cost **0.92 ms in total** and batching
them gains nothing.

**Coverage is the real argument.** The index holds 516 `amplitude` paths,
1,616 `operations`, 840 `macros`, 488 `resonator`, 169 `opx_output` — and
**588 paths have more than one change point**, i.e. 588 genuinely trendable
parameters. The movers are exactly what this lab retunes:
`qubits.q2.xy.operations.x180_DragCosine.amplitude` (198 change points),
`ports.mw_outputs.con1.1.3.full_scale_power_dbm` (198),
`…resonator.operations.readout.integration_weights_angle` (202).

**Verdict: proceed with any-numeric-parameter**, curated 11 as the default
preselection so the page is useful on open. Two disclosed risks, both
pre-existing properties of `leaf_index` rather than things Trends introduces:
the ~10 s rebuild on out-of-order arrival (docs/83: "rebuilt, not repaired") —
so Trends reads, never ingests, and never triggers it on a render; and the
index covering SM's snapshots, so a chip SM has never snapshotted must say so
honestly rather than draw an empty axis.

---

## Wave 1 — shipped

### Item 6 — anharmonicity: investigated, no bug

> *"in the qubit spec vs power code, how is anharmonicity calculated now? Is it
> 2 × (difference between GE and EF/2 freq)? Investigate; if it's right then
> it's okay, maybe a user is confused."*

`core/interactive_plots/recipes/qubit_spec_vs_power.py` implements exactly that
identity. The 2-photon g→f/2 line sits at `f_01 − α/2`, so a click assigns
`anharmonicity = 2·(f_01_fit − clicked)`, encoded as the affine
`{"scale": -2e9, "offset": 2.0 * ge}` and anchored to **this run's** fitted GE
frequency. It is offered only when the run's analysis carries the ef fit
(`_EF_VARS`), so an older analysis gets an *unavailable* tile rather than a
wrong one.

A bug of this shape did exist and **is already fixed**: the tier-2 node-name
matcher used to route a standalone `28_qubit_spectroscopy_e_to_f` into the GE
recipe, making the E→F peak clickable into `f_01` — wrong by the anharmonicity,
~200–300 MHz (`recipes/qubit_spectroscopy.py`). **No action**; reopen only
against a specific run.

### Item 3 — the syntax help stops covering the list

> *"whenever a user types something in the dataset search box the syntax always
> shows up, so the user needs to scroll down to see the folder list — NO GOOD
> UX. I think just having a ? symbol inside the box is already enough. Small
> bug: clicking ? pops it up, but clicking again does not close it."*

Two independent defects, both present in **both** parallel implementations (the
Datasets page's id-based handler and the generic class/data-attribute one the
sidebar filter uses):

1. the panel opened **itself** on the first focus of its input per browser
   session (`quam_dataset_search_help_shown` / `quam_search_help_shown`);
2. the `?` was **open-only** — `hidden = false` with no else — so the control a
   user opened the panel with could not put it away. Only the `×` could.

Why it reads as *covering the folder list* rather than a mild annoyance: the
Datasets panel floats (`position:absolute`), but the sidebar's copy is
deliberately `position:static`, because a narrow `overflow-y:auto` sidebar
would clip an absolute popover. Static means **inline**, so opening it pushes
the whole experiment tree down. One keystroke buried the list and the obvious
way to undo that did nothing.

Now nothing auto-opens either panel and `?` negates `hidden` in both. The `×`,
the click-to-paste examples and the Datasets dead-click guard are unchanged.
`test_web.py` had pinned the *old* spec (asserting the sessionStorage flag was
present), so that test is **updated**, not worked around.

Pinned by `tests/test_search_help.py` + `tests/search_help_selfcheck.cjs`,
verified to fail on the pre-fix revision.

### Item 1 — say when the folder is standalone

> *"SM works as project centric now. But sometimes a user wants to modify by
> just opening an arbitrary state file, and also wants to handle the FULL state
> file in SM. I think it's enough to just indicate in SM that a user is now
> modifying not a project but a specific folder."*

Editing an unaffiliated folder always worked (docs/63: standalone loads keep
working, "displayed as-is"). The gap was that SM said nothing:
`_qualibrate_tray_badge` returned `None` whenever there was neither an active
project nor a derived scope, so an empty slot meant **both** "you are editing a
bare folder" and "this badge does not apply yet".

Boundaries, each pinned: gated on a qualibrate config existing (without one
"project" is not a concept this user has, so that case stays byte-identical);
archives excluded (the status badge already names them); it may sit **beside**
the ⚗ badge, because qualibrate having an active project while the open chip
belongs to none is exactly the case worth naming — and it explains that badge's
amber; and it is **not a warning**, so the warn/danger colours keep their
docs/55 meanings. Its own CSS class, never the sidebar tree's
`.status-standalone` (that belongs to the run-status family).

Pinned by `tests/test_project_scope.py::TestStandaloneBadge`.

### Item 7 — the FSP compensation amplitudes are editable

> *"When you change FSP the option to change the related amps comes up
> together — that itself is really good. The problem is the amps can't be
> modified by the user at all: it's accept, or discard and update only the FSP.
> Users want to be able to modify the amps a little and then update."*

Each compensated amplitude is now an input. The change lands in **one place**:
all five call sites (Explorer, both grids, All-values, and the plot-apply
popup's per-row and Apply-All paths) build their `comp` resend through
`window._fspCompUpdates`, so teaching that helper to read an override covers
every one of them with no edits to any resend site.

`a.new` keeps the computed value forever and the override rides alongside as
`a.userNew` — that is what gives the per-row reset a target, and what makes an
un-edited plan serialise byte-identically (pinned).

The consequences of an editable field, each handled: the **DAC clip warning is
recomputed from the live inputs** (it is a claim about what will be *written*,
not about the proposal that arrived with the 409), as are the per-row mark and
the docs/76 Δ; a **non-numeric cell disables the apply** rather than silently
falling back; **blank means "use the computed value"**; and departing from
`P = FSP + 20·log10|amp|` is **stated out loud**, since that identity is the
whole point of the compensation.

The server is untouched — `fsp_ack=comp` has always accepted whatever
amplitudes the batch carries and never re-derived them.

Pinned by `tests/fsp_edit_selfcheck.cjs` + `TestEditableAmpsWiring`, verified
to fail on the pre-change `app.js`.

---

## The dormant-pin outage (found while setting this campaign up)

Not a customer item, but the campaign depends on it and the user directed it be
fixed first.

`jsdom` was **not installed on this machine at all**, so all 59 `.cjs`
selfchecks were taking their driver's `pytest.skip("jsdom not installed")`
branch. Roughly fifteen DOM-level pins had been reporting green without ever
executing. Installing jsdom turned four of them red immediately — and they fail
identically against the pre-campaign `app.js`, so they were genuinely broken,
not broken by this work.

One root cause, shared by all four. The harnesses run the scripts with
`window.eval` and hand Node **individual** globals (`global.window`,
`global.document`, …) rather than using jsdom's own script realm. `CSS` was
never on that list, so:

```
typeof window.CSS         -> "object"
window.eval("typeof CSS") -> "undefined"
```

which makes the shipped idiom `(window.CSS && CSS.escape) ? CSS.escape(s) : s`
**throw ReferenceError instead of taking either branch** — the guard tests
`window.CSS` but calls the bare global. In a browser both exist, so the product
code is correct; only the harness made them disagree.

The damage was invisible because of *where* it landed. In
`LiveEditUndo._input` the throw was swallowed by `try { … } catch { return
null; }`, so every cell lookup silently missed, every restore "found nothing to
do", and `tryUndo()` returned false with no error anywhere — two assertions
failing for a reason no assertion could name. In `dataset-virtual.js`'s
`_kbHighlight` it threw outward and took the first j/k keypress with it.

Fixed in the **harness**, not the product: bridge `CSS` like every other
global. Recovered and now executing: `state_sync` 46 assertions, `ctrlz` 28,
`undo_nav` 20, `ds_flow` 17.

A `package.json` now declares `jsdom` + `canvas` so this cannot recur silently,
and CLAUDE.md records both the skip-vs-fail trap and the harness convention:
**a guard that tests `window.X` while calling bare `X` throws instead of
degrading, so a new harness must bridge every global its code reads bare.**

---

## Wave 2 — shipped

### Item 4 — quick-filter chips where the readability controls were

> *"The most common workflow is going to the search box and TYPING x180, amp,
> ro, power ... it's very repetitive and eats time. Move Aa/S-M-L into the
> Settings menu under a Live Edit section, and in that exact spot put the main
> parameters as clickable patches — multi-select of course. And the patches
> must cover ALL the major column items."*

Chips are a **view of the query string**, never a second filter. Toggling one
rewrites `#bulk-search` and lets the existing search do the work, which buys
three things for nothing: one chip filters **both** grids (they read the same
input), typing a chip's word by hand lights that chip, and deleting it unlights
it. A parallel filter could disagree with the box; this cannot. The AND/OR
toggle is therefore string assembly — `' '` or `' | '`, the docs/96 grammar the
search already parses — and not one line of new matching logic.

Per the user: the toggle is leftmost and prominent, **AND** is the default, and
zero matches offers OR as a **one-click** switch, so accepting costs exactly
what finding the toggle would.

The chip set is derived server-side (`_bulk_filter_chips`) against the chip's
real columns — curated short keywords, then a coverage sweep so no band is
unreachable — and a term that would match nothing is never offered. On the real
20-qubit chip: **22 chips over 314 columns in 22 sections.**

Two derivation details, both pinned:

- the term is a section's **first word**. The search splits on whitespace, so a
  two-word term would silently become an AND of two tokens — and taking the
  first word usefully collapses siblings: one `cz` chip covers CZ Unipolar /
  CZ Flattop / CZ Bipolar.
- coverage is tested **prefix-wise per word**, not by substring. Substring
  looked right and quietly dropped every CZ band on the real chip: the `Z+`
  section contributes the term `z`, and `"z"` is a substring of `"cz"`, so the
  gates read as already covered and got no chip at all.

`section` joined the client haystack (`_colHay`) — it is the band name already
printed above the column, and it is what lets one `xy` chip span the four
XY-ish sections a real chip has. Note this **aligns** the grids rather than
inventing something: the pair grid always included section; the qubit grid was
the outlier.

The readability cluster moved to Settings ▸ Live Edit as two labelled rows
(Table / Flat) keeping their independent localStorage keys — a pure relocation.
`test_readability_controls_present` was **re-targeted** at the full page rather
than weakened, plus a new sibling pinning that the cluster is gone from the
grid's working row.

### Item 10 — the working-state version, one click from every page

> *"Move the bookmark button below Calculator, and in its place show the current
> state working version id. Since we're adding Auto-Sync, revert back and forth
> has to be really free. Clicking it lists the version history with when each
> was updated, checkboxes to pick several → show just the combined diff → and
> let a chosen state be applied to the live chip."*

Deliberately thin: every action delegates to machinery that already exists and
is already gated. Two ticks open the docs/84 diff workbench, three or more open
the Compare hub basket, and *Go back* posts the same
`/state-history/<ts>/restore-live` **with both independent force gates**
(unsaved edits, wiring topology) — not weakened for being one click closer.
What this adds is reachability from every page instead of a navigation.

The version id is the snapshot **timestamp**, and which one is "now" is
**content-matched** (`snapshot_ts_for_current_content`) — never "the newest",
because after an A→B→A cycle the newest snapshot holds the wrong content (the
audit-r10 finding that helper exists for), and never `store.mutation_seq`,
which resets on reload/eviction/restart. Both pinned.

**Lazy by construction**: resolving the version hashes the live state+wiring
pair, and docs/28 forbids a live read on a surface that renders on every page —
so the chip rides its own endpoint fetched after paint, like the diagnostics and
instances slots beside it. The panel lives **outside** the swap target, because
an `innerHTML` swap on the slot would delete it on every refresh.

Three honest states: a matching snapshot names it; no match says *unsaved* (the
ordinary mid-edit condition, muted rather than warned); no chip open renders
nothing. A chip with no history **still** gets the affordance — the panel's
empty state is what explains where versions come from.

---

## The measured baseline (env `cqt`, wave 2)

`39 failed, 5,648 passed, 247 skipped`, accounted for completely:

| group | n | status |
|---|---|---|
| documented environmental (CLAUDE.md list) | ~15 | expected |
| autofit/synth, two families + flux cube | ~22 | **pre-existing**, verified on a clean pre-campaign commit |
| load-induced timing flakes | 2 | **pass in isolation**; Windows mtime family |

**Zero regressions across waves 1–2.** The four selfchecks repaired above
(`ctrlz`, `ds_flow`, `state_roundtrip`, `undo_nav`) are absent from this list,
confirming the fix; all **59 selfchecks pass, 0 skipped**.

---

## Wave 3 — shipped

### Item 11 — one chip map, with the pair roles on it

> *"On Chip Status → Topology the qubit layout appears **twice**… users are
> confused — why does the first one exist? — but they **prefer** the first one.
> Put the pair design into the first layout, make it fill the screen more, and
> bring the second layout's information across."*

They were never two designs. When the hero shipped, the pre-existing card
diagram was left underneath it — docs/92 says so verbatim, *"cards below
untouched"* — so this was an unfinished migration reported as a UX complaint.

The cards are deleted. The hero took their space (`CELL` 96 → 132, and it is
now **responsive** through its own viewBox rather than a fixed pixel size that
scrolled sideways), their frequency chevrons, and their control/target
information.

**The trap, and why this was not a simple deletion.** `_sharedQubitPopup` is
what the hero's hover handler opens, and it was only ever *assigned* inside
`buildTopology` — the IIFE that drew the cards. Deleting that block wholesale
would have left the bridge null forever, and `bindHover` merely guards on it:

```js
if (!_sharedQubitPopup) return;
```

so the qubit detail popup — `SECONDARY_PROPS`, the gate-fidelity recency line,
the lazy sparkline fetch — would have stopped opening with **no error, no
console warning and no failing test**. It lives in its own `buildQubitPopup`
now, and `tests/hero_popup_selfcheck.cjs` exists purely to prove a hover still
opens it.

**Roles, to the customer's convention**: the arrow direction encodes the f₀₁
inequality, and C / T / M are small circles beside each qubit at the end of the
arrow they belong to. `moving_qubit` is a **role** (`"control"`/`"target"`), so
M always coincides with C or T and is never a third position; a chip recording
none gets **no M** rather than one inferred from the frequencies, because
quam_builder defaults the mover to the higher-f₀₁ qubit and a guess would hide
exactly the override worth seeing. Verified on the real chip: 30 chevrons,
C/T/M on all 30 pairs, **M at the declared end for every one**.

The drawing is **shared** (`TopoGraph.pairGlyphs`), so the hero and the
component-page maps cannot drift into two conventions — which is what the
customer asked for. The chevron half is lifted verbatim and the component map's
output is byte-identical.

Three more things the deletion would have silently taken, each caught:

- the **"changed vs live" highlight** queried `[data-qubit]`, a *card*
  attribute, so it would have matched nothing on the page;
- the **"Edge labels" checkbox** controlled the cards' text labels — removed
  rather than left as a control that does nothing;
- my own **M geometry**: offset along the edge, it overshot the midpoint on a
  one-cell pair and landed nearer the *other* qubit, the exact opposite of its
  meaning. It stacks perpendicular now.

Markers carry `data-cm-pair` **and** `data-cm-at`, because a qubit is the
control of one pair and the target of another on any chain — "the C nearest
qA1" is an ambiguous question, and the first version of the test asked it and
got the wrong answer.

### Items 5 + 9 — Trends: every qubit on one plot per metric

> *"To see T1/RB/T2 trends today you go to Param History, but that shows
> **per-qubit** trends, not an **integrated** one. Add a Trends tab under Chip
> Status where **all qubits' T1 appear in a SINGLE plot**."*

One chart per metric, one line per qubit, legend = the qubit ids. Verified on
the real chip: a single `f_01` chart carrying **20 qubit lines in 37 ms**.

Most of it already existed. `extract_property_history` always returned every
qubit for a metric in one call — `/api/topology/sparklines` just happened to
pass one — and `_trend_chart.html` already rendered exactly this shape, using
the colorway `app.js` documents as being for *"each qubit's line"*. What was
missing was the surface.

**Any numeric parameter**, per the overhead investigation above. A qubit-scoped
path fans out across the chip, because charting one qubit's copy of a parameter
is not what this page is for.

Honest three ways: a selected metric with nothing recorded still gets a slot
that **says so**; only numeric points become line points, so a null never
becomes a 0; and it is **lazy**, because docs/28's rule against paying for what
the render does not need applies equally to a section a user may never scroll
to.

Registered in all **seven** places a Chip Status section must be declared at
once, and pinned — missing one leaves a tab that exists but never builds.

### Item 8 — Auto-Sync: the covenant amended for the pull direction

> *"Many mature users run VS Code with auto-save on… SM's original design
> concept was: never pull/push the source of truth without the user's
> permission. It's time to drop that. What users want is auto pull/push **when
> they allow it**."*

docs/107 stated the covenant; docs/117 amended its **scope** so one press could
authorize a session of *writes*. This amends the other direction for the first
time — replacing the working copy *from* live.

The line the user drew, and the whole feature:

| pull | replace | local edits | behaviour |
|---|---|---|---|
| on | on | either | live wins, **silently** — the tick *is* the consent |
| on | off | no | pull silently (today's `RECONCILE_SYNCED`) |
| on | off | **yes** | **do not pull**; the drift banner asks |
| off | — | — | byte-identical to today, **and that row is a test** |

The third row is docs/87 intact: with replace unticked, SM still refuses to
choose between the user's work and the chip.

The **policy lives entirely in `POST /auto-sync/pull`**; the client only
notices `auto_pull` on the poll and presses the button, so the rule cannot be
half-implemented in two places. **No new poller** (docs/110) — the signal rides
`/state/drift`, carried on *every* branch including the untracked ones, since a
chip with no drift baseline can still have diverged. The pull takes
`window._applyInFlight`, the same latch the manual Apply and the push flusher
use.

**Arm-ability is split per direction**, which is the subtle part: the push gate
refuses on `live_diverged` and on a read-only live folder. Both are wrong for
pull — divergence is the very condition that makes pulling useful, and a pull
only reads live while writing the working copy.

One session dict holds all three flags, and `_auto_apply_state` returns it only
when `push` is on, so every existing push path is unchanged **by construction**
rather than by test. A pull-only session must never make the client start
writing; that has its own pin. Sessions are per-chip and never persisted —
arming chip A cannot authorize a write to chip B.


---

## The heavy review — findings and what they changed

The user asked for a red-team pass at every phase and, at the end, one over
the whole campaign in the customer's own role, weighted to *speed, performance
and stability*. Three reviewers ran in parallel. The HIGH/MEDIUM findings were
fixed in `ada1e1b`; this section records the tail that outlived it, because
each is a case of the surface **saying something that is not so** — nothing
crashed, no test failed, and that is exactly why they needed pins.

### 1 — `x180` was the one term the chip row could not offer

The customer's sentence was *"go to the search box and TYPE **x180**, amp, ro,
power"*. `amp` and `power` are curated keywords and `readout` is a section, but
`x180` is neither: it is an **operation**, and the row was built from curated
words plus SECTION names only. So the feature answered every part of the
complaint except the word they led with.

Both grids already render operations — `_build_bulk_cell` labels an operation
leaf `op · x180_DragCosine · amplitude` (U+00B7) and its alias column
`op · x180` — so the names were on screen the whole time. `_bulk_op_names`
harvests them from the labels, on the qubit grid and the pair grid alike.

Two details decided by real data rather than taste:

- **The term is the name's first underscore segment.** That is what collapses
  `x180` + `x180_DragCosine` into the one chip a user means by "x180", and the
  short term stays a substring of the long spelling, so it still reaches every
  leaf of it.
- **A leading sign is stripped.** The real chip carries `-x90` and `-y90`
  (negative-amplitude aliases). `-` opens a **negated** term in the docs/96
  grammar, so a chip labelled `-x90` would have filtered to everything *except*
  x90 — the exact opposite of its own label. Stripped, it collapses into the
  `x90` chip, which reaches those columns anyway.

Order is now the customer's own sentence: operations (*which pulse*), then the
property words (*which value*), then any band neither reached. On the real
20-qubit chip the row is `x90 y90 saturation x180 y180 const cz` + 13 keywords
+ 3 bands, and the `cz` operation chip now covers the three CZ bands the sweep
used to name separately — the same term either way, so the coverage promise
holds. Ops wear `bulk-chip-op` (monospace, dashed) because an operation is a
name from *your chip*, not a property word; the class is visual only, which is
what `chipbar_selfcheck.cjs` block I exists to prove.

### 2 — the Trends x axis was the snapshot sequence, not time

433 snapshots on a `type: 'category'` axis are spaced **evenly**, so three
quiet weeks and two minutes of frantic retuning are drawn the same distance
apart — on the one page whose question is *when did this drift*. The raw ids
(`20260816_012907_4661`) are also unreadable as tick labels at that count.

The gaps are the information here, so the axis is time: `_snap_iso` parses the
id to an instant, `_trend_points` ships `(id, value, iso)` so the **snapshot id
survives into the hover** — that is what a user carries over to State History —
and `_axisFor` picks `date` only when *every* point parsed, falling back to
`category` wholesale otherwise, because mixing them would place the unparsed
points at epoch zero. An id that does not parse returns `None` rather than a
guess.

Param History's own `_trend_chart.html` keeps its category axis and that is not
an inconsistency: it plots a handful of *hand-picked* snapshots, which are
chosen items, not a timeline.

### 3 — the version panel showed 40 of 433 and stopped

No footer, no paging: the list silently claimed to *be* the history. It now
pages — **Show 40 more** and **All** — bounded by `_STATE_VERSIONS_CAP = 500`
(one fetch stays one fetch; ~90 KB), and past the cap it says so and names
State History. `limit` is user input on a route that renders every row it is
given, so the clamp has its own pin.

`StateVersions.more()` carries the **ticked rows across the fetch** and
re-applies them by value. Comparing an old version against a recent one is the
motivating case, and it must not mean starting the selection over just because
the old one was on page 3.

### 4 — the chip called the *live* version the *working* one

`_state_version_now` hashes `ctx["path"]` — the **live** pair. The template
called itself "the working-state version", and with unapplied edits in SM those
are different states, so a bare id read as *"your work is recorded as this"*.
It is not.

The id is right and stays; what was missing is whose it is. `ver.dirty` now
marks it (`+`, warning-tinted) and the tooltip says plainly that SM holds edits
not in it yet. Two smaller honesty edits went with it: the unmatched label
`unsaved` → **`unrecorded`** (an out-of-band write reaches that state as
readily as an edit does, so it must not name a culprit it did not observe), and
the file's own header comment, which was the source of the confusion.

### Findings recorded and NOT taken

| # | finding | why it waits |
|---|---|---|
| 5 | Trends metric selection is not persisted across a reload | a preference, not a lie; no data is misrepresented |
| 6 | per-pair CZ fidelity became hover-only when the card diagram went | the hero map's own metric selector still prints it per node; the cards' loss is disclosed in the item 11 record |
| 7 | dead tunables + ~44 card-only CSS rules survive the deletion | inert; removing them is a separate sweep with its own regression surface |
| 8 | `/auto-apply/gate` reports `armed:false` for a pull-only session | correct as written — that route answers "may I write?", and a pull-only session may not. Renaming it would touch every push caller |

---

## The three audits (speed · red-team · customer roles)

Run in parallel over the whole campaign at the user's instruction, weighted to
**speed and stability**, with the customer-role pass driving the real 20-qubit
CQT chip as four different people. Everything below was reproduced before a
line was changed.

### Data loss — three, all confirmed

1. **Auto-Sync's DOM-dirt detector could never fire.** It queried
   `.bulk-cell.bulk-dirty`; the grid sets **`dirty`**, and `bulk-dirty` exists
   only as the id of the *counter* span. The fallback called
   `BulkEdit.hasUnsaved`, which does not exist anywhere in the repo. So
   `dom_dirty` was never sent, and a filled-down column was destroyed by a pull
   with **replace unchecked** — the one row of the policy table that exists to
   ask. Nothing failed: the pytest pin posts `dom_dirty=1` **by hand**, so it
   proved the server honours the flag and never that the client raises it.
   `tests/dom_dirty_selfcheck.cjs` now closes both ends.

2. **The pull re-checked before its I/O, not after.** The window the code's own
   comment describes is spent *inside* `sync_from_live`, which holds no
   `store._lock` — so `/field/edit` could land there, return 200, appear in the
   tray, and be destroyed by the reload. Now re-checked after the I/O under
   `store._lock`, resolving exactly as `_reconcile_cached_quam_ctx` does.

3. **A failed pull left the stale store ACTIVE.** Popping from `_quam_cache`
   does not deactivate a context — `app.config["contexts"]` holds the same dict
   and that is what `_active_ctx()` reads. A later save+apply then wrote the old
   content back over the chip, **unforced**, because the sync point already
   matched live. The failure path now makes disk == memory instead of hoping
   the context is re-read.

### Speed — measured, four fixed

| surface | before | after |
|---|---|---|
| `/topology/trends` per point | 61 B (the instant shipped alongside the id it is derived from) | 38 B — **−38 %** |
| `/state/version` | 4.1 ms, a **526 KB live read + sha256 on every page load and every apply** | **0.49 ms**, stat-gated on `(mtime, size)` |
| one quick-filter chip press | **two** full scans of a 4,480-cell table | one |
| `_STATE_VERSIONS_CAP` | comment claimed "500 rows ≈ 90 KB" | measured **1.35 KB/row**; cap 500 → 150 |

The hot paths did not regress: `/bulk` 152 ms vs 148 ms on main (+0.51 % payload),
`_build_bulk_cell` **faster** (20.5 vs 29.0 ms over 2,600 aliases), no new
poller or observer, and `chip-status.js` is a net −214 lines.

### Honesty — four false statements, all removed

- `/help` and the landing glossary still asserted the **original** covenant
  ("edits here *never* touch the instrument", "*only ever* written by an
  explicit Apply to live") and never mentioned Auto-Sync. README had been
  updated; the two screens a newcomer actually reads had not.
- The versions panel promised "one is captured on every **save** and apply" —
  46 save cycles produced **zero** versions, and `/save`'s own comment says
  history is snapshotted on apply, not there.
- The Review tray rendered *"Auto-apply is ON: … writes it straight to the live
  chip"* and *"Durable draft — the live chip stays untouched"* **in the same
  panel**.
- `↩ Go back` wrote the live chip in one click from any page as a neutral
  `outline` button — the most consequential affordance in the panel rendered as
  the least distinguishable one. It now wears the ONE overwrite-live language
  docs/86/97 established (error-tinted, names the act); both independent force
  gates are untouched.

### Recorded, not taken

Trends mixes the dense curated tier and the change-point leaf tier with no
marker (a real parameter shows 20×4 points curated vs 17 single-point series
typed); the QDAC-biased qubits render 99 editable `not set` boxes for a `z` that
is a `QdacBiasLine`; the leaf typeahead returns `[]` until a 13 s index rebuild
that only the *chart* query triggers; and `run_selfchecks.cjs` prints
`(0 assertions)` for pins that do run — indistinguishable from the dormant state
docs/120 was written about. Each is real; none is a false statement or a data
loss, and each is a separate piece of work.

---

## Round 3 — the MEDIUM/LOW batch (2026-08-17)

Same rule as before: reproduce in a real browser against the real 20-qubit
customer chip, in the customer's own env, before touching a line. The
cluster agents from round 2 handed over root causes; **three of those root
causes were wrong**, and the profiler said so each time. That is recorded here
because the pattern is the lesson, not the individual items.

### What the profiler overturned

| reported cause | measured cause |
|---|---|
| `_virtInit()`'s layout reads block the mount for 2.5 s | those reads cost **0.6 ms** for all 158 headers. The cost is the write→read ALTERNATION, and it moved from `_updateTopScroll` (397 ms) to `_updateStickyOffset` (369 ms) to pair-edit's twin (579 ms) as each was fixed |
| `_refreshGlobal()` scans 3,160 nodes twice, 10–20 ms per keystroke | **0.9 ms** across ten keystrokes. The real cost was `_positionCellBtn` at **70.4 ms** — two `getComputedStyle` calls and a forced layout of a 158-column table, per key |
| the sticky top bar swallows clicks on Auto-allocate | `elementFromPoint` says nothing occludes it at step 1 in a 1600×1000 window. But `--topbar-height` **is** a lie — 48px declared vs 201/229/254 measured — which is the real, general defect behind "the failure message is below the fold" |

Reads in a row share one layout; it is the interleaving that costs. A function
is not guilty because it is on the hot path — it is guilty when the profile says
so.

### The measurements that decided things

Live State Edit mount, time-to-responsive on the real chip, three runs each,
median:

| config | TTR | blocked | worst stall |
|---|---|---|---|
| before | 3,322 ms | 2,461 ms | 1,368 ms |
| geometry batching only | 3,178 | 2,440 | **1,969** |
| virtualization only | 2,758 | 2,003 | 1,332 |
| both (shipped) | **2,693** | **1,919** | **1,120** |

Batching alone is not a win — it concentrates the same layout into one longer
frame. It earns its place only on top of virtualization, and that is why the
table is here rather than a single "6.5× faster mount" number (the mount
FUNCTION did go 1,003 → 154 ms; the user does not feel a function).

The virtualization gate was `cells >= 4000` and this grid has 3,160 — 840 under
a threshold that is a proxy for the thing it cares about. It now gates on how
many cells would actually go **cold**, which is already computed and cannot be
wrong about a wide-but-short or narrow-but-tall chip.

### The findings with a consequence

- **A window applied an edit its user never saw.** Two tabs share one server
  context and one change log; a tray only refreshes on its own actions. Tab B,
  opened first and left alone, showed `data-change-count="0"` and "● Synced"
  while tab A typed — and tab B's Apply answered `{"replay":{"applied":1}}`.
  Both live-write doors now refuse when the server holds MORE changes than the
  presser's screen showed, name the paths, and offer one click to accept.
  Count-based, not seq-based; absent parameter ⇒ byte-identical; and `force=1`
  does **not** double as the acknowledgement (it answers the staleness
  question — one token never collapses two gates).
- **Every `hx-on::after-request` in the app was dead.** The app's own CSP omits
  'unsafe-eval'; htmx compiles those attributes with `new Function`. Seven
  handlers, including `LiveEditUndo.clear()` after an apply. One had already
  been patched blind earlier in this campaign ("the Auto-Sync popup does not
  close after Save") — this was why. Replaced by `data-after-request` + a
  delegated dispatch table.
- **Auto-Sync's auto-pull could not fire while you stayed on a page** —
  `live_diverged` was refreshed only by a full render and by
  `/api/topology-mtime`, which only Chip Status polls. Measured at 9 polls over
  90 s answering `auto_pull:false` while the drift count beside it said 481.
- **A dangling pointer locked its field**: refusing plain text is right,
  refusing `null` too left the user unable to repair or remove a broken
  reference.
- **One aliased sibling disabled the sibling-type guard chip-wide** — a pointer
  counted as `other` and `other == 0` is the unanimity gate. Aliasing is
  deferral, not evidence.
- **The guard could never fire on `/field/create`** at all: `get_value` raises on
  an absent path and that was read as "no opinion". Absent carries no type for
  the same reason null doesn't.
- **The pair inspector counted keys instead of naming the qubit** (`[19 items]`)
  — the second half of the report that opened this campaign.
- **Searching a value hid exactly the rows you would type it into** — the
  customer's own words. The search keeps its meaning; the way out is offered.

### A regression I shipped, and what caught it

Lowering the virtualization gate exposed a latent bug: a column found by search
was **cold**, so its cells were empty and un-editable — the same shape as the
original report. Hydration fired on scroll, nav and path repaint, never on
search. Caught by the **roles sweep**, not by any pin: the calibration
scientist's script simply could not find a cell to type in.

### What did NOT reproduce

Recorded so nobody re-derives them: the no-data stone's qubit name measures
**8.08:1** against the page background, not the reported 2.45:1; no wizard
button is occluded at step 1; and the 8-colour colorway cannot be judged on a
chip with no coherence data, which never draws a 20-trace set.

### Sweeps

**Buttons** — 1,003 buttons pressed across 12 pages. The 25 "no-op" hits are
class-only toggles (sidebar, Settings popover) that the probe cannot see; the
one real finding was the CSP violation present on every page. **Roles** — four
customer roles each complete their task end-to-end with **0 console errors**.
